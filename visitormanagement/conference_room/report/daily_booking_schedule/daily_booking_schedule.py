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
			"width": 130,
		},
		{
			"label": _("Room"),
			"fieldname": "conference_room",
			"fieldtype": "Link",
			"options": "Conference Room",
			"width": 130,
		},
		{
			"label": _("Meeting"),
			"fieldname": "meeting_title",
			"fieldtype": "Data",
			"width": 280,
		},
		{
			"label": _("Start"),
			"fieldname": "start_time",
			"fieldtype": "Time",
			"width": 80,
		},
		{
			"label": _("End"),
			"fieldname": "end_time",
			"fieldtype": "Time",
			"width": 80,
		},
		{
			"label": _("Duration"),
			"fieldname": "duration_hours",
			"fieldtype": "Float",
			"width": 85,
			"precision": 2,
		},
		{
			"label": _("Type"),
			"fieldname": "meeting_type",
			"fieldtype": "Data",
			"width": 90,
		},
		{
			"label": _("Attendees"),
			"fieldname": "expected_attendees",
			"fieldtype": "Int",
			"width": 90,
		},
		{
			# Plain Data, not a Link: this column now carries the employee's name,
			# and a Link would try to resolve that name as an Employee ID and
			# render a dead link. The ID is still on the row as `booked_by` for
			# anyone who needs it.
			"label": _("Booked By"),
			"fieldname": "booked_by_name",
			"fieldtype": "Data",
			"width": 130,
		},
		{
			"label": _("Status"),
			"fieldname": "status",
			"fieldtype": "Data",
			"width": 100,
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
			crb.booked_by, emp.employee_name AS booked_by_name, crb.status
		FROM `tabConference Room Booking` crb
		LEFT JOIN `tabEmployee` emp ON emp.name = crb.booked_by
		"""
		+ conditions
		+ """
		ORDER BY crb.conference_room, crb.start_time
		""",
		values,
		as_dict=True,
	)
	return columns, data
