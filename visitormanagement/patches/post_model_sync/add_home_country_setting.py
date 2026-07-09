import frappe


def execute():
	if not frappe.db.exists("Custom Field", "VMS Settings-home_country"):
		frappe.get_doc({
			"doctype": "Custom Field",
			"dt": "VMS Settings",
			"fieldname": "home_country",
			"label": "Home Country",
			"fieldtype": "Link",
			"options": "Country",
			"insert_after": "admin_email",
			"default": "India",
			"description": "Visitors with a nationality other than this country are treated as foreign nationals.",
		}).insert(ignore_permissions=True)

	if not frappe.db.get_single_value("VMS Settings", "home_country"):
		frappe.db.set_single_value("VMS Settings", "home_country", "India")
