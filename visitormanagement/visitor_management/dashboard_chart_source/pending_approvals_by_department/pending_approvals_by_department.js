frappe.provide("frappe.dashboards.chart_sources");

frappe.dashboards.chart_sources["Pending Approvals by Department"] = {
	method: "visitormanagement.visitor_management.dashboard_chart_source.pending_approvals_by_department.pending_approvals_by_department.get_data",
	filters: [],
};
