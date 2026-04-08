import frappe


def _has_any_role(user_roles, roles):
	return any(role in user_roles for role in roles)


def _is_admin(user):
	return user == "Administrator"


def _owner_condition(table_name, user):
	return f"`{table_name}`.`owner` = {frappe.db.escape(user)}"


def get_prr_permission_query_conditions(user=None):
	user = user or frappe.session.user
	if _is_admin(user):
		return None

	roles = set(frappe.get_roles(user))
	table = "tabPre-Registration Request"
	conditions = [_owner_condition(table, user)]

	if "System Manager" in roles:
		conditions.append(
			f"(`{table}`.`status` = 'Pending System Manager' and `{table}`.`visitor_type` in ('Contractor', 'Supplier'))"
		)
	if "HR Manager" in roles:
		conditions.append(f"(`{table}`.`status` = 'Pending HR Manager' and `{table}`.`visitor_type` = 'Candidate')")
	if "Sales Manager" in roles:
		conditions.append(f"(`{table}`.`status` = 'Pending Sales Manager' and `{table}`.`visitor_type` = 'Customer')")
	if "HOD" in roles:
		conditions.append(f"(`{table}`.`status` = 'Pending HOD' and `{table}`.`visitor_type` = 'VIP')")
	if "CEO" in roles:
		conditions.append(f"(`{table}`.`status` = 'Pending CEO' and `{table}`.`visitor_type` = 'VIP')")

	return " or ".join(conditions) if conditions else "1=0"


def has_prr_permission(doc, user=None, permission_type=None):
	user = user or frappe.session.user
	if _is_admin(user):
		return True

	if doc.owner == user:
		return True

	roles = set(frappe.get_roles(user))
	if "System Manager" in roles and doc.status == "Pending System Manager" and doc.visitor_type in {"Contractor", "Supplier"}:
		return True
	if "HR Manager" in roles and doc.status == "Pending HR Manager" and doc.visitor_type == "Candidate":
		return True
	if "Sales Manager" in roles and doc.status == "Pending Sales Manager" and doc.visitor_type == "Customer":
		return True
	if "HOD" in roles and doc.status == "Pending HOD" and doc.visitor_type == "VIP":
		return True
	if "CEO" in roles and doc.status == "Pending CEO" and doc.visitor_type == "VIP":
		return True

	return False


def get_visitor_pass_permission_query_conditions(user=None):
	user = user or frappe.session.user
	if _is_admin(user):
		return None

	roles = set(frappe.get_roles(user))
	table = "tabVisitor Pass"
	conditions = [_owner_condition(table, user)]

	if "System Manager" in roles:
		conditions.append(
			f"(`{table}`.`workflow_state` in ('Pending System Manager', 'Pending Visitor Manager') and `{table}`.`visitor_type` in ('Contractor', 'Supplier'))"
		)
	if "HR Manager" in roles:
		conditions.append(f"(`{table}`.`workflow_state` = 'Pending HR Manager' and `{table}`.`visitor_type` = 'Candidate')")
	if "Sales Manager" in roles:
		conditions.append(f"(`{table}`.`workflow_state` = 'Pending Sales Manager' and `{table}`.`visitor_type` = 'Customer')")
	if "HOD" in roles:
		conditions.append(f"(`{table}`.`workflow_state` = 'Pending HOD' and `{table}`.`visitor_type` = 'VIP')")
	if "CEO" in roles:
		conditions.append(f"(`{table}`.`workflow_state` = 'Pending CEO' and `{table}`.`visitor_type` = 'VIP')")
	if _has_any_role(roles, {"Gate Security", "Security Supervisor", "Manager"}):
		conditions.append(f"`{table}`.`status` in ('Approved', 'Items Verified', 'Checked-In', 'Checked-Out')")

	return " or ".join(conditions) if conditions else "1=0"


def has_visitor_pass_permission(doc, user=None, permission_type=None):
	user = user or frappe.session.user
	if _is_admin(user):
		return True

	if doc.owner == user:
		return True

	roles = set(frappe.get_roles(user))
	if "System Manager" in roles and doc.workflow_state in {"Pending System Manager", "Pending Visitor Manager"} and doc.visitor_type in {"Contractor", "Supplier"}:
		return True
	if "HR Manager" in roles and doc.workflow_state == "Pending HR Manager" and doc.visitor_type == "Candidate":
		return True
	if "Sales Manager" in roles and doc.workflow_state == "Pending Sales Manager" and doc.visitor_type == "Customer":
		return True
	if "HOD" in roles and doc.workflow_state == "Pending HOD" and doc.visitor_type == "VIP":
		return True
	if "CEO" in roles and doc.workflow_state == "Pending CEO" and doc.visitor_type == "VIP":
		return True
	if _has_any_role(roles, {"Gate Security", "Security Supervisor", "Manager"}) and doc.status in {
		"Approved",
		"Items Verified",
		"Checked-In",
		"Checked-Out",
	}:
		return True

	return False
