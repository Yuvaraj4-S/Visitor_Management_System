import frappe

from visitormanagement.visitor_management.lifecycle import (
	ensure_hospitality_request,
	normalize_visitor_pass,
	sync_compliance_check,
)


def execute():
	for name in frappe.get_all("Visitor Pass", pluck="name"):
		doc = frappe.get_doc("Visitor Pass", name)
		normalize_visitor_pass(doc)

		updates = {
			"request_channel": doc.request_channel,
			"risk_level": doc.risk_level,
			"approval_sla_minutes": doc.approval_sla_minutes,
			"no_show": doc.no_show,
		}
		if doc.visitor_type == "Supplier":
			updates["supplier_visit_mode"] = doc.supplier_visit_mode or "Delivery"

		frappe.db.set_value("Visitor Pass", name, updates, update_modified=False)

		doc.reload()
		ensure_hospitality_request(doc)
		sync_compliance_check(name)
