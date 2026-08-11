# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_time


EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


# Every policy number here is read by code that assumes a sane value. Zero or a
# negative would not error anywhere — it would quietly change behaviour (an
# expiry of 0 expires invitations the moment they are sent), so the floor is
# enforced at the point the value is set rather than at each of its readers.
POSITIVE_SETTINGS = (
	("max_visit_duration_hrs", "Max Visit Duration (hrs)"),
	("max_advance_booking_days", "Max Advance Booking Days"),
	("invitation_expiry_days", "Invitation Expiry Days"),
	("no_show_grace_hours", "No-Show Grace Hours"),
)


class VMSSettings(Document):
	def validate(self):
		self._validate_emails()
		self._validate_policy_numbers()
		self._validate_country_code()
		self._validate_meal_windows()

	def _validate_emails(self):
		for field in ("admin_email", "food_dept_email"):
			value = (getattr(self, field, None) or "").strip()
			if value and not EMAIL_RE.match(value):
				frappe.throw(
					_("'{0}' is not a valid email address.").format(value),
					title=_("Invalid Email"),
				)

	def _validate_policy_numbers(self):
		for fieldname, label in POSITIVE_SETTINGS:
			value = getattr(self, fieldname, None)
			if value in (None, ""):
				continue
			if int(value) < 1:
				frappe.throw(
					_("{0} must be at least 1 — {1} would disable the rule silently.").format(
						_(label), int(value)
					),
					title=_("Invalid Policy Value"),
				)

	def _validate_country_code(self):
		code = (self.default_country_code or "").strip().lstrip("+")
		if not code:
			return
		if not code.isdigit() or not 1 <= len(code) <= 4:
			frappe.throw(
				_("Default Country Code must be 1–4 digits (for example 91), not '{0}'.").format(
					self.default_country_code
				),
				title=_("Invalid Country Code"),
			)
		self.default_country_code = code

	def _validate_meal_windows(self):
		"""A malformed meal window fails silently: no meal is ever matched.

		`settings.meal_windows()` walks these rows to decide which meal a visit
		qualifies for, so a window that ends before it starts, or two windows
		covering the same minute, produces wrong or missing hospitality without
		raising anything. Catch it where it is entered.
		"""
		windows = []
		for row in self.meal_windows or []:
			label = (row.meal_label or "").strip()
			if not label:
				frappe.throw(
					_("Row {0}: Meal Label is required.").format(row.idx),
					title=_("Incomplete Meal Window"),
				)
			if not row.start_time or not row.end_time:
				frappe.throw(
					_("Row {0} ({1}): both Start Time and End Time are required.").format(row.idx, label),
					title=_("Incomplete Meal Window"),
				)

			start, end = get_time(row.start_time), get_time(row.end_time)
			if start >= end:
				frappe.throw(
					_("Row {0} ({1}): End Time {2} must be later than Start Time {3}.").format(
						row.idx, label, end, start
					),
					title=_("Invalid Meal Window"),
				)
			windows.append((row.idx, label, start, end))

		seen = {}
		for idx, label, start, end in windows:
			key = label.casefold()
			if key in seen:
				frappe.throw(
					_("Meal Label '{0}' is used twice (rows {1} and {2}). Each meal must appear once.").format(
						label, seen[key], idx
					),
					title=_("Duplicate Meal Window"),
				)
			seen[key] = idx

		for i, (idx_a, label_a, start_a, end_a) in enumerate(windows):
			for idx_b, label_b, start_b, end_b in windows[i + 1:]:
				if start_a < end_b and start_b < end_a:
					frappe.throw(
						_("{0} ({1}–{2}) overlaps {3} ({4}–{5}). A visit would qualify for both.").format(
							label_a, start_a, end_a, label_b, start_b, end_b
						),
						title=_("Overlapping Meal Windows"),
					)
