# For license information, please see license.txt
"""Visitor Pass counts per Visitor Type, including types with zero passes.

A "Group By" Dashboard Chart runs `frappe.get_list(doctype, group_by=...)` — a SQL
GROUP BY over existing rows. A Visitor Type with no Visitor Pass rows can never
produce a group, so it silently drops off the chart instead of showing a zero.
This source starts from the Visitor Type master (not from Visitor Pass) and
zero-fills, so every active type is represented — a true zero reads as a zero,
not as "missing".

`get_visitor_pass_counts_by_type()` is shared by this module and the sibling
"Pending Visitor Passes by Type" source (visitor_passes_by_type_pending) so the
counting query is written once; each source's `get_data` only supplies its own
status filter.
"""

import frappe
from frappe import _
from frappe.utils.dashboard import cache_source


def get_visitor_pass_counts_by_type(status: str | None = None) -> tuple[list[str], list[int]]:
	"""Count Visitor Pass rows per active Visitor Type, zero-filled and sorted.

	Both queries here go through `frappe.get_list` (never `frappe.get_all` or raw
	SQL), so this respects the same permissions every other Visitor Pass read in
	this app does: the row-level scoping from
	`visitormanagement.permissions.get_visitor_pass_permission_query_conditions`
	(wired in hooks.py) applies here too. Different roles legitimately see
	different counts — e.g. Security only sees passes in gate-relevant statuses,
	so a role with nothing visible correctly gets all-zero counts here rather
	than being special-cased.

	:param status: optional Visitor Pass `status` to filter on (e.g. "Pending
		Approval"). Omit for an all-status count.
	:return: (labels, values) — active Visitor Type names and their matching
		Visitor Pass counts, sorted by count descending, ties broken by name.
	"""
	visitor_types = frappe.get_list("Visitor Type", filters={"is_active": 1}, pluck="name")

	# Exclude cancelled documents — the same convention the stock Dashboard Chart
	# dispatcher (frappe.desk.doctype.dashboard_chart.dashboard_chart.get) applies
	# to every chart via `filters.append([doctype, "docstatus", "<", 2])`. Visitor
	# Pass is submittable (is_submittable=1), so this matters here too.
	filters = [["visitor_type", "in", visitor_types], ["docstatus", "<", 2]]
	if status:
		filters.append(["status", "=", status])

	counted = frappe.get_list(
		"Visitor Pass",
		filters=filters,
		group_by="visitor_type",
		fields=["visitor_type as name", {"COUNT": "name", "as": "count"}],
	)
	count_by_type = {row.name: row.get("count") or 0 for row in counted}

	# Zero-fill: every active Visitor Type appears even if it has no matching
	# Visitor Pass rows (e.g. "Researcher" — this is the whole point of moving
	# off "Group By", which cannot represent a zero-row group at all).
	rows = [(visitor_type, count_by_type.get(visitor_type, 0)) for visitor_type in visitor_types]
	rows.sort(key=lambda row: (-row[1], row[0]))

	labels = [row[0] for row in rows]
	values = [row[1] for row in rows]
	return labels, values


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
	labels, values = get_visitor_pass_counts_by_type()

	return {
		"labels": labels,
		"datasets": [{"name": _("Visitors by Type"), "values": values}],
	}
