import frappe


ROLES = [
	"Host Employee",
	"Hospitality Manager",
	"Transport Coordinator",
	"Front Office Executive",
	"Factory Tour Coordinator",
	"Greeting Staff",
]


def execute():
	for role_name in ROLES:
		if frappe.db.exists("Role", role_name):
			continue
		frappe.get_doc({"doctype": "Role", "role_name": role_name}).insert(ignore_permissions=True)
