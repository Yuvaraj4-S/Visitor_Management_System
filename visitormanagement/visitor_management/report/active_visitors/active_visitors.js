// For license information, please see license.txt

frappe.query_reports["Active Visitors"] = {
	filters: [
		{
			fieldname: "visitor_type",
			label: __("Visitor Type"),
			fieldtype: "Link",
			options: "Visitor Type",
		},
		{
			fieldname: "gate_name",
			label: __("Gate"),
			fieldtype: "Link",
			options: "Visitor Gate",
		},
		{
			fieldname: "host",
			label: __("Host"),
			fieldtype: "Link",
			options: "Employee",
			// Employee is an HRMS DocType: search it through this app's own query, and
			// skip core's per-pick check, which needs Select on Employee.
			get_query: () => ({
				query: "visitormanagement.visitor_management.link_details.link_query",
			}),
			ignore_link_validation: 1,
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "visitor_type" && data) {
			const colour_map = {
				Contractor: "orange",
				Candidate: "purple",
				Customer: "green",
				Supplier: "blue",
				VIP: "red",
			};
			const colour = colour_map[data.visitor_type] || "grey";
			return `<span class="indicator-pill ${colour}">${value || ""}</span>`;
		}
		if (column.fieldname === "item_verification_status" && data) {
			const colour =
				{ "All Verified": "green", Partial: "orange", Pending: "red" }[
					data.item_verification_status
				] || "grey";
			return `<span class="indicator-pill ${colour}">${value || ""}</span>`;
		}
		return value;
	},
};
