import frappe


def execute():
	options = frappe.get_meta("Visitor Pass").get_field("id_proof_type").options or ""
	if options.startswith("\n"):
		return

	if not frappe.db.exists(
		"Property Setter", {"doc_type": "Visitor Pass", "field_name": "id_proof_type", "property": "options"}
	):
		frappe.make_property_setter(
			{
				"doctype": "Visitor Pass",
				"fieldname": "id_proof_type",
				"property": "options",
				"value": "\nAadhaar\nPAN Card\nPassport\nDriving License",
			}
		)
