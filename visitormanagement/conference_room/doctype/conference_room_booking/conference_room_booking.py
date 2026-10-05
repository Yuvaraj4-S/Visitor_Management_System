# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, flt, get_datetime, get_time, getdate, time_diff_in_hours, today


INTERNAL_PURPOSES = ("Team Meeting", "Training", "Board Meeting")


def purpose_for_visitor_type(visitor_type):
	return {
		"Customer": "Visitor - Customer",
		"Supplier": "Visitor - Supplier",
		"Candidate": "Interview",
	}.get(visitor_type, "Visitor - General")


class ConferenceRoomBooking(Document):

	def validate(self):
		self.apply_meeting_purpose()
		self.validate_schedule()
		self.calculate_duration()
		self.validate_capacity()
		self.validate_overlap()
		self.validate_operating_hours()
		self.validate_room_purpose()
		self.auto_set_service_flags()
		self._validate_visitor_pass_approved()

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

	# -- Meeting Purpose --

	def apply_meeting_purpose(self):
		"""Purpose drives the meeting type; visitor-linked bookings default it from the visitor type."""
		if not self.meeting_purpose and self.visitor_pass:
			visitor_type = frappe.db.get_value("Visitor Pass", self.visitor_pass, "visitor_type")
			self.meeting_purpose = purpose_for_visitor_type(visitor_type)
		if not self.meeting_purpose:
			# bookings created without a purpose (API / import / older code) keep working
			self.meeting_purpose = "Team Meeting" if self.meeting_type == "Internal" else "Visitor - General"
		if self.meeting_purpose and self.meeting_type != "Hybrid":
			self.meeting_type = "Internal" if self.meeting_purpose in INTERNAL_PURPOSES else "External"

	def validate_room_purpose(self):
		if not (self.meeting_purpose and self.conference_room):
			return
		from visitormanagement.conference_room.doctype.conference_room.conference_room import allowed_purposes

		room = frappe.get_cached_doc("Conference Room", self.conference_room)
		if self.meeting_purpose not in allowed_purposes(room):
			frappe.throw(
				_("{0} is not set up for {1} meetings. Allowed: {2}.").format(
					self.conference_room, self.meeting_purpose, ", ".join(allowed_purposes(room)) or _("none")
				),
				title=_("Room Not Suitable"),
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
		overlap = frappe.db.sql(
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
			""",
			{
				"room": self.conference_room,
				"date": self.booking_date,
				"start_time": self.start_time,
				"end_time": self.end_time,
				"self_name": self.name or "NEW",
			},
			as_dict=True,
		)

		if overlap:
			frappe.throw(
				_("Time conflict with booking <b>{0}</b> ({1}: {2} - {3}). "
				  "Please choose a different time slot.").format(
					overlap[0].name,
					overlap[0].meeting_title,
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
def get_available_rooms(booking_date, start_time, end_time, min_capacity=0, exclude_booking=None, meeting_purpose=None):
	"""Return rooms available for the given slot, sorted smallest-suitable-first."""
	if not booking_date or not start_time or not end_time:
		return []

	min_cap = cint(min_capacity) or 1

	rooms = frappe.get_all(
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

	available = [r for r in rooms if r.name not in booked]
	if not meeting_purpose:
		return available  # original behaviour for the "Find Available Rooms" dialog

	from visitormanagement.conference_room.doctype.conference_room.conference_room import allowed_purposes

	fits = []
	for r in available:
		room = frappe.get_cached_doc("Conference Room", r.name)
		if meeting_purpose not in allowed_purposes(room):
			continue
		if room.available_from and get_time(start_time) < get_time(room.available_from):
			continue
		if room.available_to and get_time(end_time) > get_time(room.available_to):
			continue
		r.priority_order = cint(room.get("priority_order"))
		fits.append(r)
	# smallest room that fits first, then the configured priority
	fits.sort(key=lambda r: (cint(r.capacity) - cint(min_capacity), r.priority_order, r.room_name))
	for i, r in enumerate(fits):
		r.best_fit = 1 if i == 0 else 0
	return fits


@frappe.whitelist()
def get_room_suggestions(meeting_purpose, booking_date, start_time, end_time, attendee_count=0, exclude_booking=None):
	"""Rooms for the purpose / slot / head-count, plus the ones ruled out and why."""
	if not (meeting_purpose and booking_date and start_time and end_time):
		return {"available_rooms": [], "unavailable_rooms": [], "suggestion": "", "total_available": 0}
	attendee_count = cint(attendee_count)
	available = get_available_rooms(booking_date, start_time, end_time, attendee_count, exclude_booking, meeting_purpose)
	available_names = {r.name for r in available}
	unavailable = []
	from visitormanagement.conference_room.doctype.conference_room.conference_room import allowed_purposes

	for room in frappe.get_all("Conference Room", filters={"is_active": 1}, pluck="name"):
		if room in available_names:
			continue
		doc = frappe.get_cached_doc("Conference Room", room)
		if meeting_purpose not in allowed_purposes(doc):
			reason = _("not for {0}").format(meeting_purpose)
		elif cint(doc.capacity) < attendee_count:
			reason = _("seats {0}").format(doc.capacity)
		elif (doc.available_from and get_time(start_time) < get_time(doc.available_from)) or (
			doc.available_to and get_time(end_time) > get_time(doc.available_to)
		):
			reason = _("open {0}–{1}").format(doc.available_from, doc.available_to)
		else:
			reason = _("already booked")
		unavailable.append({"name": room, "room_name": doc.room_name, "capacity": doc.capacity, "reason": reason})
	if available:
		best = available[0]
		suggestion = _("Best fit: {0} ({1} seats for {2} attendees).").format(best.room_name, best.capacity, attendee_count or 1)
	else:
		suggestion = _("No room fits this purpose, time and head-count.")
	return {
		"available_rooms": available,
		"unavailable_rooms": unavailable,
		"suggestion": suggestion,
		"total_available": len(available),
	}


@frappe.whitelist()
def get_room_schedule(conference_room, booking_date):
	"""Get all bookings for a room on a given date."""
	return frappe.get_all(
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
			name, meeting_title,
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
