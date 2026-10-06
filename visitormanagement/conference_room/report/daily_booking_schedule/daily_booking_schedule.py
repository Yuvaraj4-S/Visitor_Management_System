# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import escape_html


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

	conditions = "WHERE crb.docstatus < 2 AND crb.status NOT IN ('Cancelled')"
	values = {}

	if filters.get("date"):
		conditions += " AND crb.booking_date = %(date)s"
		values["date"] = filters["date"]

	if filters.get("conference_room"):
		conditions += " AND crb.conference_room = %(conference_room)s"
		values["conference_room"] = filters["conference_room"]

	# Raw SQL bypasses permission_query_conditions, so the booking scope is
	# re-applied here — the same rule as the calendar (get_booking_events): every
	# booking stays on the schedule so its slot reads as taken, but only the
	# booking's owner, its booked_by employee or a room overseer sees what the
	# meeting is and who is in it. Everyone else sees "Busy".
	data = frappe.db.sql(
		"""
		SELECT
			crb.name, crb.conference_room, crb.meeting_title,
			crb.start_time, crb.end_time, crb.duration_hours,
			crb.meeting_type, crb.expected_attendees,
			crb.booked_by, emp.employee_name AS booked_by_name, crb.status,
			("""
		+ _booking_scope("crb")
		+ """) AS is_visible
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
	for row in data:
		if not row.pop("is_visible"):
			row.update(
				name=None,
				meeting_title=_("Busy"),
				meeting_type=None,
				expected_attendees=None,
				booked_by=None,
				booked_by_name=None,
			)
		# The report grid renders a Data cell as HTML. Frappe strips scripts when
		# a record is saved but keeps plain markup, so a title typed as
		# "<b>Board</b><img src=x>" was drawn as bold text and an image. What a
		# person typed is shown as text.
		for fieldname in ("meeting_title", "booked_by_name"):
			if row.get(fieldname):
				row[fieldname] = escape_html(row[fieldname])
	return columns, data


def _booking_scope(alias):
	"""SQL condition: is this booking row the caller's to see in full? ("1" for overseers)."""
	from visitormanagement.permissions import get_conference_room_booking_permission_query_conditions

	condition = get_conference_room_booking_permission_query_conditions()
	if not condition:
		return "1"
	return condition.replace("`tabConference Room Booking`", f"`{alias}`")
