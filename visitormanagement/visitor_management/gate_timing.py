"""Is a gate movement inside the visitor's booked time? Warn and note it — never refuse.

People come early, run late, stay on after a meeting overruns. None of that is a
reason to keep someone at the barrier while a queue builds behind them, so the
clock never blocks a movement: the guard sees a short orange warning, and the
Security Log keeps a plain note of it (`outside_booked_time`, `timing_note`) so
the record says what happened.

The booked window of a day is the pass's Expected Check-In .. Expected Check-Out
on that day, the same on every day of a multi-day pass. A check-out time at or
before the check-in time is an overnight visit that ends the next morning, read
by the same two rules the no-show and overstay jobs use (`tasks._planned_end`,
`tasks._stay_day`): a 00:30 arrival on a 22:00-06:00 shift belongs to the shift
that began the evening before, and is inside it.

A movement is outside when it falls more than VMS Settings -> Gate Time Tolerance
(minutes) before the window opens or after it closes: an early or late arrival,
an early or late leave. Always judged on the time the server recorded, never on
a time a browser sent. Switched off with VMS Settings -> Warn at the gate
outside the booked time.

The date rules stay in the Security Log and are strict: a pass is still refused
on a day it is not valid for. `on_a_pass_day` lets those rules see an overnight
visitor's after-midnight arrival as the night it belongs to.
"""

from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, getdate

from visitormanagement.visitor_management.settings import feature_on
from visitormanagement.visitor_management.tasks import _planned_end, _stay_day

DEFAULT_TOLERANCE_MINUTES = 30

# The Visitor Pass fields these rules read, for callers that fetch a pass with get_value.
PASS_FIELDS = (
	"visit_date",
	"multi_day_pass",
	"pass_valid_until",
	"expected_checkin",
	"expected_checkout",
	"actual_checkin",
)


def tolerance():
	"""How far outside the booked time a movement may be before the guard is warned.

	Read straight from the Single, so it works before the field is on the form:
	nothing saved means the default, and 0 (warn at once) is a real choice.
	"""
	stored = frappe.db.sql(
		"select value from `tabSingles` where doctype = 'VMS Settings' and field = 'gate_time_tolerance_minutes'"
	)
	value = stored[0][0] if stored and stored[0][0] not in (None, "") else DEFAULT_TOLERANCE_MINUTES
	return timedelta(minutes=max(0, cint(value)))


def pass_days(vp):
	"""(first, last) day the pass is good for: its visit date, or the multi-day window."""
	first = getdate(vp.get("visit_date"))
	if cint(vp.get("multi_day_pass")) and vp.get("pass_valid_until"):
		return first, max(first, getdate(vp.get("pass_valid_until")))
	return first, first


def shift_day(vp, at):
	"""The day whose booking a movement at `at` belongs to (see tasks._stay_day)."""
	return _stay_day(at, vp.get("expected_checkin"), vp.get("expected_checkout"))


def on_a_pass_day(vp, at):
	"""Is `at` on a day this pass is good for — by the calendar, or by the overnight shift it is part of?"""
	if not vp.get("visit_date"):
		return False
	first, last = pass_days(vp)
	return any(first <= day <= last for day in (getdate(at), shift_day(vp, at)))


def booked_window(vp, day):
	"""(start, end) datetimes of the booking on `day`, or None without booked times."""
	start_time, end_time = vp.get("expected_checkin"), vp.get("expected_checkout")
	if not (start_time and end_time):
		return None
	return get_datetime(f"{getdate(day)} {start_time}"), _planned_end(day, start_time, end_time)


def _movement_day(vp, event_type, at):
	"""The pass day a movement is judged against.

	A check-in belongs to the shift it falls in; a check-out to the stay it
	closes, so leaving at 07:00 after a night shift is judged against that night.
	Clamped to the pass's own days: the date rules decide whether the day itself
	is allowed.
	"""
	moment = at
	if event_type == "Check-Out" and vp.get("actual_checkin"):
		moment = vp.get("actual_checkin")
	first, last = pass_days(vp)
	return min(max(shift_day(vp, moment), first), last)


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


def timing_note(vp, event_type, at):
	"""One short sentence when a movement at `at` is outside the booked time, else None.

	`at` is the server's time for the movement (the Security Log's stamped
	check-in / check-out). None as well for any other event, a pass without
	booked times, or with the warning switched off.
	"""
	if event_type not in ("Check-In", "Check-Out") or not at or not vp.get("visit_date"):
		return None
	if not feature_on("warn_outside_booked_time"):
		return None
	at = get_datetime(at)
	window = booked_window(vp, _movement_day(vp, event_type, at))
	if not window:
		return None
	start, end = window
	slack = tolerance()
	booked = f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}"
	if event_type == "Check-In":
		if at < start - slack:
			return _("Arrived {0} early (booked {1}).").format(_span(start - at), booked)
		if at > end + slack:
			return _("Arrived {0} after the booked time ended (booked {1}).").format(_span(at - end), booked)
		return None
	if at < start - slack:
		return _("Left {0} before the booked time began (booked {1}).").format(_span(start - at), booked)
	if at > end + slack:
		return _("Left {0} late (booked {1}).").format(_span(at - end), booked)
	return None
