// Copyright (c) 2026, Harthesh and contributors
// For license information, please see license.txt

frappe.query_reports["Compliance Overview"] = {
	filters: [
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
			options: "\nContractor\nCandidate\nCustomer\nSupplier\nVIP",
		},
		{
			fieldname: "compliance_status",
			label: __("Compliance Status"),
			fieldtype: "Select",
			options: "\nCompliant\nNeeds Review\nNon-Compliant\nNo Show",
		},
	],
};
