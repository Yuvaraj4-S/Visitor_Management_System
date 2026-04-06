import frappe


def execute():
	if frappe.db.exists("Role", "Hospitality User"):
		return

	role = frappe.get_doc({"doctype": "Role", "role_name": "Hospitality User"})
	role.insert(ignore_permissions=True)
