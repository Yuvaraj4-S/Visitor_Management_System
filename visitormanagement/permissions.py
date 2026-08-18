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
		conditions.append(f"`{table}`.`status` in ('Approved', 'Items Verified', 'Checked-In', 'Checked-Out')")

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
	if ptype in _READ_LIKE_PTYPES and (
		"Facility Manager" in roles or "Hospitality Manager" in roles
	):
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
