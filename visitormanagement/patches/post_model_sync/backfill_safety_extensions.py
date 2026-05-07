import frappe

from visitormanagement.visitor_management.lifecycle import (
	generate_emergency_muster_records,
	sync_compliance_check,
	sync_contact_trace,
)


def execute():
	for name in frappe.get_all("Security Log", pluck="name", order_by="creation asc"):
		doc = frappe.get_doc("Security Log", name)
		sync_contact_trace(doc.visitor_pass, doc)

	for name in frappe.get_all("Emergency Event", filters={"status": "Active"}, pluck="name"):
		generate_emergency_muster_records(frappe.get_doc("Emergency Event", name))

	for name in frappe.get_all("Visitor Pass", pluck="name"):
		sync_compliance_check(name)
