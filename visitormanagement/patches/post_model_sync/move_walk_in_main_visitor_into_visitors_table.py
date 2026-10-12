import frappe

from visitormanagement.visitor_management.doctype.walk_in_visitor_request.walk_in_visitor_request import (
	MAIN_VISITOR_FIELDS,
)

CHILD = "Walk In Additional Visitor"


def execute():
	"""Walk In Visitor Request now keeps every visitor in its Visitors table (row 1 = main
	visitor). Move each existing request's main visitor (header fields) in as row 1 —
	directly, without re-validating old requests."""
	for req in frappe.get_all("Walk In Visitor Request", fields=["name", *MAIN_VISITOR_FIELDS, "visitor_pass"]):
		if not (req.visitor_full_name or "").strip():
			continue
		rows = frappe.get_all(
			CHILD,
			filters={"parent": req.name, "parenttype": "Walk In Visitor Request"},
			fields=["name", "idx", "visitor_full_name"],
			order_by="idx asc",
		)
		if rows and rows[0].visitor_full_name == req.visitor_full_name:
			continue

		# Shift existing rows down, then insert the main visitor as row 1.
		for r in rows:
			frappe.db.set_value(CHILD, r.name, "idx", r.idx + 1, update_modified=False)
		row = frappe.new_doc(CHILD)
		row.update({f: req.get(f) for f in MAIN_VISITOR_FIELDS})
		row.update(
			{
				"parent": req.name,
				"parenttype": "Walk In Visitor Request",
				"parentfield": "additional_visitors",
				"idx": 1,
				"visitor_pass": req.visitor_pass,
			}
		)
		row.db_insert()
		frappe.db.set_value(
			"Walk In Visitor Request", req.name, "number_of_visitors", len(rows) + 1, update_modified=False
		)
