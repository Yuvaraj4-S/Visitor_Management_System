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
    Draft ──Submit──▶ Pending <primary role>          (one per distinct role)
    Pending <primary> ──Approve──▶ Pending <secondary>  (types with 2 approvers)
    Pending <role>    ──Approve──▶ Approved             (when <role> is the last)
    Pending <role>    ──Reject───▶ Rejected
    Rejected          ──Reapply──▶ Draft

Conditions are evaluated against the *live* Visitor Type row, so re-pointing a
type at a different approver takes effect immediately for in-flight passes
without another rebuild.
"""

import frappe

WORKFLOW_NAME = "Visitor Pass Approval"
DOCTYPE = "Visitor Pass"
STATE_FIELD = "workflow_state"

DRAFT = "Draft"
APPROVED = "Approved"
REJECTED = "Rejected"
CANCELLED = "Cancelled"

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

# The role a plain host/reception user has when raising a pass.
REQUESTOR_ROLE = "Employee"

# Cancelling an Approved pass is the approval authority exercised in reverse, so
# the Cancel transitions are generated for the approver roles themselves (see
# `build_workflow`). Security is deliberately not among them: it is read-only on
# Visitor Pass by design — the gate acts through Security Log — and offering it
# an action it cannot execute produces a button that only ever errors.
CANCEL_ROLE = None

STATE_STYLES = {
	DRAFT: "Warning",
	APPROVED: "Success",
	REJECTED: "Danger",
	CANCELLED: "Danger",
}
PENDING_STYLE = "Warning"


def lane_for_role(role: str) -> str:
	"""The pending-state name that belongs to an approver role."""
	return f"Pending {role}"


def _visitor_type_rows():
	"""Active Visitor Types that carry a primary approver, newest naming first."""
	return frappe.get_all(
		"Visitor Type",
		filters={"is_active": 1},
		fields=["name", "approver_role", "secondary_approver_role"],
		order_by="name asc",
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
	frappe.get_doc(
		{"doctype": "Workflow State", "workflow_state_name": state, "style": style}
	).insert(ignore_permissions=True)
	_mark_created("Workflow State", state)


def _ensure_workflow_action(action: str):
	if frappe.db.exists("Workflow Action Master", action):
		return
	frappe.get_doc(
		{"doctype": "Workflow Action Master", "workflow_action_name": action}
	).insert(ignore_permissions=True)
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
	return f'{_vt("approver_role")} == {role!r}'


def _has_secondary(role, secondary):
	"""This pass's type is primary=`role`, secondary=`secondary`."""
	return f'{_vt("approver_role")} == {role!r} and {_vt("secondary_approver_role")} == {secondary!r}'


def _is_final_approver(role):
	"""`role` is the last approver for this pass's type — approving completes it."""
	return (
		f'({_vt("secondary_approver_role")} == {role!r})'
		f' or (not {_vt("secondary_approver_role")} and {_vt("approver_role")} == {role!r})'
	)


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
	for action in (ACTION_SUBMIT, ACTION_APPROVE, ACTION_REJECT, ACTION_REAPPLY, ACTION_CANCEL):
		_ensure_workflow_action(action)
	for state, style in STATE_STYLES.items():
		_ensure_workflow_state(state, style)
	for role in roles:
		_ensure_role(role)
		_ensure_workflow_state(lane_for_role(role), PENDING_STYLE)
	_ensure_role(REQUESTOR_ROLE)

	# --- states -------------------------------------------------------
	states = [{"state": DRAFT, "doc_status": "0", "allow_edit": REQUESTOR_ROLE}]
	for role in roles:
		states.append({"state": lane_for_role(role), "doc_status": "0", "allow_edit": role})
	states.append({"state": APPROVED, "doc_status": "1", "allow_edit": "System Manager"})
	states.append({"state": REJECTED, "doc_status": "0", "allow_edit": REQUESTOR_ROLE})
	states.append({"state": CANCELLED, "doc_status": "2", "allow_edit": "System Manager"})

	# --- transitions --------------------------------------------------
	transitions = []

	# Draft -> the lane owned by each type's primary approver.
	# Sorted: a set's order changes between processes, and the fingerprint below
	# must be the same for the same routing.
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

	# Primary -> secondary, for every (primary, secondary) pair in use.
	pairs = {
		(r.approver_role, r.secondary_approver_role)
		for r in rows
		if r.approver_role and r.secondary_approver_role
	}
	for primary, secondary in sorted(pairs):
		transitions.append(
			{
				"state": lane_for_role(primary),
				"action": ACTION_APPROVE,
				"next_state": lane_for_role(secondary),
				"allowed": primary,
				"condition": _has_secondary(primary, secondary),
				# An approver must not also be the requester who raised this pass —
				# see frappe/model/workflow.py:has_approval_access. Submit/Reject/
				# Reapply stay self-approvable: those are the requester's own moves.
				"allow_self_approval": 0,
			}
		)

	# Any lane -> Approved when that role is the final approver, and -> Rejected.
	for role in roles:
		transitions.append(
			{
				"state": lane_for_role(role),
				"action": ACTION_APPROVE,
				"next_state": APPROVED,
				"allowed": role,
				"condition": _is_final_approver(role),
				"allow_self_approval": 0,
			}
		)
		transitions.append(
			{
				"state": lane_for_role(role),
				"action": ACTION_REJECT,
				"next_state": REJECTED,
				"allowed": role,
			}
		)

	transitions.append(
		{
			"state": REJECTED,
			"action": ACTION_REAPPLY,
			"next_state": DRAFT,
			"allowed": REQUESTOR_ROLE,
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
