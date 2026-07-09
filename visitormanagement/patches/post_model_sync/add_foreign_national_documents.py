import frappe


def execute():
	# Passport Copy is intentionally not created here — it duplicated
	# id_proof_scan (already mandatory, and forced to be a passport scan once
	# id_proof_type is restricted to "Passport" for foreign nationals). See
	# remove_redundant_passport_copy_field.py for sites where it was already
	# created by an earlier version of this patch.
	if not frappe.db.exists("Custom Field", "Visitor Pass-custom_visa_copy"):
		frappe.get_doc(
			{
				"doctype": "Custom Field",
				"dt": "Visitor Pass",
				"fieldname": "custom_visa_copy",
				"label": "Visa Copy",
				"fieldtype": "Attach",
				"insert_after": "custom_nationality",
			}
		).insert(ignore_permissions=True)
