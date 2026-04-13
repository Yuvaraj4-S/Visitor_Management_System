import frappe


def execute():
	if not frappe.db.has_column("Visitor Pass", "visitor_summary"):
		return

	passes = frappe.get_all(
		"Visitor Pass",
		fields=["name", "visitor_full_name", "visitor_type", "mobile_number"],
	)
	for vp in passes:
		parts = [
			vp.visitor_full_name or "Unnamed",
			f"({vp.visitor_type})" if vp.visitor_type else None,
			vp.mobile_number,
		]
		summary = " • ".join([p for p in parts if p])
		frappe.db.set_value("Visitor Pass", vp.name, "visitor_summary", summary, update_modified=False)
