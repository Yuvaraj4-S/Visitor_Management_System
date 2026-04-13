// Copyright (c) 2026, Harthesh and contributors
// For license information, please see license.txt

frappe.query_reports["Monthly Hospitality Cost"] = {
	filters: [
		{
			fieldname: "month",
			label: "Month (any date in month)",
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
	],
};
