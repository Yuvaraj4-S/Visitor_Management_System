import frappe
from frappe.utils import cint, getdate, nowdate

from visitormanagement.visitor_management.workflow_builder import (
	ASSIGNED_APPROVER_ROLE,
	FRONT_DESK_ROLE,
	pending_lanes,
)


def _has_any_role(user_roles, roles):
	return any(role in user_roles for role in roles)


def _is_admin(user):
	return user == "Administrator"


def _owner_condition(table_name, user):
	return f"`{table_name}`.`owner` = {frappe.db.escape(user)}"


def _employee_for_user(user):
	"""Return the Employee record linked to a User, if any."""
	if not user or user in ("Administrator", "Guest"):
		return None
	return frappe.db.get_value("Employee", {"user_id": user}, "name")


def get_visitor_pass_permission_query_conditions(user=None):
	user = user or frappe.session.user
	if _is_admin(user):
		return None

	roles = set(frappe.get_roles(user))
	table = "tabVisitor Pass"

	# System Manager is the site's administrative role and its sibling
	# get_visitor_invitation_permission_query_conditions already exempts it. This
	# one did not, so a System Manager who was not literally logged in as
	# Administrator saw only the passes they owned or hosted — 118 of 185 on this
	# site. The dashboard was the visible symptom: "Checked-In Visitors" and
	# "Pending Visitor Approvals" quietly under-reported while presenting
	# themselves as org-wide operations totals.
	if "System Manager" in roles:
		return None

	# Facility Manager (Conference Room Booking → visitor_pass link) and
	# Hospitality Manager (Hospitality Request → visitor_pass link) need to
	# see every Visitor Pass so their link widgets and typeaheads work.
	# They have no write/submit at role level, so this is read-only exposure.
	if "Facility Manager" in roles or "Hospitality Manager" in roles:
		return None

	conditions = [_owner_condition(table, user)]

	# Host scope — user can see any pass where they are person_to_visit (the host)
	employee = _employee_for_user(user)
	if employee:
		conditions.append(f"`{table}`.`person_to_visit` = {frappe.db.escape(employee)}")

	# Approver scope — each role owns the Visitor Types where it is the
	# configured approver_role/secondary_approver_role, end-to-end.
	owned_types = frappe.get_all(
		"Visitor Type",
		or_filters=[["approver_role", "in", list(roles)], ["secondary_approver_role", "in", list(roles)]],
		pluck="name",
	)
	if owned_types:
		type_list = ", ".join(frappe.db.escape(t) for t in owned_types)
		conditions.append(f"`{table}`.`visitor_type` in ({type_list})")
	if "Security" in roles:
		conditions.append(
			f"`{table}`.`status` in ('Approved', 'Items Verified', 'Checked-In', 'Checked-Out')"
		)

	# Named approver scope — the passes that name this user (the host's manager,
	# or a stand-in for an approver). An exact match within the list: LIKE would
	# treat the `_` common in user ids as a wildcard. has_visitor_pass_permission
	# decides what they may do with each one.
	if ASSIGNED_APPROVER_ROLE in roles:
		escaped = frappe.db.escape(f",{user},")
		conditions.append(
			f"locate({escaped}, concat(',', ifnull(`{table}`.`current_approvers`, ''), ',')) > 0"
		)

	# Reception works from today's visitors, whoever raised them.
	if FRONT_DESK_ROLE in roles:
		conditions.append(_visiting_today_condition(table))

	# Parenthesised: Frappe ANDs this fragment with its own clauses (User
	# Permissions, share filters). Unbracketed, `AND` binds tighter than `OR`, so
	# a User Permission would constrain only the first disjunct and every other
	# branch would widen the result set past it.
	return "(" + " or ".join(conditions) + ")" if conditions else "1=0"


def _visiting_today_condition(table):
	today = frappe.db.escape(str(getdate(nowdate())))
	return (
		f"(`{table}`.`visit_date` = {today} or (`{table}`.`multi_day_pass` = 1"
		f" and `{table}`.`visit_date` <= {today} and `{table}`.`pass_valid_until` >= {today}))"
	)


def _is_visiting_today(doc):
	today = getdate(nowdate())
	if not doc.get("visit_date"):
		return False
	visit_date = getdate(doc.visit_date)
	if visit_date == today:
		return True
	return bool(
		cint(doc.get("multi_day_pass"))
		and doc.get("pass_valid_until")
		and visit_date <= today <= getdate(doc.pass_valid_until)
	)


# What the people a visit belongs to may do with an approved pass: call it off,
# until the visitor arrives (VisitorPass.before_cancel refuses it after that too).
_CANCELLABLE_STATUSES = ("Approved", "Items Verified")


def _host_may_cancel(doc, ptype):
	# `status` and not the workflow state: the workflow has already moved the
	# state to Cancelled by the time the cancel is permission-checked, while
	# `status` changes only in on_cancel.
	return ptype == "cancel" and doc.get("status") in _CANCELLABLE_STATUSES and not doc.get("actual_checkin")


def _named_approver_permission(doc, user, ptype):
	"""What a pass's named approver may do with it: None when the pass does not name them.

	While the pass waits, they may work it as its approver, judged on who may
	decide it today (approval_tasks.may_decide) rather than the stored list — a
	stand-in whose delegation has ended gets nothing. Once it is decided they keep
	sight of it, as the lane roles keep sight of the passes they decided.
	"""
	from visitormanagement.visitor_management import approval_tasks

	if not approval_tasks.is_named(doc, user):
		# A decision moves `current_approvers` on — to the next lane's stand-ins,
		# or to nothing once the pass is decided — so the pass stops naming whoever
		# decided it: a stand-in got "Not permitted" the moment their Approve went
		# through, and could not open the pass again. Frappe records every workflow
		# action as a "Workflow" comment by its actor (a row only System Manager may
		# write by hand), which is what keeps their sight of it.
		if ptype in _READ_LIKE_PTYPES and _acted_on_in_workflow(doc, user):
			return True
		return None
	# Judged on the pass as stored: during an Approve the workflow has already
	# moved the document being saved to its next state.
	stored = (
		frappe.db.get_value(
			"Visitor Pass",
			doc.name,
			["name", "docstatus", "workflow_state", "visitor_type", "person_to_visit", "owner"],
			as_dict=True,
		)
		if doc.get("name")
		else None
	) or doc
	if stored.get("docstatus") == 0 and stored.get("workflow_state") in pending_lanes():
		if ptype in _NAMED_APPROVER_PTYPES and approval_tasks.may_decide(stored, user):
			return True
		return None
	return True if ptype in _READ_LIKE_PTYPES else None


def _acted_on_in_workflow(doc, user):
	"""Whether `user` moved this pass through its workflow (Approve, Reject, ...)."""
	if not doc.get("name"):
		return False
	return bool(
		frappe.db.exists(
			"Comment",
			{
				"reference_doctype": "Visitor Pass",
				"reference_name": doc.name,
				"comment_type": "Workflow",
				"owner": user,
			},
		)
	)


def get_visitor_invitation_permission_query_conditions(user=None):
	"""Scope Visitor Invitation to the host who raised it.

	An invitation row carries `invitation_token` — the bearer credential that
	opens the visitor's pre-registration form. Employee holds read+write on this
	doctype so hosts can invite their own guests, and without a row filter that
	meant every member of staff could read every token and edit anyone's
	invitation. The token is a capability, so listing it is disclosing it.
	"""
	user = user or frappe.session.user
	if _is_admin(user):
		return None

	roles = set(frappe.get_roles(user))
	table = "tabVisitor Invitation"

	if "System Manager" in roles:
		return None

	conditions = [_owner_condition(table, user)]
	employee = _employee_for_user(user)
	if employee:
		conditions.append(f"`{table}`.`host_employee` = {frappe.db.escape(employee)}")

	# Approvers oversee the visitor types they are responsible for — the same
	# scope they get on Visitor Pass, rather than blanket visibility.
	owned_types = _approver_visitor_types(roles)
	if owned_types:
		type_list = ", ".join(frappe.db.escape(t) for t in owned_types)
		conditions.append(f"`{table}`.`visitor_type` in ({type_list})")

	return "(" + " or ".join(conditions) + ")"


def _approver_visitor_types(roles):
	return frappe.get_all(
		"Visitor Type",
		or_filters=[["approver_role", "in", list(roles)], ["secondary_approver_role", "in", list(roles)]],
		pluck="name",
	)


def has_visitor_invitation_permission(doc, user=None, ptype=None, debug=False):
	user = user or frappe.session.user
	if _is_admin(user):
		return True

	roles = set(frappe.get_roles(user))
	if "System Manager" in roles:
		return True

	if doc.owner == user:
		return True

	employee = _employee_for_user(user)
	if employee and doc.host_employee == employee:
		return True

	# Approver oversight is visibility, not custody. Without this the approver
	# branch granted write as well, so any approver role could edit another
	# host's invitation — including repointing `visitor_email` and triggering a
	# resend, which mails the bearer token to an address of their choosing.
	if ptype in _READ_LIKE_PTYPES:
		return bool(doc.visitor_type and doc.visitor_type in _approver_visitor_types(roles))

	return False


# Permission types that count as "read-like" — holding one of these does NOT let
# the user write/submit/cancel/delete the doc.
#
# `None` is deliberately absent. Frappe invokes this hook as
# `frappe.call(method, doc=..., ptype=..., user=...)`, and `frappe.call` drops
# any keyword the function does not declare — so while the parameter here was
# named `permission_type`, it was *always* None, None was in this set, and every
# read-only branch below returned True for write and submit as well. An unknown
# permission type now falls through to the deny path instead.
#
# `share` is absent for the same reason it matters here: sharing a Visitor
# Invitation hands over the `invitation_token` inside it, which is a bearer
# credential, not a view.
_READ_LIKE_PTYPES = {"read", "select", "print", "email", "report", "export"}

# What raising a pass entitles you to on your own pass. Deliberately excludes
# `submit`, `cancel` and `amend`: those are approval authority, and approval must
# come from the role the Visitor Type names, never from having created the
# record. Without this boundary an owner or host who happened to hold any
# blanket submit role (System Manager, Sales Manager, HR Manager, HOD, CEO —
# for any unrelated reason) could take a pass sitting in someone else's approval
# lane, set docstatus=1, save, and land it at "Approved" with the configured
# approver never involved. Frappe's own machinery then completes the illusion:
# `set_workflow_state_on_action` force-sets workflow_state to whichever state
# carries doc_status 1, so the pass reads as legitimately approved and every
# downstream control — badge minting, the gate — correctly trusts it.
_OWNER_PTYPES = _READ_LIKE_PTYPES | {"write", "create", "delete"}

# A named approver acts on a waiting pass through the workflow: Approve submits
# it on the last step.
_NAMED_APPROVER_PTYPES = _READ_LIKE_PTYPES | {"write", "submit"}


def has_visitor_pass_permission(doc, user=None, ptype=None, debug=False):
	user = user or frappe.session.user
	if _is_admin(user):
		return True

	# Owner and host scope. Note these *fall through* rather than returning False
	# for anything outside _OWNER_PTYPES — a Sales Manager who happens to own a
	# Customer pass is still that pass's configured approver, and the approver
	# branch further down is what should decide that, not an early denial here.
	if doc.owner == user and (ptype in _OWNER_PTYPES or _host_may_cancel(doc, ptype)):
		return True

	# Host scope — user is the person the visitor is meeting
	employee = _employee_for_user(user)
	if (
		employee
		and doc.person_to_visit == employee
		and (ptype in _OWNER_PTYPES or _host_may_cancel(doc, ptype))
	):
		return True

	if _named_approver_permission(doc, user, ptype):
		return True

	roles = set(frappe.get_roles(user))

	# Reception reads today's visitors, whoever raised them; it writes only its own.
	if FRONT_DESK_ROLE in roles and ptype in _READ_LIKE_PTYPES and _is_visiting_today(doc):
		return True

	# System Manager administers the site, so it gets the same reach here that it
	# has in the query conditions — but deliberately not `submit`/`cancel`/
	# `amend`. Those stay with the Visitor Type's configured approver for exactly
	# the reason _OWNER_PTYPES exists: System Manager is one of the blanket-submit
	# roles, and granting it here would reopen the self-approval bypass from the
	# other side. A System Manager who genuinely belongs to an approval lane still
	# reaches it through the approver branch below, and the workflow action itself
	# is unaffected — this only refuses a bare docstatus flip.
	if "System Manager" in roles and ptype in _OWNER_PTYPES:
		return True

	# Facility Manager + Hospitality Manager: read-only on all passes so
	# Conference Room Booking / Hospitality Request link widgets can render
	# the visitor's title. Write paths are blocked at role level.
	if ptype in _READ_LIKE_PTYPES and ("Facility Manager" in roles or "Hospitality Manager" in roles):
		return True

	# Approver scope — role owns this doc's Visitor Type as approver_role or
	# secondary_approver_role.
	visitor_type_doc = None
	if doc.visitor_type:
		try:
			visitor_type_doc = frappe.get_cached_doc("Visitor Type", doc.visitor_type)
		except frappe.DoesNotExistError:
			visitor_type_doc = None
	if visitor_type_doc and (
		visitor_type_doc.approver_role in roles
		or (visitor_type_doc.secondary_approver_role and visitor_type_doc.secondary_approver_role in roles)
	):
		return True
	# Security reads approved passes and works the gate through Security Log. It
	# writes only the walk-ins it raised itself, through the owner scope above
	# (walk_in.py). Without this guard, has_permission would short-circuit `True`
	# for every ptype and let Security write/submit any approved pass.
	if "Security" in roles and doc.status in {
		"Approved",
		"Items Verified",
		"Checked-In",
		"Checked-Out",
	}:
		return ptype in _READ_LIKE_PTYPES

	return False


# ─────────────────────────────────────────────────────────
# Hospitality Request
# ─────────────────────────────────────────────────────────
# `Employee` holds read+write+create on Hospitality Request with if_owner = 0,
# and no query condition was registered — so every member of staff could open
# and overwrite every other host's request. That is not an abstract exposure:
# these rows carry dietary allergies, accessibility needs, hotel cost, and
# drivers' phone numbers. An audit proved it by editing another host's request
# from a Hospitality User account and pushing it into an approval queue.
#
# Scoped the same way Visitor Pass is: the people who run hospitality see
# everything, and everyone else sees the requests that are theirs — raised by
# them, assigned to them, or belonging to a visitor they are hosting.
HOSPITALITY_OVERSEERS = ("System Manager", "Hospitality Manager")

# The service teams the 07:00 hospitality digest goes to
# (settings.DEFAULT_DIGEST_ROLES), each with the services it delivers — None for
# all of them. They hold read on the doctype (setup.HOSPITALITY_DIGEST_READERS),
# but the row scope above let only the overseers see other people's requests, so
# a cab coordinator opened a request from the digest or the Daily Hospitality
# Schedule and got "Not permitted", and the schedule came up empty for them.
#
# Read only: delivering a service is recorded by the Hospitality Manager, and
# write stays with the owner, the host and the assigned staff member. Only
# requests that were sent in count — a Draft is the host's own business until
# then — and an approved request stays readable once it is cancelled, so a
# coordinator who was told it was called off can still open it.
HOSPITALITY_SERVICE_ROLES = {
	"Hospitality User": None,
	"Front Office Executive": None,
	"Transport Coordinator": ("cab_required", "buggy_required"),
	"Factory Tour Coordinator": ("factory_tour_required",),
	"Greeting Staff": ("greeting_required",),
}


def _hospitality_service_flags(roles):
	"""The service flags whose requests `roles` may read: a tuple, None for every service, or False."""
	flags = set()
	for role, services in HOSPITALITY_SERVICE_ROLES.items():
		if role in roles:
			if services is None:
				return None
			flags.update(services)
	return tuple(sorted(flags)) if flags else False


def _hospitality_service_condition(table, roles):
	flags = _hospitality_service_flags(roles)
	if flags is False:
		return None
	sent_in = f"(`{table}`.`docstatus` > 0 or `{table}`.`workflow_state` = 'Pending Approval')"
	if flags is None:
		return sent_in
	services = " or ".join(f"`{table}`.`{flag}` = 1" for flag in flags)
	return f"({sent_in} and ({services}))"


def _is_hospitality_service_reader(doc, roles):
	flags = _hospitality_service_flags(roles)
	if flags is False:
		return False
	if not (doc.docstatus > 0 or doc.get("workflow_state") == "Pending Approval"):
		return False
	return flags is None or any(cint(doc.get(flag)) for flag in flags)


def get_hospitality_request_permission_query_conditions(user=None):
	user = user or frappe.session.user
	if _is_admin(user):
		return None

	roles = set(frappe.get_roles(user))
	if _has_any_role(roles, HOSPITALITY_OVERSEERS):
		return None

	table = "tabHospitality Request"
	conditions = [_owner_condition(table, user)]

	employee = _employee_for_user(user)
	if employee:
		escaped = frappe.db.escape(employee)
		# Assigned to them to deliver.
		conditions.append(f"`{table}`.`assigned_staff` = {escaped}")
		# Or named on it to deliver one service: the guide, the buggy driver, the greeter.
		conditions.append(
			f"{escaped} in (`{table}`.`tour_guide`, `{table}`.`buggy_driver`, `{table}`.`greeting_assigned_to`)"
		)
		# Or raised for a visitor they are hosting. The host is on the pass, not
		# on the request, so this has to go through the link.
		conditions.append(
			f"`{table}`.`visitor_pass` in "
			f"(select `name` from `tabVisitor Pass` where `person_to_visit` = {escaped})"
		)

	# Or a service their team delivers (see HOSPITALITY_SERVICE_ROLES).
	service = _hospitality_service_condition(table, roles)
	if service:
		conditions.append(service)

	# Parenthesised for the same reason as Visitor Pass: Frappe ANDs this with
	# its own clauses, and unbracketed `AND` would bind tighter than `OR`.
	return "(" + " or ".join(conditions) + ")"


def has_hospitality_request_permission(doc, user=None, ptype=None, debug=False):
	user = user or frappe.session.user
	if _is_admin(user):
		return True

	roles = set(frappe.get_roles(user))
	if _has_any_role(roles, HOSPITALITY_OVERSEERS):
		return True

	if doc.owner == user:
		return True

	employee = _employee_for_user(user)
	if employee:
		if doc.get("assigned_staff") == employee:
			return True
		if ptype in _READ_LIKE_PTYPES and employee in (
			doc.get("tour_guide"),
			doc.get("buggy_driver"),
			doc.get("greeting_assigned_to"),
		):
			return True
		if (
			doc.get("visitor_pass")
			and frappe.db.get_value("Visitor Pass", doc.visitor_pass, "person_to_visit") == employee
		):
			return True

	if ptype in _READ_LIKE_PTYPES and _is_hospitality_service_reader(doc, roles):
		return True

	# The people a visit belongs to may see what was arranged for it: whoever
	# raised the pass, and the approvers of its Visitor Type. The approvers are not
	# a convenience: `ensure_hospitality_request` creates the request inside the
	# approving user's own request, and Frappe's `validate_workflow` calls
	# `get_transitions`, which needs read on the new document. Without them an
	# approver who is not also the host — a CEO signing off a VIP visit, say — got
	# a PermissionError and the approval itself failed. (The host is covered above.)
	#
	# This used to ask "can this user read the Visitor Pass?", which is a wider
	# question: Security reads every approved pass and Facility Manager reads all
	# of them, each for a reason that has nothing to do with hospitality, and both
	# inherited read on the request — dietary allergies, accessibility needs, the
	# driver's phone number and the hotel booking — which the list view rightly
	# never showed them. Read on the pass is not the test; a stake in the visit is.
	if ptype in _READ_LIKE_PTYPES and doc.get("visitor_pass"):
		# An orphaned request — one whose pass was deleted — has nobody left to
		# inherit from. A permission question about a record that is not there is "no".
		visitor_pass = frappe.db.get_value(
			"Visitor Pass", doc.visitor_pass, ["owner", "visitor_type", "current_approvers"], as_dict=True
		)
		if not visitor_pass:
			return False
		if visitor_pass.owner == user:
			return True
		# Named approvers too (the host's manager, a stand-in): the request is
		# brought in line inside their own Approve, which needs read on it.
		from visitormanagement.visitor_management.approval_tasks import is_named

		if is_named(visitor_pass, user):
			return True
		return bool(visitor_pass.visitor_type and visitor_pass.visitor_type in _approver_visitor_types(roles))

	return False


# ─────────────────────────────────────────────────────────
# Conference Room Booking
# ─────────────────────────────────────────────────────────
# `Employee` holds create/read/write on Conference Room Booking doctype-wide
# (no `if_owner`), and — unlike Visitor Pass, Visitor Invitation and
# Hospitality Request above — no query condition or has_permission hook was
# ever registered for it. So any member of staff could open any other
# employee's booking: its `meeting_title`, `department`, attendee count and
# `booked_by` are all visible with no scoping at all — a title such as
# "Board interview — CFO candidate" was readable by every Employee.
#
# Scoped the same way Hospitality Request is: the people who run the rooms
# see everything, and everyone else sees only their own bookings — made by
# them, or organised by them. Deliberately narrower than Visitor Pass's own
# conditions: there is no host/approver concept to extend visibility to here,
# and no read-only role like Security to carve an exception for — a booking
# is either yours to run or it isn't.
#
# The calendar, the room schedule and the Daily Booking Schedule read bookings
# with raw SQL so every taken slot shows, and apply this same condition to mask
# everyone else's booking as "Busy" (conference_room_booking._show_as_busy).
CONFERENCE_ROOM_BOOKING_OVERSEERS = ("System Manager", "Facility Manager")


def get_conference_room_booking_permission_query_conditions(user=None):
	user = user or frappe.session.user
	if _is_admin(user):
		return None

	roles = set(frappe.get_roles(user))
	if _has_any_role(roles, CONFERENCE_ROOM_BOOKING_OVERSEERS):
		return None

	table = "tabConference Room Booking"
	conditions = [_owner_condition(table, user)]

	employee = _employee_for_user(user)
	if employee:
		conditions.append(f"`{table}`.`booked_by` = {frappe.db.escape(employee)}")

	# Parenthesised for the same reason as the other three doctypes above:
	# Frappe ANDs this with its own clauses, and unbracketed `AND` would bind
	# tighter than `OR`.
	return "(" + " or ".join(conditions) + ")"


def has_conference_room_booking_permission(doc, user=None, ptype=None, debug=False):
	user = user or frappe.session.user
	if _is_admin(user):
		return True

	roles = set(frappe.get_roles(user))
	if _has_any_role(roles, CONFERENCE_ROOM_BOOKING_OVERSEERS):
		return True

	if doc.owner == user:
		return True

	employee = _employee_for_user(user)
	if employee and doc.get("booked_by") == employee:
		return True

	return False
