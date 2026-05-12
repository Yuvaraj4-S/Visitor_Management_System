# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	columns = get_columns()
	data = get_data(filters)
	return columns, data


def get_columns():
	return [
		{
			"label": _("Conference Room"),
			"fieldname": "conference_room",
			"fieldtype": "Link",
			"options": "Conference Room",
			"width": 180,
		},
		{
			"label": _("Total Bookings"),
			"fieldname": "total_bookings",
			"fieldtype": "Int",
			"width": 120,
		},
		{
			"label": _("Total Hours"),
			"fieldname": "total_hours",
			"fieldtype": "Float",
			"width": 120,
			"precision": 1,
		},
		{
			"label": _("Avg Duration (hrs)"),
			"fieldname": "avg_hours",
			"fieldtype": "Float",
			"width": 140,
			"precision": 1,
		},
		{
			"label": _("Internal"),
			"fieldname": "internal_count",
			"fieldtype": "Int",
			"width": 100,
		},
		{
			"label": _("External"),
			"fieldname": "external_count",
			"fieldtype": "Int",
			"width": 100,
		},
	]


def get_data(filters):
	conditions = "WHERE crb.docstatus = 1"
	values = {}

	if filters.get("from_date"):
		conditions += " AND crb.booking_date >= %(from_date)s"
		values["from_date"] = filters["from_date"]
	if filters.get("to_date"):
		conditions += " AND crb.booking_date <= %(to_date)s"
		values["to_date"] = filters["to_date"]
	if filters.get("conference_room"):
		conditions += " AND crb.conference_room = %(conference_room)s"
		values["conference_room"] = filters["conference_room"]

	return frappe.db.sql(
		"""
		SELECT
			crb.conference_room,
			COUNT(*) AS total_bookings,
			ROUND(SUM(crb.duration_hours), 1) AS total_hours,
			ROUND(AVG(crb.duration_hours), 1) AS avg_hours,
			SUM(CASE WHEN crb.meeting_type = 'Internal' THEN 1 ELSE 0 END) AS internal_count,
			SUM(CASE WHEN crb.meeting_type != 'Internal' THEN 1 ELSE 0 END) AS external_count
		FROM `tabConference Room Booking` crb
		{conditions}
		GROUP BY crb.conference_room
		ORDER BY total_hours DESC
		""".format(conditions=conditions),
		values,
		as_dict=True,
	)
