# For license information, please see license.txt

from datetime import date, datetime, timedelta

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, get_time, now_datetime

# What the fields default to on the form, for when the DocField itself has none.
DEFAULT_AVAILABLE_FROM = "08:00:00"
DEFAULT_AVAILABLE_TO = "20:00:00"

# A Time value this close behind the clock, with a fraction of a second on it,
# was not typed by anybody — see ConferenceRoom._set_opening_hours.
_FRAMEWORK_NOW_WINDOW = timedelta(minutes=2)


class ConferenceRoom(Document):
	def validate(self):
		self._set_opening_hours()

		if self.available_from and self.available_to:
			# get_time() on both sides, never a raw comparison: a Time field can
			# reach validate() as a string, and the Desk sends a single-digit hour
			# without a leading zero. "9:00:00" >= "17:00:00" is True as strings
			# ('9' > '1'), so a room open 09:00-17:00 was rejected as "'Available
			# From' must be before 'Available To'". A room opening before 10:00
			# therefore could not be re-saved at all — even without touching the
			# time fields.
			# conference_room_booking.py:87 already compares this way; matched here.
			if get_time(self.available_from) >= get_time(self.available_to):
				frappe.throw(_("'Available From' must be before 'Available To'."))

		if self.capacity is not None and self.capacity < 1:
			frappe.throw(_("Seating Capacity must be at least 1."))

		if self.min_booking_minutes and self.min_booking_minutes < 15:
			frappe.throw(_("Minimum booking duration must be at least 15 minutes."))

		self._validate_bookable_window()

	def _set_opening_hours(self):
		"""Give a new room real opening hours when it was not given any.

		On Frappe 15 a document created on the server — REST API, Data Import of a
		sheet without the two columns, a script, another app — never receives the
		form's 08:00 / 20:00. `frappe.new_doc(as_dict=True)` sets EVERY Time field
		to the current time, default or not (frappe/model/create_new.py
		set_dynamic_default_values), and Document._set_defaults copies that into
		whatever the caller left empty. Such a room opened and closed within the
		same millisecond of the afternoon it was created: it passed the check
		below and then refused every booking as "too early" or "too late".

		A value the framework filled in is recognisable: it is the present moment
		and carries microseconds, which no person and no import sheet supplies.
		Only a new room is touched. On an existing one, clearing both fields is a
		choice ("no restriction") and is left as it is.
		"""
		if not self.is_new():
			return

		for fieldname, fallback in (
			("available_from", DEFAULT_AVAILABLE_FROM),
			("available_to", DEFAULT_AVAILABLE_TO),
		):
			value = self.get(fieldname)
			if not value or _is_framework_now(value):
				self.set(fieldname, self.meta.get_field(fieldname).default or fallback)

	def _validate_bookable_window(self):
		"""The room must be open long enough to hold its own shortest booking."""
		if not (self.available_from and self.available_to):
			return

		# No minimum set means any length can be booked — but not no length at all.
		minimum = cint(self.min_booking_minutes) or 1
		opens = datetime.combine(date.min, get_time(self.available_from))
		closes = datetime.combine(date.min, get_time(self.available_to))
		open_minutes = int((closes - opens).total_seconds() // 60)
		if open_minutes < minimum:
			frappe.throw(
				_(
					"{0} is open for {1} minutes ({2} to {3}), which is shorter than its minimum "
					"booking of {4} minutes. Nobody could book it — widen the operating hours or "
					"lower the minimum."
				).format(
					self.room_name or self.name,
					open_minutes,
					frappe.utils.format_time(self.available_from),
					frappe.utils.format_time(self.available_to),
					minimum,
				),
				title=_("Operating Hours Too Short"),
			)


def _is_framework_now(value):
	"""True for a Time that is the server's own "now", not a time somebody chose."""
	try:
		value = get_time(value)
	except Exception:
		return False
	if not value.microsecond:
		return False
	now = now_datetime()
	moment = datetime.combine(now.date(), value)
	if moment > now:
		# Just after midnight, "a moment ago" was yesterday.
		moment -= timedelta(days=1)
	return now - moment <= _FRAMEWORK_NOW_WINDOW
