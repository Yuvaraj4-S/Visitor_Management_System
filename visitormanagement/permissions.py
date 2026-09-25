import frappe


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

	# Parenthesised: Frappe ANDs this fragment with its own clauses (User
	# Permissions, share filters). Unbracketed, `AND` binds tighter than `OR`, so
	# a User Permission would constrain only the first disjunct and every other
	# branch would widen the result set past it.
	return "(" + " or ".join(conditions) + ")" if conditions else "1=0"


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


def has_visitor_pass_permission(doc, user=None, ptype=None, debug=False):
	user = user or frappe.session.user
	if _is_admin(user):
		return True

	# Owner and host scope. Note these *fall through* rather than returning False
	# for anything outside _OWNER_PTYPES — a Sales Manager who happens to own a
	# Customer pass is still that pass's configured approver, and the approver
	# branch further down is what should decide that, not an early denial here.
	if doc.owner == user and ptype in _OWNER_PTYPES:
		return True

	# Host scope — user is the person the visitor is meeting
	employee = _employee_for_user(user)
	if employee and doc.person_to_visit == employee and ptype in _OWNER_PTYPES:
		return True

	roles = set(frappe.get_roles(user))

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
	# Security role is read-only on Visitor Pass — gate workflow happens through
	# Security role is read-only on Visitor Pass. Gate workflow happens through
	# Security Log (where they have write). Without this guard, has_permission
	# would short-circuit `True` for every ptype and let Security write/submit
	# any approved pass.
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
		# Or raised for a visitor they are hosting. The host is on the pass, not
		# on the request, so this has to go through the link.
		conditions.append(
			f"`{table}`.`visitor_pass` in "
			f"(select `name` from `tabVisitor Pass` where `person_to_visit` = {escaped})"
		)

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
		if (
			doc.get("visitor_pass")
			and frappe.db.get_value("Visitor Pass", doc.visitor_pass, "person_to_visit") == employee
		):
			return True

	# If you are allowed to see the visit, you are allowed to see what was
	# arranged for it. This is not a convenience: `ensure_hospitality_request`
	# creates the request inside the approving user's own request, and Frappe's
	# `validate_workflow` calls `get_transitions`, which needs read on the new
	# document. Without this an approver who is not also the host — a CEO signing
	# off a VIP visit, say — got a PermissionError and the approval itself failed.
	# Visitor Pass is already row-scoped to owner/host/approver/Security, so this
	# inherits that boundary rather than widening past it.
	if ptype in _READ_LIKE_PTYPES and doc.get("visitor_pass"):
		# `has_permission` raises DoesNotExistError rather than returning False for
		# a missing document, so an orphaned request — one whose pass was deleted —
		# would throw out of a permission check and take the whole list view with
		# it. A permission question about a record that is not there is "no".
		if not frappe.db.exists("Visitor Pass", doc.visitor_pass):
			return False
		return bool(frappe.has_permission("Visitor Pass", "read", doc=doc.visitor_pass, user=user))

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
# This does NOT close every leak on this doctype. `get_booking_events` (the
# calendar view in conference_room_booking.py, a file this fix does not own)
# calls `frappe.has_permission("Conference Room Booking", "read", throw=True)`
# with no `doc` — a blanket, doctype-level check that does not invoke this
# module's has_permission hook at all — and then runs a raw `frappe.db.sql`
# that selects every booking's `meeting_title` company-wide with no row
# filtering whatsoever. That endpoint's own comment says as much: "so the
# check has to be explicit — otherwise a future decision to scope bookings
# would be silently undone by this one endpoint." This IS that future
# decision, and it does get silently undone by that endpoint. Whoever owns
# conference_room_booking.py needs to either scope that raw SQL with the same
# owner/booked_by/overseer condition, or stop returning `meeting_title` from
# it for callers who are not an overseer.
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
