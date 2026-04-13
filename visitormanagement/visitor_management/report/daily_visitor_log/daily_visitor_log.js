// Copyright (c) 2026, Harthesh and contributors
// For license information, please see license.txt

frappe.query_reports["Daily Visitor Log"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "visitor_type",
			label: __("Visitor Type"),
			fieldtype: "Select",
			options: "\nContractor\nCandidate\nCustomer\nSupplier\nVIP",
		},
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: "\nApproved\nChecked-In\nChecked-Out\nCancelled",
		},
		{
			fieldname: "host",
			label: __("Host"),
			fieldtype: "Link",
			options: "Employee",
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "status" && data) {
			const colour_map = {
				Approved: "blue", "Checked-In": "green", "Checked-Out": "grey",
				Cancelled: "red", Draft: "orange",
			};
			const colour = colour_map[data.status] || "grey";
			return `<span class="indicator-pill ${colour}">${value || ""}</span>`;
		}
		if (column.fieldname === "visitor_type" && data) {
			const colour_map = {
				Contractor: "orange", Candidate: "purple", Customer: "green",
				Supplier: "blue", VIP: "red",
			};
			const colour = colour_map[data.visitor_type] || "grey";
			return `<span class="indicator-pill ${colour}">${value || ""}</span>`;
		}
		return value;
	},
};
