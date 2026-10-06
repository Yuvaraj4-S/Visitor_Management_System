// For license information, please see license.txt

frappe.query_reports["Visitor Identity Match Report"] = {
	filters: [
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			reqd: 1,
			// 30 days, not the old 3-month default: the query is O(rows) to fetch and
			// O(k^2) per duplicate-key group to pair-match, so a wide default range is
			// how this report used to OOM the worker. reqd is a UI convenience only —
			// the real boundary is enforced server-side in execute() regardless of what
			// reaches this filter.
			default: frappe.datetime.add_days(frappe.datetime.get_today(), -30),
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			reqd: 1,
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "visitor_type",
			label: __("Primary Visitor Type"),
			fieldtype: "Link",
			options: "Visitor Type",
		},
		{
			fieldname: "matched_visitor_type",
			label: __("Matched Visitor Type"),
			fieldtype: "Link",
			options: "Visitor Type",
		},
		{
			fieldname: "match_scope",
			label: __("Type Comparison"),
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
