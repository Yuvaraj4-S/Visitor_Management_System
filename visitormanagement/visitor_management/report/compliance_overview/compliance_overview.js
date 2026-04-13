// Copyright (c) 2026, Harthesh and contributors
// For license information, please see license.txt

frappe.query_reports["Compliance Overview"] = {
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
			fieldname: "compliance_status",
			label: __("Compliance Status"),
			fieldtype: "Select",
			options: "\nCompliant\nNeeds Review\nNon-Compliant\nNo Show",
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "compliance_status" && data) {
			const colour_map = {
				Compliant: "green", "Needs Review": "orange",
				"Non-Compliant": "red", "No Show": "grey",
			};
			const colour = colour_map[data.compliance_status] || "grey";
			return `<span class="indicator-pill ${colour}">${value || ""}</span>`;
		}
		if (column.fieldname === "score" && data && data.score !== undefined && data.score !== null) {
			let colour = "red";
			if (data.score >= 85) colour = "green";
			else if (data.score >= 60) colour = "orange";
			return `<span style="color:${colour === "green" ? "#28a745" : colour === "orange" ? "#fd7e14" : "#dc3545"};font-weight:600">${value}</span>`;
		}
		return value;
	},
};
