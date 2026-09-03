# For license information, please see license.txt
"""Pending-approval Visitor Pass counts per Visitor Type, zero-filled.

Same shape as the sibling "Visitor Passes by Type" source
(visitor_passes_by_type), restricted to `workflow_state like "Pending%"` — the
authoritative approval field (`status` is derived from it; see
`visitor_pass._sync_status_with_workflow`). The counting query itself is not
duplicated here — both sources call the one `get_visitor_pass_counts_by_type()`
helper defined in visitor_passes_by_type.py, passing their own workflow_state
filter.

Zero-filling every Visitor Type is right when SOME types have pending passes
and others genuinely have none (a 0 bar next to real bars is informative). It
looks broken when EVERY type is zero — a role with nothing pending draws a
full 8-category axis with not one visible bar, while the sibling number card
and every other empty chart on this workspace show a plain "No Data"
placeholder instead. So when nothing at all is pending, this returns an empty
dict — Frappe's own dashboard widget already renders a "No Data" state for
that (see `frappe.public.js.widgets.chart_widget`: `if (!this.data ...)`).
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
	labels, values = get_visitor_pass_counts_by_type(workflow_state_like="Pending%")

	if not any(values):
		return {}

	return {
		"labels": labels,
		"datasets": [{"name": _("Pending Approvals by Type"), "values": values}],
	}
