// For license information, please see license.txt

frappe.query_reports["Evacuation Roll Call"] = {
	filters: [
		{
			fieldname: "location",
			label: __("Location"),
			fieldtype: "Link",
			options: "Visitor Gate",
		},
	],
	onload(report) {
		// One click from the open report to a printed roll call: no print dialog,
		// the layout below (evacuation_roll_call.html) with a tick box per person.
		report.page
			.add_inner_button(__("Print Roll Call"), () =>
				frappe.query_report.print_report({ orientation: "Landscape" })
			)
			.addClass("btn-primary");
	},
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "location" && data) {
			return `<b>${value || ""}</b>`;
		}
		if (column.fieldname === "vip" && data && data.vip) {
			return `<span class="indicator-pill red">${__("VIP")}</span>`;
		}
		return value;
	},
};
