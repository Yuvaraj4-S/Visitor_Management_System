# For license information, please see license.txt
"""Visitors who left without checking out and were checked out automatically."""

import frappe
from frappe import _
from frappe.utils import add_days, getdate, nowdate


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{"label": _("Security Log"), "fieldname": "security_log", "fieldtype": "Link", "options": "Security Log", "width": 150},
		{"label": _("Visitor Pass"), "fieldname": "visitor_pass", "fieldtype": "Link", "options": "Visitor Pass", "width": 140},
		{"label": _("Visitor Name"), "fieldname": "visitor_name", "fieldtype": "Data", "width": 170},
		{"label": _("Visitor Type"), "fieldname": "visitor_type", "fieldtype": "Data", "width": 105},
		{"label": _("Host"), "fieldname": "host", "fieldtype": "Link", "options": "Employee", "width": 120},
		{"label": _("Host Name"), "fieldname": "host_name", "fieldtype": "Data", "width": 150},
		{"label": _("Visit Date"), "fieldname": "visit_date", "fieldtype": "Date", "width": 100},
		{"label": _("Check-In Time"), "fieldname": "check_in_time", "fieldtype": "Datetime", "width": 160},
		{"label": _("Check-In Gate"), "fieldname": "check_in_gate", "fieldtype": "Data", "width": 120},
		{"label": _("Expected Checkout"), "fieldname": "expected_checkout", "fieldtype": "Time", "width": 120},
		{"label": _("Auto Checkout Time"), "fieldname": "auto_checkout_time", "fieldtype": "Datetime", "width": 160},
	]


def get_data(filters):
	from_date = getdate(filters.from_date or add_days(nowdate(), -30))
	to_date = getdate(filters.to_date or nowdate())
	conditions = ["sl.is_auto_checkout = 1", "sl.event_type = 'Check-Out'", "date(sl.check_out_date_time) between %(from_date)s and %(to_date)s"]
	values = {"from_date": from_date, "to_date": to_date}
	if filters.visitor_type:
		conditions.append("vp.visitor_type = %(visitor_type)s")
		values["visitor_type"] = filters.visitor_type
	rows = frappe.db.sql(
		f"""
		select sl.name as security_log, sl.visitor_pass, vp.visitor_full_name as visitor_name,
			vp.visitor_type, vp.person_to_visit as host, emp.employee_name as host_name,
			vp.visit_date, vp.expected_checkout, sl.check_out_date_time as auto_checkout_time
		from `tabSecurity Log` sl
		join `tabVisitor Pass` vp on vp.name = sl.visitor_pass
		left join `tabEmployee` emp on emp.name = vp.person_to_visit
		where {" and ".join(conditions)}
		order by sl.check_out_date_time desc
		""",
		values,
		as_dict=True,
	)
	for row in rows:
		checkin = frappe.db.get_value(
			"Security Log",
			{"visitor_pass": row.visitor_pass, "event_type": "Check-In", "check_in_date_time": ["<=", row.auto_checkout_time]},
			["check_in_date_time", "gate_name"],
			order_by="check_in_date_time desc",
			as_dict=True,
		)
		row.check_in_time = checkin.check_in_date_time if checkin else None
		row.check_in_gate = checkin.gate_name if checkin else None
	if filters.gate_name:
		rows = [r for r in rows if r.check_in_gate == filters.gate_name]
	return rows
