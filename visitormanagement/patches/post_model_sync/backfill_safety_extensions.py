import frappe

from visitormanagement.visitor_management.lifecycle import sync_contact_trace


def execute():
	for name in frappe.get_all("Security Log", pluck="name", order_by="creation asc"):
		doc = frappe.get_doc("Security Log", name)
		sync_contact_trace(doc.visitor_pass, doc)
