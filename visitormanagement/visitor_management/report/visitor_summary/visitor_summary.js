// For license information, please see license.txt

frappe.query_reports["Visitor Summary"] = {
	filters: [
		{
			fieldname: "period",
			label: __("Period"),
			fieldtype: "Select",
			options: "Today\nThis Week\nThis Month\nLast Month\nThis Year\nTill Date\nCustom",
			default: "This Month",
			reqd: 1,
			on_change() {
				frappe.query_report.refresh();
			},
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			depends_on: "eval:doc.period=='Custom'",
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			depends_on: "eval:doc.period=='Custom'",
		},
		{
			fieldname: "group_by",
			label: __("Group By"),
			fieldtype: "Select",
			options: "Day\nMonth\nVisitor Type\nDepartment\nHost\nVisitor List",
			default: "Day",
			reqd: 1,
		},
		{
			fieldname: "visitor_type",
			label: __("Visitor Type"),
			fieldtype: "Link",
			options: "Visitor Type",
		},
		{
			fieldname: "host",
			label: __("Host"),
			fieldtype: "Link",
			options: "Employee",
		},
		{
			fieldname: "department",
			label: __("Department"),
			fieldtype: "Link",
			options: "Department",
		},
	],
};
