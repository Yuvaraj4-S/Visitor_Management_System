import frappe


def _has_any_role(user_roles, roles):
	return any(role in user_roles for role in roles)


def _is_admin(user):
	return user == "Administrator"


# Roles that read every Visitor Pass (read-only; write is blocked at role level).
PASS_READ_ALL_ROLES = ("Facility Manager", "VMS Manager", "VMS User")

# Hospitality Request: the VMS Manager assigns each request to a VMS User, who then
# enters the cab / meal / factory-tour details. A VMS User only works on what they
# were assigned (plus their own visitors' requests as a host).
HOSPITALITY_FULL_ACCESS_ROLES = ("VMS Manager", "System Manager")


def _is_restricted_vms_user(user, roles=None):
	roles = roles if roles is not None else set(frappe.get_roles(user))
	return "VMS User" in roles and not _has_any_role(roles, HOSPITALITY_FULL_ACCESS_ROLES)


def get_hospitality_request_permission_query_conditions(user=None):
	user = user or frappe.session.user
	if _is_admin(user) or not _is_restricted_vms_user(user):
		return None
	table = "tabHospitality Request"
	conditions = [
		_owner_condition(table, user),
		# Assigned via "Assign to VMS User" (standard assignment) ...
		f"`{table}`.`_assign` like {frappe.db.escape('%' + user + '%')}",
		# ... or picked in a service field (Cab / Hotel / Buggy / Greeting), which shares the request.
		f"`{table}`.`name` in (select share_name from `tabDocShare` where share_doctype = 'Hospitality Request' "
		f"and user = {frappe.db.escape(user)})",
	]
	employee = _employee_for_user(user)
	if employee:
		conditions.append(
			f"`{table}`.`visitor_pass` in (select name from `tabVisitor Pass` "
			f"where person_to_visit = {frappe.db.escape(employee)})"
		)
	return "(" + " or ".join(conditions) + ")"


def has_hospitality_request_permission(doc, user=None, permission_type=None):
	user = user or frappe.session.user
	if _is_admin(user) or not _is_restricted_vms_user(user):
		return True
	# Read owner / assignment / pass from the database, not from the document being
	# checked: on a form Save Frappe rebuilds the doc from what the browser sends, and
	# the browser never sends the internal `_assign` list (and could alter the rest).
	saved = (
		frappe.db.get_value(doc.doctype, doc.name, ["owner", "_assign", "visitor_pass"], as_dict=True)
		if doc.name
		else None
	) or frappe._dict(owner=doc.owner, _assign=doc.get("_assign"), visitor_pass=doc.visitor_pass)

	if saved.owner == user or user in (frappe.parse_json(saved._assign) or []):
		return True
	if frappe.db.exists("DocShare", {"share_doctype": doc.doctype, "share_name": doc.name, "user": user}):
		return True
	employee = _employee_for_user(user)
	if employee and saved.visitor_pass:
		return frappe.db.get_value("Visitor Pass", saved.visitor_pass, "person_to_visit") == employee
	return False


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

	# Facility Manager (Conference Room Booking → visitor_pass link) and the
	# VMS hospitality team (Hospitality Request → visitor_pass link) need to
	# see every Visitor Pass so their link widgets and typeaheads work.
	# They have no write/submit at role level, so this is read-only exposure.
	if _has_any_role(roles, PASS_READ_ALL_ROLES):
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

	return " or ".join(conditions) if conditions else "1=0"


# Permission types that count as "read-like" — granting these to a role does
# NOT let the user write/submit/cancel/delete the doc.
_READ_LIKE_PTYPES = {None, "read", "select", "print", "email", "report", "export", "share"}


def has_visitor_pass_permission(doc, user=None, permission_type=None):
	user = user or frappe.session.user
	if _is_admin(user):
		return True

	if doc.owner == user:
		return True

	# Host scope — user is the person the visitor is meeting
	employee = _employee_for_user(user)
	if employee and doc.person_to_visit == employee:
		return True

	roles = set(frappe.get_roles(user))

	# Facility Manager + VMS Manager / VMS User: read-only on all passes so
	# Conference Room Booking / Hospitality Request link widgets can render
	# the visitor's title. Write paths are blocked at role level.
	if permission_type in _READ_LIKE_PTYPES and _has_any_role(roles, PASS_READ_ALL_ROLES):
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
		return permission_type in _READ_LIKE_PTYPES

	return False
