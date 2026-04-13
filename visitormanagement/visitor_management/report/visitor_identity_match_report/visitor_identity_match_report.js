// Copyright (c) 2026, Harthesh and contributors
// For license information, please see license.txt

frappe.query_reports["Visitor Identity Match Report"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -3),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "visitor_type",
			label: __("Primary Visitor Type"),
			fieldtype: "Select",
			options: "\nContractor\nSupplier\nCandidate\nCustomer\nVIP",
		},
		{
			fieldname: "matched_visitor_type",
			label: __("Matched Visitor Type"),
			fieldtype: "Select",
			options: "\nContractor\nSupplier\nCandidate\nCustomer\nVIP",
		},
		{
			fieldname: "match_scope",
			label: __("Match Scope"),
			fieldtype: "Select",
			options: "\nSame Type\nDifferent Type",
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "match_scope" && data) {
			const colour = data.match_scope === "Different Type" ? "red" : "blue";
			return `<span class="indicator-pill ${colour}">${value || ""}</span>`;
		}
		return value;
	},
};
