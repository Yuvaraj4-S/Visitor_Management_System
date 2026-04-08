import frappe


def execute():
    roles = [
        "Visitor Manager",
        "Gate Security",
        "Security Supervisor",
    ]

    for role_name in roles:
        if not frappe.db.exists("Role", role_name):
            role = frappe.get_doc({"doctype": "Role", "role_name": role_name})
            role.insert(ignore_permissions=True)
