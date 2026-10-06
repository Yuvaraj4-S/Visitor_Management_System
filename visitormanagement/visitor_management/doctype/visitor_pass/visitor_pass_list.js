// Where a pass stands, from `status`. Up to approval that is the workflow state
// under another name. After it, the gate moves the pass on — Items Verified,
// Checked-In, Checked-Out — and only `status` says so: the workflow state stays
// "Approved" throughout, which is what the list showed for a visitor who was
// inside the building, and for one who had already left.
frappe.listview_settings["Visitor Pass"] = {
	add_fields: ["status"],

	get_indicator(doc) {
		// Same colours as the form's own stage (visitor_pass.js: get_pass_stage_color).
		const colours = {
			Draft: "gray",
			"Pending Approval": "orange",
			Rejected: "red",
			Approved: "green",
			"Items Verified": "blue",
			"Checked-In": "green",
			"Checked-Out": "blue",
			Cancelled: "red",
		};
		const status = doc.status || "Draft";
		return [__(status), colours[status] || "gray", `status,=,${status}`];
	},
};
