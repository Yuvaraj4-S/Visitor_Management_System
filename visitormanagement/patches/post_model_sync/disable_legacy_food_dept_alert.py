import frappe


def execute():
	if not frappe.db.exists("Notification", "VMS Food Dept Alert"):
		return

	frappe.db.set_value("Notification", "VMS Food Dept Alert", "enabled", 0, update_modified=False)
