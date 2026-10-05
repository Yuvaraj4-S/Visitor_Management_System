// For license information, please see license.txt

frappe.query_reports["Missed Checkout"] = {
	filters: [
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date", default: frappe.datetime.add_days(frappe.datetime.get_today(), -30), reqd: 1 },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date", default: frappe.datetime.get_today(), reqd: 1 },
		{ fieldname: "visitor_type", label: __("Visitor Type"), fieldtype: "Select", options: "\nContractor\nCandidate\nCustomer\nSupplier\nVIP" },
		{ fieldname: "gate_name", label: __("Check-In Gate"), fieldtype: "Select", options: "\nMain Gate\nBack Gate\nVIP Entrance\nLoading Dock\nEmergency Exit" },
	],
};
