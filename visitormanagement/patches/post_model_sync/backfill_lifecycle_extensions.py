import frappe

from visitormanagement.visitor_management.lifecycle import (
	ensure_hospitality_request,
	normalize_visitor_pass,
)


def execute():
	for name in frappe.get_all("Visitor Pass", pluck="name"):
		doc = frappe.get_doc("Visitor Pass", name)
		normalize_visitor_pass(doc)

		updates = {
			"request_channel": doc.request_channel,
			"no_show": doc.no_show,
		}
		if doc.visitor_type == "Supplier":
			updates["supplier_visit_mode"] = doc.supplier_visit_mode or "Meeting"

		frappe.db.set_value("Visitor Pass", name, updates, update_modified=False)

		doc.reload()
		ensure_hospitality_request(doc)
