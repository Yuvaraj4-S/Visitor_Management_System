// For license information, please see license.txt

frappe.query_reports["Daily Hospitality Schedule"] = {
	filters: [
		{
			fieldname: "date",
			label: "Date",
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "service",
			label: "Service",
			fieldtype: "Select",
			options: "All\nCab\nHotel\nFactory Tour\nBuggy\nGreeting",
			default: "All",
		},
	],
};
