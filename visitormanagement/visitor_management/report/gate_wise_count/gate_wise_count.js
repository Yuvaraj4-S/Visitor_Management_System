// For license information, please see license.txt

frappe.query_reports["Gate Wise Count"] = {
	filters: [
		{
			fieldname: "date",
			label: __("Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "pending_verify" && data && (data.pending_verify || 0) > 0) {
			return `<span class="indicator-pill orange">${value}</span>`;
		}
		if (column.fieldname === "inside" && data && (data.inside || 0) > 0) {
			return `<span style="color:#007bff;font-weight:600">${value}</span>`;
		}
		return value;
	},
};
