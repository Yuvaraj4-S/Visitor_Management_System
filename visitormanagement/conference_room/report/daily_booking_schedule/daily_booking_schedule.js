frappe.query_reports["Daily Booking Schedule"] = {
	filters: [
		{
			fieldname: "date",
			label: __("Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "conference_room",
			label: __("Conference Room"),
			fieldtype: "Link",
			options: "Conference Room",
		},
	],
};
