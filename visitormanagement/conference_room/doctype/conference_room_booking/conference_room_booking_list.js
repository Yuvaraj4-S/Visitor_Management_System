frappe.listview_settings["Conference Room Booking"] = {
	get_indicator(doc) {
		const status_map = {
			"Draft": [__("Draft"), "grey", "status,=,Draft"],
			"Approved": [__("Approved"), "green", "status,=,Approved"],
			"Cancelled": [__("Cancelled"), "red", "status,=,Cancelled"],
		};
		return status_map[doc.status] || [__("Draft"), "grey", "status,=,Draft"];
	},
};
