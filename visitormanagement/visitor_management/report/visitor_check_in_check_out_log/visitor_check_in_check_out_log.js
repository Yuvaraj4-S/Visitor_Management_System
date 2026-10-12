// For license information, please see license.txt

frappe.query_reports["Visitor Check In Check Out Log"] = {
	filters: [
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1 },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1 },
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: ["", "Completed", "Not Checked Out", "Left Late"].join("\n"),
		},
		{ fieldname: "visitor_type", label: __("Visitor Type"), fieldtype: "Link", options: "Visitor Type" },
		{ fieldname: "host", label: __("Host"), fieldtype: "Link", options: "Employee" },
		{ fieldname: "request_channel", label: __("Channel"), fieldtype: "Select", options: "\nPortal\nWalk-In\nDesk" },
		{ fieldname: "card_not_returned", label: __("ID Card Not Returned"), fieldtype: "Check" },
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "status" && data) {
			const colour = { Completed: "green", "Left Late": "orange", "Not Checked Out": "red" }[data.status];
			if (colour) value = `<span class="indicator-pill ${colour}">${value}</span>`;
		}
		if (column.fieldname === "id_card_returned" && data && data.id_card_returned === "No") {
			value = `<span style="color: var(--red-600); font-weight: 600;">${value}</span>`;
		}
		return value;
	},
};
