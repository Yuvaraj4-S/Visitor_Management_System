// For license information, please see license.txt

frappe.listview_settings["Gate Entry Request"] = {
	add_fields: ["status"],
	get_indicator(doc) {
		const colours = {
			Draft: "gray",
			"Pending Approval": "orange",
			Approved: "green",
			Rejected: "red",
			Expired: "darkgrey",
		};
		return [__(doc.status), colours[doc.status] || "gray", `status,=,${doc.status}`];
	},
};
