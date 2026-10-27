"""Generate the Visitor Pass approval workflow from the Visitor Type masters.

Why generate it
---------------
Frappe's workflow engine needs a *static* role on each transition — it cannot
resolve `allowed` at runtime. The app used to ship a fixed workflow with five
hardcoded lanes (System Manager / Sales Manager / HR Manager / HOD / CEO), so a
Visitor Type whose `approver_role` was anything else had no `Draft -> Pending X`
transition at all: the pass could be created but never submitted for approval.

Instead of hardcoding lanes we rebuild the workflow from the Visitor Type table
whenever a Visitor Type changes. Frappe keeps its static transitions, and admins
get a genuinely data-driven approval chain: add a Visitor Type with any approver
role and the lane, the states and both transitions appear automatically.

Shape of the generated workflow
-------------------------------
    Draft ──Submit──▶ Pending <primary role>          (one per distinct role; Security: own walk-ins)
    Pending <primary> ──Approve──▶ Pending <secondary>  (types with 2 approvers)
    Pending <role>    ──Approve──▶ Approved             (when <role> is the last)
    Pending <role>    ──Reject───▶ Rejected
    Rejected          ──Reapply──▶ Draft
    Draft / Pending   ──Withdraw─▶ Withdrawn          (the host or whoever raised it)
    Approved          ──Cancel───▶ Cancelled          (approvers; host/owner before check-in)

Who may act in a lane
---------------------
A lane belongs to its role, unless the pass names its approvers. With VMS
Settings -> "Send approvals to the host's own manager" the first lane goes to
the host's `reports_to` alone (`routed_approver`), and an approver on leave can
hand their approvals to a stand-in (VMS Approval Delegation). Both arrive as
`current_approvers` on the pass, computed by the server on every save
(approval_tasks.resolve_approvers). The lane role's own transitions honour a
routed pass, and a second set of transitions, held by ASSIGNED_APPROVER_ROLE,
lets a named approver act without holding the lane role. The workflow can only
read what is stored, so the server checks the live answer again on the action
itself (VisitorPass._assert_decided_by_current_approver).

Conditions are evaluated against the *live* Visitor Type row. A Visitor Type
refuses to be re-pointed at a different approver, or switched off, while passes
of it are awaiting approval (VisitorType._refuse_stranding_pending_passes), and
a type switched off regardless keeps its lanes until those passes are decided
(`_visitor_type_rows`) — otherwise they could be neither approved nor rejected.
"""

import frappe

WORKFLOW_NAME = "Visitor Pass Approval"
DOCTYPE = "Visitor Pass"
STATE_FIELD = "workflow_state"

DRAFT = "Draft"
APPROVED = "Approved"
REJECTED = "Rejected"
CANCELLED = "Cancelled"
# Called off by the host or whoever raised the pass before it was decided. A
# final state at docstatus 0: there is nothing to cancel, and nothing comes back.
WITHDRAWN = "Withdrawn"

# Canonical tuple form for callers that gate on "is this pass genuinely
# approved" (visitor_gate.py's check-in/check-out guard, visitor_pass.py's
# badge issuance). Both used to redefine `("Approved",)` locally -- one of
# them with a comment claiming the duplication was needed "so this module has
# no import cycle", which was false: visitor_pass.py already imports names
# from this module at the top of the file. Import APPROVED_STATES from here
# instead of repeating the literal, so a future state-name change can't leave
# one of the copies silently guarding the old value.
APPROVED_STATES = (APPROVED,)

ACTION_SUBMIT = "Submit"
ACTION_APPROVE = "Approve"
ACTION_REJECT = "Reject"
ACTION_REAPPLY = "Reapply"
ACTION_CANCEL = "Cancel"
ACTION_WITHDRAW = "Withdraw"

# The role a plain host/reception user has when raising a pass.
REQUESTOR_ROLE = "Employee"
# Reception raises passes for any host, and often has no Employee record — and
# ERPNext takes the Employee role away from a user with none. So the front desk
# gets the requestor's transitions in its own right, on the passes it raised.
FRONT_DESK_ROLE = "Front Office Executive"
REQUESTOR_ROLES = (REQUESTOR_ROLE, FRONT_DESK_ROLE)

# The gate registers a visitor who arrives without a pass (walk_in.py), and a
# guard is often a contract worker with no Employee record, so no Employee role.
# Security edits only the passes it raised (permissions.py) and sends for
# approval only walk-ins it raised; deciding them stays with the lanes below.
GATE_ROLE = "Security"
# request_channel of a pass raised by the New Walk-In form. Only that form sets
# it (VisitorPass._keep_request_channel), so the condition below can trust it.
WALK_IN_CHANNEL = "Walk-In"

# Held by the people a pass can name as its approvers — the host's manager, and
# stand-ins for an approver on leave — so they can act without holding the lane
# role. The role on its own opens nothing: every transition it carries, and
# every row permissions.py lets it see, requires the user to be named on that
# pass. approval_tasks gives it out when a user is named and takes it back once
# they no longer are.
ASSIGNED_APPROVER_ROLE = "Assigned Visitor Approver"

# Cancelling an Approved pass is the approval authority exercised in reverse, so
# the Cancel transitions are generated for the approver roles themselves (see
# `build_workflow`). Security is deliberately not among them: it raises walk-ins
# but decides nothing — the gate acts through Security Log — and offering it an
# action it cannot execute produces a button that only ever errors.
CANCEL_ROLE = None

STATE_STYLES = {
	DRAFT: "Warning",
	APPROVED: "Success",
	REJECTED: "Danger",
	CANCELLED: "Danger",
	WITHDRAWN: "Inverse",
}
PENDING_STYLE = "Warning"


def lane_for_role(role: str) -> str:
	"""The pending-state name that belongs to an approver role."""
	return f"Pending {role}"


def _visitor_type_rows():
	"""The Visitor Types whose approval lanes must exist, in name order.

	Every active type, and any inactive type that still has passes waiting in a
	pending lane. Dropping such a type removed its "Pending <role>" state and all
	its transitions, so its pending passes sat in a state the workflow no longer
	knew: nobody could approve, reject or withdraw them. Deactivating a type is
	about new passes; the ones already in flight are still owed a decision.
	"""
	rows = frappe.get_all(
		"Visitor Type",
		fields=["name", "is_active", "approver_role", "secondary_approver_role"],
		order_by="name asc",
	)
	inactive = [row.name for row in rows if not row.is_active]
	in_flight = set(types_with_pending_passes(inactive)) if inactive else set()
	return [row for row in rows if row.is_active or row.name in in_flight]


def types_with_pending_passes(visitor_types):
	"""Which of `visitor_types` have a pass in a pending approval lane."""
	return frappe.get_all(
		DOCTYPE,
		filters={
			"visitor_type": ("in", list(visitor_types)),
			"docstatus": 0,
			STATE_FIELD: ("like", lane_for_role("%")),
		},
		distinct=True,
		pluck="visitor_type",
		order_by=None,
	)


def approver_roles() -> list[str]:
	"""Every distinct role that appears as a primary or secondary approver."""
	roles = []
	for row in _visitor_type_rows():
		for role in (row.approver_role, row.secondary_approver_role):
			if role and role not in roles:
				roles.append(role)
	return roles


def pending_lanes() -> set[str]:
	"""All pending-state names the current Visitor Type configuration can produce."""
	return {lane_for_role(role) for role in approver_roles()}


@frappe.whitelist()
def get_visitor_type_approvers() -> dict:
	"""Visitor Type name -> its primary approver role, for the Desk client.

	visitor_pass.js used to hardcode this vocabulary as a fixed 5-entry map
	(Contractor/Supplier/Customer/Candidate/VIP) instead of reading it from
	here. A Visitor Type routed to any other approver role -- e.g. "Auditor"
	pointed at Facility Manager -- rendered with no approver name in the intro
	text, and the same fixed list in the Pending Web Submissions dialog filter
	meant passes sitting in that lane never appeared in the dialog at all, with
	no error anywhere. This is a read-only mirror of the same `_visitor_type_rows()`
	`build_workflow` uses, so the client's vocabulary can never drift from the
	workflow this module actually generates -- a new Visitor Type with a new
	approver role needs no client-side change to be understood correctly.
	"""
	frappe.has_permission("Visitor Pass", "read", throw=True)
	return {row.name: row.approver_role for row in _visitor_type_rows() if row.approver_role}


# ─────────────────────────────────────────────────────────
# Dependency records
# ─────────────────────────────────────────────────────────
# Created only when missing and never updated: states, actions and roles are
# shared by name with every other app's workflows. What this app creates is
# recorded, so uninstall can remove it (setup.mark_created).
def _ensure_workflow_state(state: str, style: str):
	if frappe.db.exists("Workflow State", state):
		return
	frappe.get_doc({"doctype": "Workflow State", "workflow_state_name": state, "style": style}).insert(
		ignore_permissions=True
	)
	_mark_created("Workflow State", state)


def _ensure_workflow_action(action: str):
	if frappe.db.exists("Workflow Action Master", action):
		return
	frappe.get_doc({"doctype": "Workflow Action Master", "workflow_action_name": action}).insert(
		ignore_permissions=True
	)
	_mark_created("Workflow Action Master", action)


def _ensure_role(role: str):
	if not frappe.db.exists("Role", role):
		frappe.get_doc({"doctype": "Role", "role_name": role}).insert(ignore_permissions=True)
		_mark_created("Role", role)


def _mark_created(doctype, name):
	from visitormanagement.setup import mark_created

	mark_created(doctype, name)


# ─────────────────────────────────────────────────────────
# Conditions
# ─────────────────────────────────────────────────────────
def _vt(field):
	return f'frappe.db.get_value("Visitor Type", doc.visitor_type, "{field}")'


def _routes_to(role):
	"""This pass's type sends it to `role` first."""
	return f"{_vt('approver_role')} == {role!r}"


def _has_secondary(role, secondary):
	"""This pass's type is primary=`role`, secondary=`secondary`."""
	return f"{_vt('approver_role')} == {role!r} and {_vt('secondary_approver_role')} == {secondary!r}"


def _is_final_approver(role):
	"""`role` is the last approver for this pass's type — approving completes it."""
	return (
		f"({_vt('secondary_approver_role')} == {role!r})"
		f" or (not {_vt('secondary_approver_role')} and {_vt('approver_role')} == {role!r})"
	)


# Frappe's `allow_self_approval = 0` only compares the approver with the document
# OWNER (frappe/model/workflow.py:has_approval_access). The person with a stake
# in a visit is its host, and a pass is routinely raised by somebody else —
# reception, or the visitor through the portal — so an approver hosting the visit
# could approve it himself. A transition condition is evaluated on the server for
# every action and also hides the button, so the rule lives there.
_NOT_THE_HOST = (
	"(not doc.person_to_visit"
	' or frappe.db.get_value("Employee", doc.person_to_visit, "user_id") != frappe.session.user)'
)


def _by_someone_other_than_the_host(condition):
	"""`condition`, and the user approving is not the host of this visit."""
	return f"({condition}) and {_NOT_THE_HOST}"


# Whoever sent the pass for approval decides neither way: Frappe's self-approval
# rule knows only the owner, and a user holding the requester's role and the
# approver's could send someone else's draft and then approve it. The server
# refuses it too (VisitorPass._assert_decided_by_current_approver).
_NOT_THE_SUBMITTER = (
	'(frappe.session.user == "Administrator" or frappe.session.user != doc.submitted_for_approval_by)'
)
_IS_WALK_IN = f"doc.request_channel == {WALK_IN_CHANNEL!r}"


# The approvers this pass names (approval_tasks.resolve_approvers), as stored by
# the server. `frappe.session` is the whole session object in a workflow
# condition, so `.user` is the user taking the action.
_IS_NAMED = 'frappe.session.user in (doc.current_approvers or "").split(",")'
# A pass routed to one person is theirs (and their stand-in's) alone; any other
# pass stays with everyone holding the lane role, exactly as before.
_ROUTING_RESPECTED = f"(not doc.routed_approver or {_IS_NAMED})"

_IS_OWNER = "doc.owner == frappe.session.user"
_IS_HOST_OR_OWNER = (
	f"({_IS_OWNER} or (doc.person_to_visit"
	' and frappe.db.get_value("Employee", doc.person_to_visit, "user_id") == frappe.session.user))'
)
# A visit that has started is not cancelled from the host's side: before_cancel
# refuses it anyway, so the button would only ever error.
_BEFORE_CHECK_IN = "(doc.status in ('Approved', 'Items Verified') and not doc.actual_checkin)"


def _and(*conditions):
	return " and ".join(f"({c})" for c in conditions if c)


# ─────────────────────────────────────────────────────────
# Builder
# ─────────────────────────────────────────────────────────
_FINGERPRINT_KEY = "vms_visitor_pass_workflow_fingerprint"


def _fingerprint(states, transitions):
	import hashlib
	import json

	return hashlib.sha256(json.dumps([states, transitions], sort_keys=True).encode()).hexdigest()


def build_workflow(commit=False):
	"""Rebuild `Visitor Pass Approval` from the current Visitor Type masters.

	Returns the workflow name when it was written, or None when there was nothing
	to write: no active Visitor Type carries an approver role, or the generated
	lanes are exactly what was generated last time.

	That second case is what keeps a hand edit alive. This used to rewrite the
	whole workflow on every migrate and every Visitor Type save, so anything an
	admin changed on it (an extra condition, a renamed action, allow_self_approval)
	vanished on the next deploy. The generated shape is fingerprinted: it is only
	written again when the approval routing itself changes — a Visitor Type's
	approver roles, or this generator's own rules in a new app version — or when
	the workflow is missing. A routing change still replaces the whole workflow,
	because the lanes are derived from the Visitor Types and cannot be merged.
	"""
	rows = [r for r in _visitor_type_rows() if r.approver_role]
	if not rows:
		return None

	roles = approver_roles()

	# --- dependencies -------------------------------------------------
	for action in (
		ACTION_SUBMIT,
		ACTION_APPROVE,
		ACTION_REJECT,
		ACTION_REAPPLY,
		ACTION_CANCEL,
		ACTION_WITHDRAW,
	):
		_ensure_workflow_action(action)
	for state, style in STATE_STYLES.items():
		_ensure_workflow_state(state, style)
	for role in roles:
		_ensure_role(role)
		_ensure_workflow_state(lane_for_role(role), PENDING_STYLE)
	for role in (*REQUESTOR_ROLES, GATE_ROLE, ASSIGNED_APPROVER_ROLE):
		_ensure_role(role)

	# --- states -------------------------------------------------------
	# A state may be listed once per role that edits it: the Desk opens the form
	# for editing to whoever holds any of them (frappe.workflow.is_read_only).
	states = [{"state": DRAFT, "doc_status": "0", "allow_edit": r} for r in (*REQUESTOR_ROLES, GATE_ROLE)]
	for role in roles:
		states.append({"state": lane_for_role(role), "doc_status": "0", "allow_edit": role})
	# The gate moves an approved pass on (Items Verified, Checked-In, Checked-Out)
	# in `status` while the workflow state stays "Approved"; lists show `status`.
	states.append(
		{"state": APPROVED, "doc_status": "1", "allow_edit": "System Manager", "avoid_status_override": 1}
	)
	states.extend({"state": REJECTED, "doc_status": "0", "allow_edit": r} for r in REQUESTOR_ROLES)
	states.append({"state": CANCELLED, "doc_status": "2", "allow_edit": "System Manager"})
	# Read-only for everyone else; VisitorPass refuses any later save of one.
	states.append({"state": WITHDRAWN, "doc_status": "0", "allow_edit": "System Manager"})

	# --- transitions --------------------------------------------------
	transitions = []

	# Draft -> the lane owned by each type's primary approver.
	# Sorted: a set's order changes between processes, and the fingerprint below
	# must be the same for the same routing.
	# The front desk acts on the passes it raised; an employee on any pass they
	# may write (their own, or one they host — permissions.py decides that).
	for role in sorted({r.approver_role for r in rows if r.approver_role}):
		transitions.append(
			{
				"state": DRAFT,
				"action": ACTION_SUBMIT,
				"next_state": lane_for_role(role),
				"allowed": REQUESTOR_ROLE,
				"condition": _routes_to(role),
			}
		)
		transitions.append(
			{
				"state": DRAFT,
				"action": ACTION_SUBMIT,
				"next_state": lane_for_role(role),
				"allowed": FRONT_DESK_ROLE,
				"condition": _and(_routes_to(role), _IS_OWNER),
			}
		)
		# The gate sends the walk-ins it raised, into the same lane as anyone's.
		transitions.append(
			{
				"state": DRAFT,
				"action": ACTION_SUBMIT,
				"next_state": lane_for_role(role),
				"allowed": GATE_ROLE,
				"condition": _and(_routes_to(role), _IS_OWNER, _IS_WALK_IN),
			}
		)

	# Primary -> secondary, for every (primary, secondary) pair in use.
	pairs = {
		(r.approver_role, r.secondary_approver_role)
		for r in rows
		if r.approver_role and r.secondary_approver_role
	}

	# Each decision exists twice: once for the lane role, which also has to be
	# named when the pass is routed to a person, and once for a named approver
	# who does not hold the lane role. Someone holding both sees one button: the
	# Actions menu lists an action once, and both rows lead to the same state.
	def decision(state, action, next_state, condition=None, self_approval=True):
		for allowed, who in ((state_role[state], _ROUTING_RESPECTED), (ASSIGNED_APPROVER_ROLE, _IS_NAMED)):
			row = {
				"state": state,
				"action": action,
				"next_state": next_state,
				"allowed": allowed,
				"condition": _and(condition, who, _NOT_THE_SUBMITTER),
			}
			if not self_approval:
				row["allow_self_approval"] = 0
			transitions.append(row)

	state_role = {lane_for_role(role): role for role in roles}

	for primary, secondary in sorted(pairs):
		# An approver must not also be the requester who raised this pass —
		# see frappe/model/workflow.py:has_approval_access — nor its host
		# (the condition). Submit/Reject/Reapply stay self-approvable:
		# those are the requester's own moves.
		decision(
			lane_for_role(primary),
			ACTION_APPROVE,
			lane_for_role(secondary),
			_by_someone_other_than_the_host(_has_secondary(primary, secondary)),
			self_approval=False,
		)

	# Any lane -> Approved when that role is the final approver, and -> Rejected.
	for role in roles:
		decision(
			lane_for_role(role),
			ACTION_APPROVE,
			APPROVED,
			_by_someone_other_than_the_host(_is_final_approver(role)),
			self_approval=False,
		)
		decision(lane_for_role(role), ACTION_REJECT, REJECTED)

	transitions.append(
		{
			"state": REJECTED,
			"action": ACTION_REAPPLY,
			"next_state": DRAFT,
			"allowed": REQUESTOR_ROLE,
		}
	)
	transitions.append(
		{
			"state": REJECTED,
			"action": ACTION_REAPPLY,
			"next_state": DRAFT,
			"allowed": FRONT_DESK_ROLE,
			"condition": _IS_OWNER,
		}
	)

	# Draft or waiting -> Withdrawn: the visit is off before anyone decided it.
	# Only the people the pass belongs to; an approver says no with Reject.
	for state in (DRAFT, *(lane_for_role(role) for role in roles)):
		for allowed, condition in ((REQUESTOR_ROLE, _IS_HOST_OR_OWNER), (FRONT_DESK_ROLE, _IS_OWNER)):
			transitions.append(
				{
					"state": state,
					"action": ACTION_WITHDRAW,
					"next_state": WITHDRAWN,
					"allowed": allowed,
					"condition": condition,
				}
			)

	# Approved -> Cancelled: a visitor who cancels, or someone barred after
	# approval, must not keep a gate-valid pass forever. Open to every role
	# that can approve a pass — the approver who signed it off is the one who
	# can pull it. `setup.VISITOR_PASS_CANCELLERS` grants those same roles the
	# `cancel` DocPerm, without which this transition would render a button that
	# is refused the moment it is clicked.
	for role in sorted(roles):
		transitions.append(
			{
				"state": APPROVED,
				"action": ACTION_CANCEL,
				"next_state": CANCELLED,
				"allowed": role,
			}
		)
	# The visit's own people may call off an approved visit too, until the
	# visitor arrives. `setup._ensure_permissions` grants these roles `cancel`,
	# and permissions.py allows it only on their own passes.
	for allowed, condition in ((REQUESTOR_ROLE, _IS_HOST_OR_OWNER), (FRONT_DESK_ROLE, _IS_OWNER)):
		transitions.append(
			{
				"state": APPROVED,
				"action": ACTION_CANCEL,
				"next_state": CANCELLED,
				"allowed": allowed,
				"condition": _and(condition, _BEFORE_CHECK_IN),
			}
		)

	# --- persist ------------------------------------------------------
	fingerprint = _fingerprint(states, transitions)
	if frappe.db.exists("Workflow", WORKFLOW_NAME) and frappe.db.get_default(_FINGERPRINT_KEY) == fingerprint:
		return None

	workflow = (
		frappe.get_doc("Workflow", WORKFLOW_NAME)
		if frappe.db.exists("Workflow", WORKFLOW_NAME)
		else frappe.new_doc("Workflow")
	)
	workflow.workflow_name = WORKFLOW_NAME
	workflow.document_type = DOCTYPE
	workflow.workflow_state_field = STATE_FIELD
	workflow.is_active = 1
	workflow.override_status = 0
	workflow.send_email_alert = 0

	workflow.set("states", [])
	for st in states:
		workflow.append("states", st)

	workflow.set("transitions", [])
	for tr in transitions:
		workflow.append("transitions", tr)

	workflow.flags.ignore_permissions = True
	if workflow.is_new():
		workflow.insert(ignore_permissions=True)
	else:
		workflow.save(ignore_permissions=True)
	frappe.db.set_default(_FINGERPRINT_KEY, fingerprint)

	if commit:
		# nosemgrep: frappe-manual-commit - only when the caller asks for commit=True
		frappe.db.commit()

	return workflow.name


def rebuild_on_visitor_type_change(doc=None, method=None):
	"""Doc hook target — keep the workflow in step with the Visitor Type table."""
	try:
		build_workflow()
	except Exception:
		# A workflow rebuild must never block saving a Visitor Type; surface it
		# in the error log so an admin can see why the lane didn't appear.
		frappe.log_error(frappe.get_traceback(), "VMS: Visitor Pass workflow rebuild failed")
