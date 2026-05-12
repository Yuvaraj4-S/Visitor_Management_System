// For license information, please see license.txt

frappe.query_reports["Active Visitors"] = {
	filters: [
		{
			fieldname: "visitor_type",
			label: __("Visitor Type"),
			fieldtype: "Select",
			options: "\nContractor\nCandidate\nCustomer\nSupplier\nVIP",
		},
		{
			fieldname: "gate_name",
			label: __("Gate"),
			fieldtype: "Select",
			options: "\nMain Gate\nBack Gate\nVIP Entrance\nLoading Dock\nEmergency Exit",
		},
		{
			fieldname: "host",
			label: __("Host"),
			fieldtype: "Link",
			options: "Employee",
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "visitor_type" && data) {
			const colour_map = {
				Contractor: "orange", Candidate: "purple", Customer: "green",
				Supplier: "blue", VIP: "red",
			};
			const colour = colour_map[data.visitor_type] || "grey";
			return `<span class="indicator-pill ${colour}">${value || ""}</span>`;
		}
		if (column.fieldname === "item_verification_status" && data) {
			const colour = { "All Verified": "green", Partial: "orange", Pending: "red" }[data.item_verification_status] || "grey";
			return `<span class="indicator-pill ${colour}">${value || ""}</span>`;
		}
		return value;
	},
};
