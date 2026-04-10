# Copyright (c) 2026, Harthesh and contributors
# For license information, please see license.txt

import frappe
from frappe import _


def execute(filters=None):
	columns = [
		{
			"label": _("Booking ID"),
			"fieldname": "name",
			"fieldtype": "Link",
			"options": "Conference Room Booking",
			"width": 160,
		},
		{
			"label": _("Room"),
			"fieldname": "conference_room",
			"fieldtype": "Link",
			"options": "Conference Room",
			"width": 160,
		},
		{
			"label": _("Meeting"),
			"fieldname": "meeting_title",
			"fieldtype": "Data",
			"width": 200,
		},
		{
			"label": _("Start"),
			"fieldname": "start_time",
			"fieldtype": "Time",
			"width": 100,
		},
		{
			"label": _("End"),
			"fieldname": "end_time",
			"fieldtype": "Time",
			"width": 100,
		},
		{
			"label": _("Duration"),
			"fieldname": "duration_hours",
			"fieldtype": "Float",
			"width": 100,
			"precision": 2,
		},
		{
			"label": _("Type"),
			"fieldname": "meeting_type",
			"fieldtype": "Data",
			"width": 100,
		},
		{
			"label": _("Attendees"),
			"fieldname": "expected_attendees",
			"fieldtype": "Int",
			"width": 100,
		},
		{
			"label": _("Booked By"),
			"fieldname": "booked_by",
			"fieldtype": "Link",
			"options": "Employee",
			"width": 150,
		},
		{
			"label": _("Status"),
			"fieldname": "status",
			"fieldtype": "Data",
			"width": 120,
		},
	]

	conditions = "WHERE crb.docstatus < 2 AND crb.status NOT IN ('Cancelled')"
	values = {}

	if filters.get("date"):
		conditions += " AND crb.booking_date = %(date)s"
		values["date"] = filters["date"]

	if filters.get("conference_room"):
		conditions += " AND crb.conference_room = %(conference_room)s"
		values["conference_room"] = filters["conference_room"]

	data = frappe.db.sql(
		"""
		SELECT
			crb.name, crb.conference_room, crb.meeting_title,
			crb.start_time, crb.end_time, crb.duration_hours,
			crb.meeting_type, crb.expected_attendees,
			crb.booked_by, crb.status
		FROM `tabConference Room Booking` crb
		{conditions}
		ORDER BY crb.conference_room, crb.start_time
		""".format(conditions=conditions),
		values,
		as_dict=True,
	)
	return columns, data
