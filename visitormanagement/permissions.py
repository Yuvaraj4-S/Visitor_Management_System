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
	conditions = [_owner_condition(table, user)]

	# Host scope — user can see any pass where they are person_to_visit (the host)
	employee = _employee_for_user(user)
	if employee:
		conditions.append(f"`{table}`.`person_to_visit` = {frappe.db.escape(employee)}")

	# Approver scope — each role owns their visitor_type end-to-end
	if "System Manager" in roles:
		conditions.append(f"`{table}`.`visitor_type` in ('Contractor', 'Supplier')")
	if "HR Manager" in roles:
		conditions.append(f"`{table}`.`visitor_type` = 'Candidate'")
	if "Sales Manager" in roles:
		conditions.append(f"`{table}`.`visitor_type` = 'Customer'")
	if "HOD" in roles or "CEO" in roles:
		conditions.append(f"`{table}`.`visitor_type` = 'VIP'")
	if "Security" in roles:
		conditions.append(f"`{table}`.`status` in ('Approved', 'Items Verified', 'Checked-In', 'Checked-Out')")

	return " or ".join(conditions) if conditions else "1=0"


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
	if "System Manager" in roles and doc.visitor_type in {"Contractor", "Supplier"}:
		return True
	if "HR Manager" in roles and doc.visitor_type == "Candidate":
		return True
	if "Sales Manager" in roles and doc.visitor_type == "Customer":
		return True
	if ("HOD" in roles or "CEO" in roles) and doc.visitor_type == "VIP":
		return True
	if "Security" in roles and doc.status in {
		"Approved",
		"Items Verified",
		"Checked-In",
		"Checked-Out",
	}:
		return True

	return False
