# Copyright (c) 2026, Harthesh and contributors
# For license information, please see license.txt

import frappe


def execute(filters=None):
	filters = filters or {}

	columns = [
		{"label": "Visitor Pass", "fieldname": "visitor_pass", "fieldtype": "Link", "options": "Visitor Pass", "width": 150},
		{"label": "Visit Date", "fieldname": "visit_date", "fieldtype": "Date", "width": 110},
		{"label": "Visitor", "fieldname": "visitor_name", "fieldtype": "Data", "width": 170},
		{"label": "Type", "fieldname": "visitor_type", "fieldtype": "Data", "width": 100},
		{"label": "Host", "fieldname": "host", "fieldtype": "Link", "options": "Employee", "width": 140},
		{"label": "Status", "fieldname": "compliance_status", "fieldtype": "Data", "width": 120},
		{"label": "Score", "fieldname": "score", "fieldtype": "Percent", "width": 90},
		{"label": "No Show", "fieldname": "no_show", "fieldtype": "Check", "width": 80},
		{"label": "Verification", "fieldname": "verification_duration", "fieldtype": "Duration", "width": 120},
		{"label": "Missing Requirements", "fieldname": "missing_requirements", "fieldtype": "Small Text", "width": 280},
	]

	conditions = []
	values = {}

	if filters.get("from_date"):
		conditions.append("cc.visit_date >= %(from_date)s")
		values["from_date"] = filters["from_date"]

	if filters.get("to_date"):
		conditions.append("cc.visit_date <= %(to_date)s")
		values["to_date"] = filters["to_date"]

	if filters.get("compliance_status"):
		conditions.append("cc.compliance_status = %(compliance_status)s")
		values["compliance_status"] = filters["compliance_status"]

	if filters.get("visitor_type"):
		conditions.append("cc.visitor_type = %(visitor_type)s")
		values["visitor_type"] = filters["visitor_type"]

	where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
	data = frappe.db.sql(
		f"""
		SELECT
			cc.visitor_pass,
			cc.visit_date,
			vp.visitor_full_name AS visitor_name,
			cc.visitor_type,
			cc.host,
			cc.compliance_status,
			cc.score,
			cc.no_show,
			cc.verification_duration,
			cc.missing_requirements
		FROM `tabCompliance Check` cc
		LEFT JOIN `tabVisitor Pass` vp ON vp.name = cc.visitor_pass
		{where_clause}
		ORDER BY cc.visit_date DESC, cc.modified DESC
		""",
		values,
		as_dict=True,
	)

	status_counts = {}
	total_duration = 0
	duration_rows = 0
	for row in data:
		status_counts[row.compliance_status] = status_counts.get(row.compliance_status, 0) + 1
		if row.verification_duration:
			total_duration += row.verification_duration
			duration_rows += 1

	report_summary = [
		{
			"value": len(data),
			"label": "Compliance Checks",
			"indicator": "Blue",
		},
		{
			"value": status_counts.get("Non-Compliant", 0) + status_counts.get("Needs Review", 0),
			"label": "Exceptions",
			"indicator": "Red",
		},
		{
			"value": status_counts.get("No Show", 0),
			"label": "No Shows",
			"indicator": "Orange",
		},
	]

	if duration_rows:
		report_summary.append(
			{
				"value": round(total_duration / duration_rows, 2),
				"label": "Avg Verification (sec)",
				"indicator": "Green",
			}
		)

	chart = {
		"data": {
			"labels": list(status_counts.keys()),
			"datasets": [{"name": "Checks", "values": list(status_counts.values())}],
		},
		"type": "donut",
	}

	return columns, data, None, chart, report_summary
