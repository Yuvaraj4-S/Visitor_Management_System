# For license information, please see license.txt
"""Pending-approval Visitor Pass counts per Visitor Type, zero-filled.

Same shape as the sibling "Visitor Passes by Type" source
(visitor_passes_by_type), restricted to `status = "Pending Approval"`. The
counting query itself is not duplicated here — both sources call the one
`get_visitor_pass_counts_by_type()` helper defined in
visitor_passes_by_type.py, passing their own status filter.
"""

import frappe
from frappe import _
from frappe.utils.dashboard import cache_source

from visitormanagement.visitor_management.dashboard_chart_source.visitor_passes_by_type.visitor_passes_by_type import (
	get_visitor_pass_counts_by_type,
)


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
	labels, values = get_visitor_pass_counts_by_type(status="Pending Approval")

	return {
		"labels": labels,
		"datasets": [{"name": _("Pending Approvals by Type"), "values": values}],
	}
