# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, get_datetime, get_time, getdate, time_diff_in_hours, today

from visitormanagement.permissions import get_conference_room_booking_permission_query_conditions


class ConferenceRoomBooking(Document):

	def validate(self):
		self.validate_schedule()
		self.calculate_duration()
		self.validate_capacity()
		self.validate_overlap()
		self.validate_operating_hours()
		self.auto_set_service_flags()
		self._validate_visitor_pass_approved()
		self._sync_status_with_workflow()

	def _sync_status_with_workflow(self):
		"""Keep `status` in step with the workflow.

		The workflow's states carry no `update_field`, so only on_submit and
		on_cancel ever wrote `status` — leaving "Pending Approval" and "Rejected"
		unreachable even though both are declared options on the field.

		That is not cosmetic. validate_overlap excludes
		`status NOT IN ('Cancelled', 'Rejected')`, so a rejected booking whose
		status stayed "Draft" went on holding its room slot forever and the
		exclusion was dead code. `status` is also in_list_view and
		in_standard_filter, so the list showed a booking awaiting approval as
		"Draft".
		"""
		state = getattr(self, "workflow_state", None)
		if state in ("Draft", "Pending Approval", "Approved", "Rejected", "Cancelled"):
			self.status = state

	# Real-world rule: a room booking tied to a visitor cannot move to Pending
	# Approval until that visitor is confirmed. Drafts and bookings without any
	# linked visitor (purely internal meetings) are unaffected.
	def _validate_visitor_pass_approved(self):
		if not getattr(self, "visitor_pass", None):
			return
		current_state = getattr(self, "workflow_state", None) or "Draft"
		if current_state in ("Draft", "Rejected"):
			return
		vp_status = frappe.db.get_value("Visitor Pass", self.visitor_pass, "status")
		if vp_status not in ("Approved", "Items Verified", "Checked-In", "Checked-Out"):
			frappe.throw(
				_(
					"Visitor Pass {0} is currently <b>{1}</b>. Please ensure the Visitor "
					"Pass is Approved before submitting this Conference Room Booking."
				).format(self.visitor_pass, vp_status or _("Draft")),
				title=_("Approval Not Allowed"),
			)

	# -- Auto-Set Service Flags --

	def auto_set_service_flags(self):
		"""Auto-enable service flags for External/Hybrid meetings."""
		if self.meeting_type in ("External", "Hybrid"):
			self.room_cleaning_required = 1
			self.water_required = 1
			self.coffee_tea_required = 1

	def on_submit(self):
		if not self.status or self.status == "Draft":
			self.db_set("status", "Approved")

	def on_cancel(self):
		self.db_set("status", "Cancelled")

	# -- Schedule Validation ---

	def validate_schedule(self):
		if getdate(self.booking_date) < getdate(today()):
			frappe.throw(_("Cannot book a room for a past date."))

		if not self.start_time or not self.end_time:
			frappe.throw(_("Both Start Time and End Time are required."))

		if get_time(self.start_time) >= get_time(self.end_time):
			frappe.throw(_("Start Time must be before End Time."))

	# -- Duration Calculation --

	def calculate_duration(self):
		start_dt = get_datetime("{} {}".format(self.booking_date, self.start_time))
		end_dt = get_datetime("{} {}".format(self.booking_date, self.end_time))
		self.duration_hours = flt(time_diff_in_hours(end_dt, start_dt), 2)

		room = frappe.get_cached_doc("Conference Room", self.conference_room)
		duration_minutes = self.duration_hours * 60

		if room.min_booking_minutes and duration_minutes < cint(room.min_booking_minutes):
			frappe.throw(
				_("Minimum booking duration for {0} is {1} minutes.").format(
					room.room_name, room.min_booking_minutes
				)
			)
		if room.max_booking_hours and self.duration_hours > cint(room.max_booking_hours):
			frappe.throw(
				_("Maximum booking duration for {0} is {1} hours.").format(
					room.room_name, room.max_booking_hours
				)
			)

	# -- Capacity Validation ---

	def validate_capacity(self):
		if not self.expected_attendees or not self.conference_room:
			return

		room_capacity = frappe.db.get_value(
			"Conference Room", self.conference_room, "capacity"
		)
		if room_capacity and cint(self.expected_attendees) > cint(room_capacity):
			frappe.throw(
				_("Expected attendees ({0}) exceeds room capacity ({1}) for {2}.").format(
					self.expected_attendees, room_capacity, self.conference_room
				)
			)

	# -- Overlap Validation ----

	def validate_overlap(self):
		"""Reject a booking that collides with one already held on this room.

		The rule itself lives in `find_conflicting_booking` so Visitor Pass can warn
		about a clash the moment a host picks a room, instead of the clash only
		surfacing here when the pass is approved.

		`for_update=True` is what makes the rule true under load. Read-then-insert
		is a time-of-check/time-of-use race: two people booking the same room and
		slot at the same moment both find it free and both commit, which is exactly
		the double-booking this method exists to prevent. The locking read holds the
		matching range until the transaction commits, so the second booking waits
		and then sees the first.
		"""
		overlap = find_conflicting_booking(
			self.conference_room,
			self.booking_date,
			self.start_time,
			self.end_time,
			exclude=self.name,
			for_update=True,
		)

		if overlap:
			# Name the clashing meeting only to someone allowed to see it. The
			# calendar deliberately shows other people's bookings as "Busy" so a
			# room stays visibly occupied without leaking what it is for — and this
			# error handed the title straight back, so anyone could learn
			# "Board interview — CFO candidate" simply by trying to book over it.
			# The time and the booking id are enough to move your meeting.
			from visitormanagement.permissions import has_conference_room_booking_permission

			other = frappe.get_doc("Conference Room Booking", overlap[0].name)
			may_see = has_conference_room_booking_permission(other, frappe.session.user)
			what = overlap[0].meeting_title if may_see else _("another booking")

			frappe.throw(
				_("Time conflict with <b>{0}</b> ({1}: {2} - {3}). "
				  "Please choose a different time slot.").format(
					overlap[0].name,
					what,
					overlap[0].start_time,
					overlap[0].end_time,
				),
				title=_("Room Already Booked"),
			)

	# -- Operating Hours Validation --

	def validate_operating_hours(self):
		room = frappe.get_cached_doc("Conference Room", self.conference_room)

		if room.available_from and get_time(self.start_time) < get_time(room.available_from):
			frappe.throw(
				_("{0} is available from {1}. Your start time {2} is too early.").format(
					room.room_name, room.available_from, self.start_time
				)
			)
		if room.available_to and get_time(self.end_time) > get_time(room.available_to):
			frappe.throw(
				_("{0} is available until {1}. Your end time {2} is too late.").format(
					room.room_name, room.available_to, self.end_time
				)
			)



# -- Whitelisted API --

@frappe.whitelist()
def get_available_rooms(booking_date, start_time, end_time, min_capacity=0, exclude_booking=None):
	"""Return rooms available for the given slot, sorted smallest-suitable-first."""
	if not booking_date or not start_time or not end_time:
		return []

	min_cap = cint(min_capacity) or 1

	# get_list, not get_all: get_all bypasses the permission layer entirely, so
	# the room master was readable by anyone who could reach the endpoint.
	rooms = frappe.get_list(
		"Conference Room",
		filters={"is_active": 1, "capacity": [">=", min_cap]},
		fields=["name", "room_name", "capacity", "location", "floor", "room_type"],
		order_by="capacity asc",
	)

	exclude_clause = ""
	params = {
		"date": booking_date,
		"start_time": start_time,
		"end_time": end_time,
	}

	if exclude_booking:
		exclude_clause = "AND name != %(exclude)s"
		params["exclude"] = exclude_booking

	booked = frappe.db.sql_list(
		"""
		SELECT DISTINCT conference_room
		FROM `tabConference Room Booking`
		WHERE booking_date = %(date)s
		  AND docstatus < 2
		  AND status NOT IN ('Cancelled')
		  AND (start_time < %(end_time)s AND end_time > %(start_time)s)
		"""
		+ exclude_clause,
		params,
	)

	return [r for r in rooms if r.name not in booked]


@frappe.whitelist()
def get_room_schedule(conference_room, booking_date):
	"""Get all bookings for a room on a given date."""
	# Returns meeting_title and booked_by, so it must respect whatever the site
	# decides Conference Room Booking visibility should be. get_all ignored that
	# and handed every meeting title on any room to any authenticated caller.
	return frappe.get_list(
		"Conference Room Booking",
		filters={
			"conference_room": conference_room,
			"booking_date": booking_date,
			"docstatus": ["<", 2],
			"status": ["not in", ["Cancelled"]],
		},
		fields=[
			"name", "meeting_title", "start_time", "end_time",
			"booked_by", "meeting_type", "expected_attendees", "status",
		],
		order_by="start_time asc",
	)


@frappe.whitelist()
def get_booking_events(start, end, filters=None):
	"""Calendar view event source."""
	# Raw SQL below bypasses both DocPerm and any permission_query_conditions,
	# so the check has to be explicit — otherwise a future decision to scope
	# bookings would be silently undone by this one endpoint.
	frappe.has_permission("Conference Room Booking", "read", throw=True)

	# That scoping decision has now been made (hooks.py registers
	# permission_query_conditions/has_permission for this doctype), and the
	# doc-less check above cannot see it — it only asks "may this user read the
	# doctype at all", never "which rows". Unscoped, this endpoint handed every
	# employee every booking's `meeting_title` company-wide, so "Board interview
	# — CFO candidate" was readable by anyone who opened the calendar.
	#
	# The fix masks rather than filters, deliberately. A calendar that hid other
	# people's bookings would show their slots as free and invite double-booking,
	# which is the whole job this view does. So every booking is still returned —
	# the slot stays visibly busy — but the title collapses to "Busy" unless the
	# viewer owns the booking, is its `booked_by` employee, or is an overseer.
	scope = get_conference_room_booking_permission_query_conditions()
	title_expr = "meeting_title" if scope is None else f"CASE WHEN {scope} THEN meeting_title ELSE 'Busy' END"

	cond = ""
	values = {"start": start, "end": end}

	if filters:
		if isinstance(filters, str):
			filters = json.loads(filters)
		if isinstance(filters, dict) and filters.get("conference_room"):
			cond = "AND conference_room = %(room)s"
			values["room"] = filters["conference_room"]

	return frappe.db.sql(
		"""
		SELECT
			name, """
		+ title_expr
		+ """ AS meeting_title,
			TIMESTAMP(booking_date, start_time) AS `start`,
			TIMESTAMP(booking_date, end_time) AS `end`,
			conference_room, meeting_type, status,
			0 AS allDay
		FROM `tabConference Room Booking`
		WHERE docstatus < 2
		  AND status NOT IN ('Cancelled')
		  AND booking_date BETWEEN %(start)s AND %(end)s
		"""
		+ cond
		+ """
		ORDER BY booking_date, start_time
		""",
		values,
		as_dict=True,
	)


def find_conflicting_booking(room, booking_date, start_time, end_time, exclude=None, for_update=False):
	"""The booking already holding this room in this window, or None.

	Extracted so the rule lives in one place. `Conference Room Booking` enforces
	it on its own save, and `Visitor Pass` calls it the moment a host picks a
	room — before this, the room was only actually booked when the pass was
	approved, so a clash surfaced to the approver rather than to the person who
	chose the room, long after they could easily change it.

	Times compare strictly (`<` / `>`), so back-to-back bookings (10-11 then
	11-12) do not collide — a rule a real office depends on.

	`for_update` is used by the booking's own validation, where the locking read
	closes a time-of-check/time-of-use race between two simultaneous bookings.
	The advisory check on Visitor Pass passes False: it is a courtesy warning at
	pick time, not the authority, and must not hold row locks on an unrelated
	doctype's save.
	"""
	return frappe.db.sql(
		"""
		SELECT name, meeting_title, start_time, end_time
		FROM `tabConference Room Booking`
		WHERE conference_room = %(room)s
		  AND booking_date = %(date)s
		  AND name != %(self_name)s
		  AND docstatus < 2
		  AND status NOT IN ('Cancelled', 'Rejected')
		  AND (start_time < %(end_time)s AND end_time > %(start_time)s)
		LIMIT 1
		"""
		+ ("FOR UPDATE" if for_update else ""),
		{
			"room": room,
			"date": booking_date,
			"start_time": start_time,
			"end_time": end_time,
			"self_name": exclude or "NEW",
		},
		as_dict=True,
	)
