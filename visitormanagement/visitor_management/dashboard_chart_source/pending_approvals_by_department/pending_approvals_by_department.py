# For license information, please see license.txt
"""Pending Visitor Pass counts grouped by host department.

Was a stock "Group By" Dashboard Chart (`group_by_based_on: host_department`).
A stock Group By chart sends whatever value a row's grouping field holds
straight to the frappe-charts x-axis — including a blank one, which renders as
the literal string "null" on screen. That was not a rare edge case here: at
the time this was written, all 15 Visitor Pass rows pending approval had no
`host_department` set at all, so "null" was not one bar among several, it was
the *only* bar on the chart, and every one of the pending approvals it counted
belonged to it.

Two ways to remove that: exclude rows with a blank department, or keep them
and label the blank bucket honestly. Excluding was rejected — with `host_department`
this sparse, excluding blanks would have made the chart quietly empty instead
of wrong, which erases a genuine, site-wide data-completeness signal (host
department is not being captured on the pending-approval path) instead of
surfacing it. So this groups in Python and labels the blank bucket
"Unassigned" rather than hiding or mislabelling it.

Also switches the filter from `status = "Pending Approval"` to
`workflow_state like "Pending%"` — `workflow_state` is the authoritative
approval field (`status` is derived from it in
`visitor_pass._sync_status_with_workflow`), the same field the "Pending Visitor
Approvals" number card and the "Pending Approvals by Type" chart use. Reading
two different fields for "pending" is how this chart and that number card were
once able to disagree (14 vs 15) about the same site.

Uses `frappe.get_list` (permission-aware, never `frappe.get_all` or raw SQL),
matching every other Visitor Pass read in this app.
"""

import frappe
from frappe import _
from frappe.utils.dashboard import cache_source


@frappe.whitelist()
@cache_source
def get_data(
	chart_name: str | None = None,
	chart: str | None = None,
	no_cache: str | None = None,
	filters: str | None = None,
	from_date: str | None = None,
	to_date: str | None = None,
	timespan: str | None = None,
	time_interval: str | None = None,
	heatmap_year: str | None = None,
) -> dict[str, list]:
	rows = frappe.get_list(
		"Visitor Pass",
		filters=[
			["workflow_state", "like", "Pending%"],
			["docstatus", "<", 2],
		],
		fields=["host_department"],
	)

	counts: dict[str, int] = {}
	for row in rows:
		key = row.host_department or _("Unassigned")
		counts[key] = counts.get(key, 0) + 1

	if not counts:
		# Nothing pending at all (e.g. Security, which cannot see any pending
		# Visitor Pass) — return an empty dict so the widget renders the same
		# plain "No Data" placeholder every other empty chart on this workspace
		# uses, instead of an empty box with no axis and no explanation.
		return {}

	ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
	labels = [item[0] for item in ordered]
	values = [item[1] for item in ordered]

	return {
		"labels": labels,
		"datasets": [{"name": _("Pending Approvals by Department"), "values": values}],
	}
