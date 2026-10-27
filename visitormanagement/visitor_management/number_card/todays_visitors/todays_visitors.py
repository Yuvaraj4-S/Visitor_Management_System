# For license information, please see license.txt
"""Custom-type source for the "Today's Visitors" Number Card.

A Document Type card can only AND its filters, so `visit_date = today` was all
it could say — a contractor on day 3 of a five-day pass is expected today but
was not counted. This adds the multi-day passes whose window covers today.

Both reads go through `frappe.get_list`, so the card respects the same row
scoping as every other Visitor Pass read in the app.
"""

import frappe
from frappe.utils import today

EXPECTED_STATUSES = ["Approved", "Items Verified", "Checked-In", "Checked-Out"]


@frappe.whitelist()
def get_data(filters: str | dict | list | None = None, **kwargs) -> dict:
	day = today()
	starting_today = frappe.get_list(
		"Visitor Pass",
		filters={"visit_date": day, "status": ["in", EXPECTED_STATUSES]},
		pluck="name",
	)
	running_today = frappe.get_list(
		"Visitor Pass",
		filters={
			"multi_day_pass": 1,
			"visit_date": ["<", day],
			"pass_valid_until": [">=", day],
			"status": ["in", EXPECTED_STATUSES],
		},
		pluck="name",
	)
	return {"value": len(set(starting_today) | set(running_today))}
