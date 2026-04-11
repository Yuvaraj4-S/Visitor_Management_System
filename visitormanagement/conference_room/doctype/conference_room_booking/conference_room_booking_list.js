frappe.listview_settings["Conference Room Booking"] = {
	get_indicator(doc) {
		const status_map = {
			"Draft": [__("Draft"), "grey", "status,=,Draft"],
			"Pending Approval": [__("Pending Approval"), "orange", "status,=,Pending Approval"],
			"Approved": [__("Approved"), "green", "status,=,Approved"],
			"Rejected": [__("Rejected"), "red", "status,=,Rejected"],
			"Cancelled": [__("Cancelled"), "red", "status,=,Cancelled"],
		};
		return status_map[doc.status] || [__("Draft"), "grey", "status,=,Draft"];
	},
};
