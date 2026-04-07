// Copyright (c) 2026, Harthesh and contributors
// For license information, please see license.txt

frappe.query_reports["Daily Visitor Log"] = {
	"filters": [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
		},
		{
			fieldname: "visitor_type",
			label: __("Visitor Type"),
			fieldtype: "Select",
			options: "\nVIP\nSupplier\nContractor\nCandidate\nCustomer",
		},
	]
};
