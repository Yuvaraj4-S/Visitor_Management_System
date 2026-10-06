frappe.provide("frappe.dashboards.chart_sources");

frappe.dashboards.chart_sources["Pending Visitor Passes by Type"] = {
	method: "visitormanagement.visitor_management.dashboard_chart_source.pending_visitor_passes_by_type.pending_visitor_passes_by_type.get_data",
	filters: [],
};
