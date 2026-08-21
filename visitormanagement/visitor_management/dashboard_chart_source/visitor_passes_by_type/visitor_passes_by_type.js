frappe.provide("frappe.dashboards.chart_sources");

frappe.dashboards.chart_sources["Visitor Passes by Type"] = {
	method: "visitormanagement.visitor_management.dashboard_chart_source.visitor_passes_by_type.visitor_passes_by_type.get_data",
	filters: [],
};
