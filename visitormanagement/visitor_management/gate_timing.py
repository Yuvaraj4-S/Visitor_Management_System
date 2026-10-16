"""Is a gate movement inside the visitor's booked time? Warn and note it — never refuse.

People arrive late, come early, stay on after a meeting runs over. None of that is
a reason to keep someone at the barrier or to refuse to record that they left, so
the gate never blocks on the clock: the guard is warned, and the Security Log keeps
a plain note of how far outside the booked time the movement was (`timing_note`,
`outside_booked_time`), so the record says what happened. Booked times are the
pass's Expected Check-In / Check-Out on the visit date — on each day of a
multi-day pass. VMS Settings "Gate Time Tolerance (minutes)" sets how far outside
counts as outside.

The date rules are separate and stay strict (security_log.py): a pass is still
refused on a day it is not valid for.
"""

from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import add_days, add_to_date, cint, get_datetime, getdate

DEFAULT_TOLERANCE_MINUTES = 30


def tolerance():
	"""Minutes outside the booked time before the gate warns (VMS Settings), never negative.

	0 is a real choice (warn at once), so "never saved" is told apart from it by
	whether the Single holds a value at all: a site upgraded to this version
	has none until the setting is saved, and gets the default.
	"""
	stored = frappe.db.sql(
		"select value from `tabSingles` where doctype = 'VMS Settings' and field = 'gate_time_tolerance_minutes'"
	)
	value = stored[0][0] if stored and stored[0][0] not in (None, "") else DEFAULT_TOLERANCE_MINUTES
	return timedelta(minutes=max(0, cint(value)))


def _span(delta):
	"""'2 h 5 min', '1 day 3 h', '25 min' — how a person says it."""
	minutes = int(abs(delta).total_seconds() // 60)
	days, minutes = divmod(minutes, 24 * 60)
	hours, minutes = divmod(minutes, 60)
	parts = []
	if days:
		parts.append(_("{0} day").format(days) if days == 1 else _("{0} days").format(days))
	if hours:
		parts.append(_("{0} h").format(hours))
	if minutes and not days:
		parts.append(_("{0} min").format(minutes))
	return " ".join(parts) or _("less than a minute")


def _hhmm(value):
	return get_datetime(value).strftime("%H:%M")


def booked_window(vp, on_date):
	"""(start, end) datetimes the visitor is booked for on `on_date`, or None.

	A single-day pass is booked on its visit date only; a multi-day pass has the
	same daily hours on every day of its window.
	"""
	start_time, end_time = vp.get("expected_checkin"), vp.get("expected_checkout")
	if not (start_time and end_time and vp.get("visit_date")):
		return None
	day = getdate(on_date)
	first = getdate(vp.get("visit_date"))
	last = (
		getdate(vp.get("pass_valid_until"))
		if cint(vp.get("multi_day_pass")) and vp.get("pass_valid_until")
		else first
	)
	# An overnight visit (Ends Next Day) that started the evening before is still that day's booking.
	overnight = cint(vp.get("overnight_visit")) and get_datetime(f"{first} {end_time}") <= get_datetime(
		f"{first} {start_time}"
	)
	if overnight and day > first and get_datetime(on_date) <= get_datetime(f"{day} {end_time}"):
		day = add_days(day, -1)
	day = min(max(day, first), last)
	start, end = get_datetime(f"{day} {start_time}"), get_datetime(f"{day} {end_time}")
	if overnight:
		end = add_to_date(end, days=1)
	return start, end


def timing_note(vp, event_type, at):
	"""A plain sentence when `at` is outside the booked time for this movement, else None.

	None as well when VMS Settings -> Warn When Outside the Booked Time is off.
	"""
	if event_type not in ("Check-In", "Check-Out") or not at:
		return None
	from visitormanagement.visitor_management.settings import feature_on

	if not feature_on("warn_outside_booked_time"):
		return None
	at = get_datetime(at)
	window = booked_window(vp, at)
	if not window:
		return None
	start, end = window
	slack = tolerance()
	booked = f"{_hhmm(start)}–{_hhmm(end)}"  # noqa: RUF001
	if event_type == "Check-In":
		if at < start - slack:
			return _("Arrived {0} early (booked {1}).").format(_span(start - at), booked)
		if at > end:
			return _("Arrived {0} after the booked visit ended (booked {1}).").format(_span(at - end), booked)
		if at > start + slack:
			return _("Arrived {0} late (booked {1}).").format(_span(at - start), booked)
		return None
	# Check-Out: staying on is what matters; leaving early is not a gate concern.
	if at > end + slack:
		return _("Left {0} after the expected check-out at {1} (booked {2}).").format(
			_span(at - end), _hhmm(end), booked
		)
	return None
