import frappe
from frappe.utils import date_diff, getdate

# Contractor "Multi-day pass / Valid until" becomes the generic recurring visit.
# The legacy fields are kept (and kept in sync by Visitor Pass.validate).


def execute():
	if not frappe.db.has_column("Visitor Pass", "is_recurring"):
		return
	rows = frappe.get_all(
		"Visitor Pass",
		filters={"multi_day_pass": 1, "pass_valid_until": ["is", "set"], "is_recurring": 0},
		fields=["name", "visit_date", "pass_valid_until"],
	)
	for row in rows:
		start = row.visit_date or row.pass_valid_until
		end = row.pass_valid_until
		if getdate(end) < getdate(start):
			continue
		frappe.db.set_value(
			"Visitor Pass",
			row.name,
			{
				"is_recurring": 1,
				"recurring_start_date": start,
				"recurring_end_date": end,
				"recurring_days": date_diff(end, start) + 1,
			},
			update_modified=False,
		)
