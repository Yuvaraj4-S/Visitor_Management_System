# See license.txt

"""VMS Settings — site-wide policy numbers, email/country validation, meal
windows, and the data-retention purge job's on/off switch.

This DocType shipped with zero test coverage even though `tasks.
purge_expired_visitor_data` — the one job on this site that permanently
destroys personal data (visitor names, mobiles, government ID numbers) — is
gated entirely by two fields here (`data_retention_enabled`,
`data_retention_days`). A site preparing for the Frappe Marketplace cannot
ship that job untested.
"""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, nowdate

from visitormanagement.tests.test_regression import _fresh_pan, _make_pass
from visitormanagement.visitor_management.doctype.vms_settings.vms_settings import POSITIVE_SETTINGS
from visitormanagement.visitor_management.tasks import _PURGE_MARKER_TEXT, purge_expired_visitor_data


def _settings():
	"""Fresh (uncached) VMS Settings doc — several tests deliberately push it
	into an invalid state and must never read back a stale cached copy."""
	return frappe.get_doc("VMS Settings")


# Fields these tests actually mutate. VMS Settings is a Single, and
# IntegrationTestCase only rolls the whole transaction back once at the end
# of the CLASS (`addClassCleanup`), not after each test method — so a save
# in one test (e.g. flooring every POSITIVE_SETTINGS field to 1) is still
# live for every alphabetically-later test in this same class. Confirmed
# empirically: without the snapshot/restore below,
# test_positive_settings_accept_the_floor_value's max_advance_booking_days=1
# survived into the purge tests and made _make_pass's ordinary 2-day-out
# visit_date fail as "more than 1 day in the future".
_SNAPSHOT_FIELDS = tuple(f for f, _label in POSITIVE_SETTINGS) + (
	"admin_email",
	"food_dept_email",
	"default_country_code",
	"data_retention_enabled",
)


class TestVMSSettings(IntegrationTestCase):
	def setUp(self):
		live = frappe.get_doc("VMS Settings")
		self._snapshot = {f: live.get(f) for f in _SNAPSHOT_FIELDS}
		self._snapshot_meal_windows = [
			{"meal_label": r.meal_label, "start_time": r.start_time, "end_time": r.end_time}
			for r in (live.meal_windows or [])
		]

	def tearDown(self):
		doc = frappe.get_doc("VMS Settings")
		doc.update(self._snapshot)
		doc.set("meal_windows", [])
		for row in self._snapshot_meal_windows:
			doc.append("meal_windows", row)
		doc.save(ignore_permissions=True)
		# `validate()` on VMS Settings runs against whatever is on the live
		# Singleton; clear its cache so the next test (in this file or any
		# other) never reads back a value this test only mutated in memory
		# or that the restore above just wrote.
		frappe.clear_document_cache("VMS Settings")

	# ---------------- numeric policy validators ----------------

	def test_positive_settings_reject_zero_and_negative(self):
		"""Every entry in POSITIVE_SETTINGS must floor at 1 — imported directly
		from vms_settings.py so this test can never silently drift out of sync
		with the fields the app actually enforces."""
		for fieldname, _label in POSITIVE_SETTINGS:
			for bad_value in (0, -1):
				with self.subTest(field=fieldname, value=bad_value):
					doc = _settings()
					setattr(doc, fieldname, bad_value)
					with self.assertRaises(frappe.ValidationError):
						doc.save()

	def test_positive_settings_accept_the_floor_value(self):
		doc = _settings()
		for fieldname, _label in POSITIVE_SETTINGS:
			setattr(doc, fieldname, 1)
		doc.save()  # must not raise
		self.assertEqual(doc.max_visit_duration_hrs, 1)

	# ---------------- email validators ----------------

	def test_invalid_admin_email_rejected(self):
		doc = _settings()
		doc.admin_email = "not-an-email"
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_invalid_food_dept_email_rejected(self):
		doc = _settings()
		doc.food_dept_email = "also not an email"
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_valid_emails_accepted(self):
		doc = _settings()
		doc.admin_email = "admin@example.com"
		doc.food_dept_email = "food@example.com"
		doc.save()  # must not raise

	# ---------------- country code validator ----------------

	def test_invalid_country_code_rejected(self):
		for bad in ("abc", "12345"):  # non-digit, and over the 4-digit ceiling
			with self.subTest(code=bad):
				doc = _settings()
				doc.default_country_code = bad
				with self.assertRaises(frappe.ValidationError):
					doc.save()

	def test_blank_country_code_is_not_an_error(self):
		"""Blank means "not configured", not invalid — `_validate_country_code`
		returns early on a blank value rather than rejecting it."""
		doc = _settings()
		doc.default_country_code = ""
		doc.save()  # must not raise

	def test_country_code_normalises_leading_plus(self):
		doc = _settings()
		doc.default_country_code = "+91"
		doc.save()
		self.assertEqual(doc.default_country_code, "91")

	# ---------------- meal window validation ----------------

	def _set_windows(self, rows):
		doc = _settings()
		doc.set("meal_windows", [])
		for label, start, end in rows:
			doc.append("meal_windows", {"meal_label": label, "start_time": start, "end_time": end})
		return doc

	def test_meal_window_end_before_start_rejected(self):
		doc = self._set_windows([("Brunch", "11:00:00", "10:00:00")])
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_overlapping_meal_windows_rejected(self):
		doc = self._set_windows(
			[
				("Breakfast", "08:00:00", "09:30:00"),
				("Brunch", "09:00:00", "10:30:00"),  # overlaps Breakfast 09:00-09:30
			]
		)
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_duplicate_meal_label_rejected(self):
		doc = self._set_windows(
			[
				("Lunch", "12:00:00", "13:00:00"),
				("Lunch", "18:00:00", "19:00:00"),
			]
		)
		with self.assertRaises(frappe.ValidationError):
			doc.save()

	def test_distinct_valid_meal_windows_accepted(self):
		doc = self._set_windows(
			[
				("Breakfast", "08:00:00", "09:00:00"),
				("Lunch", "13:00:00", "14:00:00"),
				("Dinner", "20:00:00", "21:30:00"),
			]
		)
		doc.save()  # must not raise
		self.assertEqual(len(doc.meal_windows), 3)

	# ---------------- data retention purge ----------------

	def _terminal_old_pass(self, host, days_old=40):
		"""An approved-then-checked-out pass backdated past any sane retention
		window. Built through the normal insert path (so it carries real
		identity data to anonymise) and then pushed into the past with
		db_set, the same technique test_new_features.py uses for the overstay
		job — validate() would refuse a visit_date this old outright."""
		vp = _make_pass("Contractor", "Retention Purge Tester", _fresh_pan(), host)
		frappe.db.set_value(
			"Visitor Pass",
			vp.name,
			{
				"visit_date": add_days(nowdate(), -days_old),
				"status": "Checked-Out",
			},
			update_modified=False,
		)
		return vp.name

	def _active_pass(self, host, days_old=40):
		"""Old by visit_date, but never advanced past Draft — must never be
		purged regardless of age, since the visit is not over."""
		vp = _make_pass("Contractor", "Retention Active Tester", _fresh_pan(), host)
		frappe.db.set_value(
			"Visitor Pass", vp.name, "visit_date", add_days(nowdate(), -days_old), update_modified=False
		)
		return vp.name

	def test_purge_does_nothing_when_disabled(self):
		host = frappe.db.get_value("Employee", {"status": "Active"}, "name")
		if not host:
			self.skipTest("no Active Employee on this site")

		frappe.db.set_single_value("VMS Settings", "data_retention_enabled", 0)
		frappe.clear_document_cache("VMS Settings")

		name = self._terminal_old_pass(host)
		before = frappe.db.get_value("Visitor Pass", name, "visitor_full_name")

		purged_count = purge_expired_visitor_data()

		self.assertEqual(purged_count, 0, "purge ran despite data_retention_enabled=0")
		self.assertEqual(
			frappe.db.get_value("Visitor Pass", name, "visitor_full_name"),
			before,
			"a disabled retention job must not touch any data",
		)

	def test_purge_anonymises_old_terminal_pass_but_spares_active_pass_and_blacklist(self):
		host = frappe.db.get_value("Employee", {"status": "Active"}, "name")
		if not host:
			self.skipTest("no Active Employee on this site")

		frappe.db.set_single_value(
			"VMS Settings", {"data_retention_enabled": 1, "data_retention_days": 30}
		)
		frappe.clear_document_cache("VMS Settings")

		purge_target = self._terminal_old_pass(host, days_old=40)
		active_pass = self._active_pass(host, days_old=40)

		blacklist = frappe.get_doc(
			{
				"doctype": "Visitor Blacklist",
				"reason": "Retention purge scope test",
				"id_proof_number": _fresh_pan(),
				"is_active": 1,
			}
		).insert(ignore_permissions=True)

		purge_expired_visitor_data()

		purged = frappe.db.get_value(
			"Visitor Pass", purge_target, ["visitor_full_name", "mobile_number", "id_proof_number"], as_dict=True
		)
		self.assertEqual(purged.visitor_full_name, _PURGE_MARKER_TEXT, "an old, terminal pass was not anonymised")
		self.assertIsNone(purged.mobile_number, "mobile_number should have been cleared by the purge")
		self.assertIsNone(purged.id_proof_number, "id_proof_number should have been cleared by the purge")

		still_active = frappe.db.get_value("Visitor Pass", active_pass, "visitor_full_name")
		self.assertEqual(
			still_active, "Retention Active Tester",
			"a pass that never left Draft must not be purged, however old its visit_date is",
		)

		bl_after = frappe.db.get_value("Visitor Blacklist", blacklist.name, "id_proof_number")
		self.assertEqual(
			bl_after, blacklist.id_proof_number,
			"Visitor Blacklist must never be touched by the retention purge",
		)
