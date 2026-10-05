import frappe

# Roles used by the Gate Entry / hospitality routing / financial approval features.
# Idempotent: only missing roles are created.
ROLES = [
	"Security",
	"Transport Coordinator",
	"Greeting Staff",
	"Factory Tour Coordinator",
	"VMS Finance Approver",
]


def execute():
	for role_name in ROLES:
		if frappe.db.exists("Role", role_name):
			continue
		frappe.get_doc({"doctype": "Role", "role_name": role_name, "desk_access": 1}).insert(ignore_permissions=True)
