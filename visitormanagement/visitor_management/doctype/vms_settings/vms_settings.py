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
	# A 0 here is not "no retention floor" the way 0 means "no limit" elsewhere in
	# this doctype — tasks.purge_expired_visitor_data() would read it as "anything
	# that is over, purge immediately", which is the one behaviour a data-retention
	# knob must never produce silently. Floored here, same as every other policy
	# number on this form.
	("data_retention_days", "Retention Period (days)"),
)


class VMSSettings(Document):
	def validate(self):
		self._validate_emails()
		self._validate_policy_numbers()
		self._validate_country_code()
		self._validate_meal_windows()
		self._publish_portal_logo()

	def _publish_portal_logo(self):
		"""Keep `portal_logo` on a URL a logged-out visitor can actually load.

		`portal_logo` is an Attach Image, and Frappe's uploader defaults to
		`is_private = 1` — so the ordinary act of picking a logo through the Desk
		files it under `/private/files/`. That URL is then rendered straight into the
		public pre-registration page by
		`web_form/visitor_pre_registration_form.py:_brand_header`, where the viewer is
		Guest and gets a 403. The result is a broken logo on the first page a
		customer's visitors ever see, with nothing in the UI explaining why — the
		admin picked a file, the picker accepted it, and the page silently fails for
		everyone but them.

		Found by the QA suite's "Guest can reach the public Visitor Pre-Registration
		portal page" flow, which fails on the console 403; it had passed on
		2026-07-07, before a logo was configured.

		A private path here is never what the administrator meant — this field's only
		purpose is to appear on a page served to anonymous visitors. So the backing
		File is flipped to public rather than the save being rejected:
		`File.handle_is_private_changed` moves the file on disk and rewrites
		`file_url`, and the new public URL is stored back here.

		A URL with no File record behind it — an attachment deleted after it was
		set — cannot be repaired that way. That
		is cleared with a visible message rather than thrown, so a dangling logo can
		never wedge the Settings form and lock an admin out of every other field on it.
		"""
		url = (self.portal_logo or "").strip()
		if not url.startswith("/private/files/"):
			return

		name = frappe.db.get_value("File", {"file_url": url}, "name")
		if not name:
			self.portal_logo = ""
			frappe.msgprint(
				_(
					"Portal Logo pointed at {0}, but that file no longer exists, so the public "
					"pre-registration page returned 403 for it. The field has been cleared — "
					"re-upload the logo if you want one."
				).format(url),
				title=_("Portal Logo Removed"),
				indicator="orange",
			)
			return

		file_doc = frappe.get_doc("File", name)
		file_doc.is_private = 0
		# ignore_permissions: the administrator editing VMS Settings is not necessarily
		# the owner of the uploaded File, and refusing to publish the logo they just
		# chose would reintroduce the silent 403 this method exists to prevent.
		file_doc.save(ignore_permissions=True)
		self.portal_logo = file_doc.file_url
		frappe.msgprint(
			_("Portal Logo was a private file and has been made public so visitors can load it."),
			title=_("Portal Logo Published"),
			indicator="blue",
		)

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
				_("Default Country Code must be 1–4 digits (for example 91), not '{0}'.").format(  # noqa: RUF001
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
		for idx, label, _start, _end in windows:
			key = label.casefold()
			if key in seen:
				frappe.throw(
					_(
						"Meal Label '{0}' is used twice (rows {1} and {2}). Each meal must appear once."
					).format(label, seen[key], idx),
					title=_("Duplicate Meal Window"),
				)
			seen[key] = idx

		for i, (_idx_a, label_a, start_a, end_a) in enumerate(windows):
			for _idx_b, label_b, start_b, end_b in windows[i + 1 :]:
				if start_a < end_b and start_b < end_a:
					frappe.throw(
						_("{0} ({1}–{2}) overlaps {3} ({4}–{5}). A visit would qualify for both.").format(  # noqa: RUF001
							label_a, start_a, end_a, label_b, start_b, end_b
						),
						title=_("Overlapping Meal Windows"),
					)
