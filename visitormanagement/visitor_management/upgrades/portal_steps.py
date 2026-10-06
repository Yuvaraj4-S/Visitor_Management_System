"""Upgrade steps for the portal, invitations and conference rooms.

Run by `upgrades.run_all()` on install and on every migrate, so everything here
is idempotent: each step only touches rows that still need it and prints a line
only when it changed something.
"""

import frappe
from frappe.utils import get_time

from visitormanagement.conference_room.doctype.conference_room.conference_room import (
	DEFAULT_AVAILABLE_FROM,
	DEFAULT_AVAILABLE_TO,
)
from visitormanagement.visitor_management.doctype.visitor_invitation.visitor_invitation import (
	expire_due_invitations,
)


def run():
	_expire_due_invitations()
	_fill_invitation_host_names()
	_repair_room_opening_hours()


def _expire_due_invitations():
	"""Catch up on invitations that ran out before anything recorded it.

	Until this release an invitation only became "Expired" if somebody opened its
	link afterwards — and even that write was undone, because the link is opened
	with a GET. The hourly job keeps them current from now on; this brings the
	backlog up to date at once.
	"""
	if not frappe.db.table_exists("Visitor Invitation"):
		return
	expired = expire_due_invitations()
	if expired:
		print(f"  Visitor Invitation: marked {expired} invitation(s) past their expiry as Expired")


def _fill_invitation_host_names():
	"""Fill the new Host Name on invitations raised before the field existed.

	The list and the form showed the host as an Employee ID ("HR-EMP-00057").
	New and re-saved invitations store the name (VisitorInvitation.validate);
	this fills the rest. Only empty names are filled, and `modified` is left
	alone: a derived column, not an edit of the invitation.
	"""
	if not frappe.db.table_exists("Visitor Invitation") or not frappe.db.has_column(
		"Visitor Invitation", "host_name"
	):
		return

	invitation = frappe.qb.DocType("Visitor Invitation")
	employee = frappe.qb.DocType("Employee")
	pending = (
		frappe.qb.from_(invitation)
		.join(employee)
		.on(employee.name == invitation.host_employee)
		.select(invitation.name, employee.employee_name)
		.where(invitation.host_name.isnull() | (invitation.host_name == ""))
		.where(employee.employee_name.isnotnull() & (employee.employee_name != ""))
	).run()
	for name, employee_name in pending:
		frappe.db.set_value("Visitor Invitation", name, "host_name", employee_name, update_modified=False)
	if pending:
		print(f"  Visitor Invitation: filled Host Name on {len(pending)} existing invitation(s)")


def _repair_room_opening_hours():
	"""Give real opening hours to rooms that were created with none.

	On Frappe 15 a Conference Room created on the server (API, import, script)
	got the creation time in BOTH hour fields instead of the form's defaults
	(see ConferenceRoom._set_opening_hours, which now prevents it). Such a room
	is open for a fraction of a second and refuses every booking.

	Only a room open for less than a minute is touched — hours nobody can have
	meant — and it gets the form's defaults; the line printed names it so the
	facility team can set the hours they actually want.
	"""
	if not frappe.db.table_exists("Conference Room"):
		return

	meta = frappe.get_meta("Conference Room")
	opens = meta.get_field("available_from").default or DEFAULT_AVAILABLE_FROM
	closes = meta.get_field("available_to").default or DEFAULT_AVAILABLE_TO

	rooms = frappe.get_all(
		"Conference Room",
		filters={"available_from": ("is", "set"), "available_to": ("is", "set")},
		fields=["name", "available_from", "available_to"],
	)
	for room in rooms:
		start, end = get_time(room.available_from), get_time(room.available_to)
		seconds = (end.hour * 3600 + end.minute * 60 + end.second + end.microsecond / 1e6) - (
			start.hour * 3600 + start.minute * 60 + start.second + start.microsecond / 1e6
		)
		if not 0 <= seconds < 60:
			continue
		frappe.db.set_value(
			"Conference Room",
			room.name,
			{"available_from": opens, "available_to": closes},
			update_modified=False,
		)
		print(
			f"  Conference Room {room.name}: opening hours were {room.available_from} to "
			f"{room.available_to} (set by the server at creation); reset to {opens} - {closes}"
		)
