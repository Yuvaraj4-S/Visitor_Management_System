# For license information, please see license.txt

import frappe
from frappe import _

from visitormanagement.conference_room.doctype.conference_room_booking.conference_room_booking import (
	NON_BLOCKING_STATUSES,
)
from visitormanagement.permissions import get_conference_room_booking_permission_query_conditions


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
			"label": _("Duration (hrs)"),
			"fieldname": "duration_hours",
			"fieldtype": "Float",
			"width": 95,
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

	filters = filters or {}
	# The table is referenced by its full name (no alias) because the row-scope
	# condition from permissions.py is written against `tabConference Room Booking`.
	crb = "`tabConference Room Booking`"
	# A cancelled or rejected booking holds no slot (the same rule as the clash
	# check and the calendar), so it is not part of the day's schedule.
	conditions = f"WHERE {crb}.docstatus < 2 AND ifnull({crb}.status, '') NOT IN %(free)s"
	values = {"free": NON_BLOCKING_STATUSES}

	if filters.get("date"):
		conditions += f" AND {crb}.booking_date = %(date)s"
		values["date"] = filters["date"]

	if filters.get("conference_room"):
		conditions += f" AND {crb}.conference_room = %(conference_room)s"
		values["conference_room"] = filters["conference_room"]

	# Raw SQL bypasses the Conference Room Booking row scoping, and Employee can
	# run this report. Mask like the calendar (get_booking_events) does: every
	# booking stays listed so the slot reads as taken, but the meeting title is
	# replaced unless the viewer may read that booking (owner, booked_by or an
	# overseer — the same condition the list view applies).
	scope = get_conference_room_booking_permission_query_conditions()
	can_read_expr = "1" if scope is None else f"CASE WHEN {scope} THEN 1 ELSE 0 END"

	data = frappe.db.sql(
		f"""
		SELECT
			{crb}.name, {crb}.conference_room, {crb}.meeting_title,
			{crb}.start_time, {crb}.end_time, {crb}.duration_hours,
			{crb}.meeting_type, {crb}.expected_attendees,
			{crb}.booked_by, emp.employee_name AS booked_by_name, {crb}.status,
			{can_read_expr} AS can_read
		FROM {crb}
		LEFT JOIN `tabEmployee` emp ON emp.name = {crb}.booked_by
		"""
		+ conditions
		+ f"""
		ORDER BY {crb}.conference_room, {crb}.start_time
		""",
		values,
		as_dict=True,
	)

	busy = _("Busy")
	for row in data:
		if not row.pop("can_read"):
			row["meeting_title"] = busy
	return columns, data
