import frappe


def execute():
	if frappe.db.exists("Custom Field", "Visitor Pass-custom_passport_copy"):
		frappe.delete_doc("Custom Field", "Visitor Pass-custom_passport_copy", ignore_permissions=True)

	# custom_visa_copy was originally inserted right after custom_passport_copy.
	# Deleting that field above leaves a dangling insert_after reference, which
	# stops custom_visa_copy from rendering at all. Re-anchor it.
	if frappe.db.get_value("Custom Field", "Visitor Pass-custom_visa_copy", "insert_after") == "custom_passport_copy":
		frappe.db.set_value("Custom Field", "Visitor Pass-custom_visa_copy", "insert_after", "custom_nationality")
