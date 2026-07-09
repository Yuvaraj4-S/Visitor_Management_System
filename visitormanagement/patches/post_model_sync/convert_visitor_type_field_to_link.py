import frappe


DOCTYPE_FIELDS = [
	("Visitor Pass", "visitor_type"),
	("Visitor Invitation", "visitor_type"),
]


def execute():
	for doctype, fieldname in DOCTYPE_FIELDS:
		if frappe.get_meta(doctype).get_field(fieldname).fieldtype == "Link":
			continue

		if not frappe.db.exists(
			"Property Setter", {"doc_type": doctype, "field_name": fieldname, "property": "fieldtype"}
		):
			frappe.make_property_setter(
				{"doctype": doctype, "fieldname": fieldname, "property": "fieldtype", "value": "Link"}
			)

		if not frappe.db.exists(
			"Property Setter", {"doc_type": doctype, "field_name": fieldname, "property": "options"}
		):
			frappe.make_property_setter(
				{"doctype": doctype, "fieldname": fieldname, "property": "options", "value": "Visitor Type"}
			)
