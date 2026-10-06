# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model import default_fields
from frappe.model.document import Document
from frappe.utils import cint, flt, get_datetime, get_time, getdate, time_diff_in_hours, today

from visitormanagement.permissions import get_conference_room_booking_permission_query_conditions
from visitormanagement.visitor_management.lifecycle import assert_submitted_through_approval
from visitormanagement.visitor_management.link_details import fill_from_link

# What a booking still says to someone who may not read it: which room, and from
# when to when. That is all "this slot is taken" needs. Everything else a query
# selects — the booking's id, its type, its status, who organised it and for how
# many — is held back by `_show_as_busy`. Listed as what is shown, not as what is
# hidden, so that a column added to one of those queries later is hidden until
# someone decides otherwise.
BUSY_SHOWN_FIELDS = frozenset(
	("conference_room", "booking_date", "start_time", "end_time", "start", "end", "allDay")
)

# Filters a calendar viewer may apply to every booking, including the ones shown
# to them only as "Busy". Any other field is part of what "Busy" hides, so a
# filter on it is answered from the viewer's own bookings alone. `status` is one
# of those: while it was open, filtering the calendar by "Approved" told a viewer
# the status of every booking that was otherwise only "Busy" to them.
CALENDAR_OPEN_FILTER_FIELDS = ("conference_room", "booking_date")


class ConferenceRoomBooking(Document):
	def validate(self):
		# Was `fetch_from: booked_by.department`, which needed READ on Employee.
		fill_from_link(self, "booked_by", "Employee", {"department": "department"})
		self.validate_schedule()
		self.calculate_duration()
		if self._asks_for_the_room():
			self.validate_past_date()
			self.validate_room_limits()
			self.validate_capacity()
			self.validate_overlap()
		self.auto_set_service_flags()
		self._validate_visitor_pass_approved()
		self._sync_status_with_workflow()

	def before_submit(self):
		# The approval must have been earned (see the helper): a Facility Manager
		# could submit their own booking, or any Draft, straight to Approved.
		assert_submitted_through_approval(self)

	def _asks_for_the_room(self):
		"""Whether this save claims the slot, and so has to satisfy the room's rules.

		A booking being rejected gives the slot up. Holding that save to the
		rules for taking a room meant a pending booking whose date had passed
		could not be rejected ("Cannot book a room for a past date") and stayed
		in the Facility Manager's queue for good. The rules apply again the
		moment it is re-applied.
		"""
		return (getattr(self, "workflow_state", None) or "Draft") != "Rejected"

	def run_notifications(self, method):
		"""Keep a failing alert email from turning a good save into an error.

		Same guard as VisitorPass.run_notifications — see the note there. On a
		site with no outgoing Email Account, "CRB Pending Approval" and "CRB
		Service Alert" fail inside frappe.sendmail, whose frappe.throw message
		stays queued for the client although the save went through: every Submit
		showed an email error, and the list's bulk action read it as "Failed".
		The failure itself is still in the Error Log.
		"""
		messages_before = list(frappe.message_log)
		try:
			super().run_notifications(method)
		finally:
			frappe.local.message_log = messages_before

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
		# Always, not only "when still Draft": submitted means Approved, whatever
		# `status` read on the way in. A booking submitted from Pending Approval
		# without the state field being changed kept "Pending Approval" on an
		# approved document.
		self.db_set("status", "Approved")

	def on_cancel(self):
		self.db_set("status", "Cancelled")

	# -- Schedule Validation ---

	def validate_schedule(self):
		if not self.start_time or not self.end_time:
			frappe.throw(_("Both Start Time and End Time are required."))

		if get_time(self.start_time) >= get_time(self.end_time):
			frappe.throw(_("Start Time must be before End Time."))

	def validate_past_date(self):
		"""No booking is made for, moved to, or approved on a day that has passed.

		Asked only when the slot is being asked for — a new booking, a changed
		date or time — and when it is being approved. Every other save of an
		existing booking (a note, a title, a workflow step that is not Approve)
		is not choosing a date, and refusing those froze old bookings.
		"""
		if getdate(self.booking_date) >= getdate(today()):
			return
		if self.docstatus == 1:
			frappe.throw(
				_(
					"The booking date {0} has passed, so this booking can no longer be approved. "
					"Reject it to clear it from the queue."
				).format(frappe.format(self.booking_date, {"fieldtype": "Date"})),
				title=_("Booking Date Passed"),
			)
		if self.is_new() or self._slot_changed():
			frappe.throw(_("Cannot book a room for a past date."))

	def _slot_changed(self):
		before = self.get_doc_before_save()
		if not before:
			return True
		return (
			self.conference_room != before.conference_room
			or getdate(self.booking_date) != getdate(before.booking_date)
			or get_time(self.start_time) != get_time(before.start_time)
			or get_time(self.end_time) != get_time(before.end_time)
			# Sending it for approval, or asking again after a rejection, is
			# asking for the slot.
			or before.get("workflow_state") == "Rejected"
			or (
				self.get("workflow_state") == "Pending Approval"
				and before.get("workflow_state") != "Pending Approval"
			)
		)

	# -- Duration Calculation --

	def calculate_duration(self):
		start_dt = get_datetime(f"{self.booking_date} {self.start_time}")
		end_dt = get_datetime(f"{self.booking_date} {self.end_time}")
		self.duration_hours = flt(time_diff_in_hours(end_dt, start_dt), 2)

	# -- Room Hours and Duration Limits --

	def validate_room_limits(self):
		room = frappe.get_cached_doc("Conference Room", self.conference_room)
		problem = slot_problem(room, self.start_time, self.end_time)
		if problem:
			frappe.throw(problem)

	# -- Capacity Validation ---

	def validate_capacity(self):
		if not self.expected_attendees or not self.conference_room:
			return

		room_capacity = frappe.db.get_value("Conference Room", self.conference_room, "capacity")
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
				_(
					"Time conflict with <b>{0}</b> ({1}: {2} - {3}). Please choose a different time slot."
				).format(
					overlap[0].name,
					what,
					overlap[0].start_time,
					overlap[0].end_time,
				),
				title=_("Room Already Booked"),
			)


def _clock(value):
	"""A time as people read it on a schedule: 09:00."""
	return get_time(value).strftime("%H:%M")


def slot_problem(room, start_time, end_time):
	"""Why this room cannot be had for this slot — its hours or its duration limits — or None.

	The one statement of the room's own rules: a booking is validated with it,
	and Find Available Rooms uses it to leave out rooms that would then refuse
	the booking. `room` is anything with the Conference Room fields.
	"""
	start, end = get_time(start_time), get_time(end_time)
	room_name = room.get("room_name") or room.get("name")

	if room.get("available_from") and start < get_time(room.get("available_from")):
		return _("{0} is available from {1}. Your start time {2} is too early.").format(
			room_name, _clock(room.get("available_from")), _clock(start)
		)
	if room.get("available_to") and end > get_time(room.get("available_to")):
		return _("{0} is available until {1}. Your end time {2} is too late.").format(
			room_name, _clock(room.get("available_to")), _clock(end)
		)

	minutes = (end.hour * 60 + end.minute + end.second / 60) - (
		start.hour * 60 + start.minute + start.second / 60
	)
	if room.get("min_booking_minutes") and minutes < cint(room.get("min_booking_minutes")):
		return _("Minimum booking duration for {0} is {1} minutes.").format(
			room_name, cint(room.get("min_booking_minutes"))
		)
	if room.get("max_booking_hours") and minutes > cint(room.get("max_booking_hours")) * 60:
		return _("Maximum booking duration for {0} is {1} hours.").format(
			room_name, cint(room.get("max_booking_hours"))
		)
	return None


def _show_as_busy(row):
	"""Reduce a booking the caller may not read to "this room is taken from ... to ...".

	The one statement of what "Busy" hides, for every list of bookings this
	module hands out (the calendar feed and the room schedule). The calendar
	used to mask the title alone and still sent the booking's id, its meeting
	type and its status with every "Busy" event.
	"""
	for fieldname in row:
		if fieldname not in BUSY_SHOWN_FIELDS:
			row[fieldname] = None
	row["meeting_title"] = _("Busy")
	return row


def _may_read_booking(name):
	"""Whether `name` is a saved booking the caller may read."""
	if not frappe.db.exists("Conference Room Booking", name):
		# A form that has not been saved yet sends its temporary name.
		return False
	return bool(frappe.has_permission("Conference Room Booking", "read", doc=name))


# -- Whitelisted API --


@frappe.whitelist()
def get_available_rooms(
	booking_date: str,
	start_time: str,
	end_time: str,
	min_capacity: int | str | None = 0,
	exclude_booking: str | None = None,
):
	"""Return rooms available for the given slot, sorted smallest-suitable-first.

	"Available" means a booking for this slot would be accepted: the room is
	active and big enough, open for the whole slot, allows a meeting of that
	length, and is not already taken. Rooms closed at that hour used to be
	offered, and the booking then failed on save.
	"""
	if not booking_date or not start_time or not end_time:
		return []

	try:
		booking_day, start, end = getdate(booking_date), get_time(start_time), get_time(end_time)
	except Exception:
		frappe.throw(_("Enter a valid date, start time and end time."))
	if booking_day < getdate(today()):
		frappe.throw(_("Cannot book a room for a past date."))
	if start >= end:
		frappe.throw(_("Start Time must be before End Time."))

	min_cap = cint(min_capacity) or 1

	# get_list, not get_all: get_all bypasses the permission layer entirely, so
	# the room master was readable by anyone who could reach the endpoint.
	rooms = frappe.get_list(
		"Conference Room",
		filters={"is_active": 1, "capacity": [">=", min_cap]},
		fields=[
			"name",
			"room_name",
			"capacity",
			"location",
			"floor",
			"room_type",
			"available_from",
			"available_to",
			"min_booking_minutes",
			"max_booking_hours",
		],
		order_by="capacity asc",
		limit_page_length=0,
	)

	exclude_clause = ""
	params = {
		"date": booking_date,
		"start_time": start_time,
		"end_time": end_time,
	}

	# `exclude_booking` is the booking being rescheduled, so that it does not
	# stand in its own way. It is honoured only for a booking the caller may
	# read: taken from anyone, it answered "which room and slot does booking X
	# hold?" (the room comes back as free once X is left out) for bookings the
	# caller is otherwise shown only as "Busy", without their id.
	if exclude_booking and _may_read_booking(exclude_booking):
		exclude_clause = "AND name != %(exclude)s"
		params["exclude"] = exclude_booking

	booked = frappe.db.sql_list(
		"""
		SELECT DISTINCT conference_room
		FROM `tabConference Room Booking`
		WHERE booking_date = %(date)s
		  AND docstatus < 2
		  AND status NOT IN ('Cancelled', 'Rejected')
		  AND (start_time < %(end_time)s AND end_time > %(start_time)s)
		"""
		+ exclude_clause,
		params,
	)

	shown = ("name", "room_name", "capacity", "location", "floor", "room_type")
	return [
		{key: room.get(key) for key in shown}
		for room in rooms
		if room.name not in booked and not slot_problem(room, start, end)
	]


@frappe.whitelist()
def get_room_schedule(conference_room: str, booking_date: str):
	"""Every slot taken in a room on a date, with other people's meetings shown as "Busy".

	The question this answers is "when is the room free", so it has to list
	every booking that holds a slot. It was a row-scoped `get_list`, which
	left out everybody else's bookings: a host saw only their own and was told
	"No other bookings for this room on this date" about a room that was full.

	Masked rather than filtered, the same rule as the calendar
	(`get_booking_events`): the time is shown to anyone who may use bookings at
	all, and everything else — the booking's id, the meeting, its organiser,
	its type, its size and its status — only to the booking's owner, its
	`booked_by` employee or a room overseer (see `_show_as_busy`).
	"""
	frappe.has_permission("Conference Room Booking", "read", throw=True)

	scope = get_conference_room_booking_permission_query_conditions()
	rows = frappe.db.sql(
		"""
		SELECT
			name, meeting_title, start_time, end_time, booked_by,
			meeting_type, expected_attendees, status,
			("""
		+ (scope or "1")
		+ """) AS is_visible
		FROM `tabConference Room Booking`
		WHERE conference_room = %(room)s
		  AND booking_date = %(date)s
		  AND docstatus < 2
		  AND status NOT IN ('Cancelled', 'Rejected')
		ORDER BY start_time ASC
		""",
		{"room": conference_room, "date": booking_date},
		as_dict=True,
	)
	for row in rows:
		if not row.pop("is_visible"):
			_show_as_busy(row)
	return rows


def _calendar_filter_conditions(filters, scope):
	"""SQL for the calendar's filters, as ` AND ...` (or an empty string).

	The Frappe calendar sends the list view's filters as rows
	(`[doctype, fieldname, operator, value]`); only a dict was read here, so
	every filter set in the calendar — the Conference Room one this view itself
	declares included — was ignored. The rows are turned into conditions by
	Frappe's own filter builder, which quotes the values.

	A filter on a field that "Busy" hides (title, organiser, ...) would let a
	viewer find out what a masked booking is by testing values against it, so
	such filters only ever match bookings the viewer may read in full.
	"""
	from frappe.desk.reportview import get_filters_cond

	if isinstance(filters, str):
		filters = json.loads(filters)
	if not filters:
		return ""
	if isinstance(filters, dict):
		filters = [["Conference Room Booking", fieldname, "=", value] for fieldname, value in filters.items()]

	meta = frappe.get_meta("Conference Room Booking")
	open_rows, private_rows = [], []
	for row in filters:
		if not isinstance(row, list | tuple) or len(row) < 4:
			continue
		doctype, fieldname = row[0], row[1]
		if doctype != "Conference Room Booking":
			continue
		if not (meta.has_field(fieldname) or fieldname in default_fields):
			continue
		(open_rows if fieldname in CALENDAR_OPEN_FILTER_FIELDS else private_rows).append(list(row[:4]))

	if not (open_rows or private_rows):
		return ""

	# The builder returns finished SQL with the values quoted inline, so a
	# literal % in it — a "like" filter — must not be read as a query parameter.
	conditions = get_filters_cond(
		"Conference Room Booking", open_rows + private_rows, [], ignore_permissions=True
	).replace("%", "%%")
	if private_rows and scope:
		conditions += f" AND {scope}"
	return conditions


@frappe.whitelist()
def get_booking_events(start: str, end: str, filters: str | dict | list | None = None):
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
	# which is the whole job this view does. So every booking that holds a slot
	# is still returned — the slot stays visibly busy — but unless the viewer owns
	# the booking, is its `booked_by` employee, or is an overseer, it comes back as
	# the room, the time and "Busy" and nothing else (`_show_as_busy`). Masking
	# the title alone left the id, the meeting type and the status of everybody
	# else's bookings in the response.
	scope = get_conference_room_booking_permission_query_conditions()
	cond = _calendar_filter_conditions(filters, scope)

	rows = frappe.db.sql(
		"""
		SELECT
			name, meeting_title,
			TIMESTAMP(booking_date, start_time) AS `start`,
			TIMESTAMP(booking_date, end_time) AS `end`,
			conference_room, meeting_type, status,
			0 AS allDay,
			("""
		+ (scope or "1")
		+ """) AS is_visible
		FROM `tabConference Room Booking`
		WHERE docstatus < 2
		  AND status NOT IN ('Cancelled')
		  AND booking_date BETWEEN %(start)s AND %(end)s
		"""
		+ cond
		+ """
		ORDER BY booking_date, start_time
		""",
		{"start": start, "end": end},
		as_dict=True,
	)

	events = []
	for row in rows:
		if not row.pop("is_visible"):
			# "Busy" says the slot is taken, and a rejected booking takes none
			# (`find_conflicting_booking`). Its status is no longer sent, so it
			# could not be told apart from a slot that really is taken.
			if row.status == "Rejected":
				continue
			_show_as_busy(row)
		# `title` names the room: with several rooms on one calendar, "10:00 Busy"
		# does not say which room is busy, which is the one thing the viewer is
		# looking for.
		row.title = " · ".join(part for part in (row.conference_room, row.meeting_title) if part)
		events.append(row)
	return events


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
