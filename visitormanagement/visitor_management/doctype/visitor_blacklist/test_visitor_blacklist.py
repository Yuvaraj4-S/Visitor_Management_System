# See license.txt

import frappe
from frappe.tests import IntegrationTestCase

from visitormanagement.visitor_management.doctype.visitor_blacklist.visitor_blacklist import (
	VisitorBlacklist,
)


class TestVisitorBlacklist(IntegrationTestCase):
	"""Tests for Visitor Blacklist doctype validations and the find_active_match helper."""

	def setUp(self):
		# Clean any leftovers from prior runs
		frappe.db.delete(
			"Visitor Blacklist",
			{"id_proof_number": ["in", ["TEST-BL-NUM-1", "TEST-BL-NUM-2", "ABCD-1234-5678"]]},
		)
		frappe.db.delete(
			"Visitor Blacklist",
			{"visitor_name": ["in", ["Test Blacklist Name Only", "TEST BLACKLIST NAME ONLY"]]},
		)
		# Test fixture: persist the cleanup so each test starts from a known
		# state; tearDown rolls back test-created rows.
		frappe.db.commit()  # nosemgrep: frappe-manual-commit

	def tearDown(self):
		# Test fixture: discard rows created during the test.
		frappe.db.rollback()  # nosemgrep: frappe-manual-commit

	# ---------------- validations ----------------

	def test_reason_is_required(self):
		bl = frappe.new_doc("Visitor Blacklist")
		bl.id_proof_number = "TEST-BL-NUM-1"
		bl.is_active = 1
		with self.assertRaises(frappe.ValidationError) as ctx:
			bl.insert(ignore_permissions=True)
		self.assertIn("Reason", str(ctx.exception))

	def test_identification_is_required(self):
		bl = frappe.new_doc("Visitor Blacklist")
		bl.reason = "Test — no identification"
		bl.is_active = 1
		with self.assertRaises(frappe.ValidationError) as ctx:
			bl.insert(ignore_permissions=True)
		# Either "Identification Required" or message mentioning ID Proof
		msg = str(ctx.exception)
		self.assertTrue("Identification" in msg or "ID Proof" in msg)

	def test_duplicate_active_blacklist_blocked(self):
		frappe.get_doc({
			"doctype": "Visitor Blacklist",
			"reason": "Test entry 1",
			"id_proof_number": "TEST-BL-NUM-1",
			"is_active": 1,
		}).insert(ignore_permissions=True)
		dup = frappe.new_doc("Visitor Blacklist")
		dup.reason = "Test entry 2 (dup)"
		dup.id_proof_number = "TEST-BL-NUM-1"
		dup.is_active = 1
		with self.assertRaises(frappe.ValidationError) as ctx:
			dup.insert(ignore_permissions=True)
		self.assertIn("already exists", str(ctx.exception))

	def test_blocked_by_and_blocked_on_autostamp(self):
		bl = frappe.get_doc({
			"doctype": "Visitor Blacklist",
			"reason": "Stamping test",
			"id_proof_number": "TEST-BL-NUM-2",
			"is_active": 1,
		}).insert(ignore_permissions=True)
		bl.reload()
		self.assertTrue(bl.blocked_by)
		self.assertTrue(bl.blocked_on)

	# ---------------- find_active_match helper ----------------

	def test_find_match_by_id_proof_number(self):
		frappe.get_doc({
			"doctype": "Visitor Blacklist",
			"reason": "Match by number",
			"id_proof_number": "TEST-BL-NUM-1",
			"is_active": 1,
		}).insert(ignore_permissions=True)
		match = VisitorBlacklist.find_active_match(id_proof_number="TEST-BL-NUM-1")
		self.assertIsNotNone(match)

	def test_find_match_matches_when_name_and_mobile_both_corroborate(self):
		"""FIXED THIS SESSION: name and mobile no longer match independently —
		only when BOTH agree with the same row does a weak identifier pair
		count as a strong enough match to hard-block. See the threat-model
		docstring on VisitorBlacklist.find_active_match."""
		frappe.get_doc({
			"doctype": "Visitor Blacklist",
			"reason": "Corroborated match",
			"visitor_name": "Test Blacklist Name Only",
			"mobile_number": "+91 9000011111",
			"id_proof_type": "Aadhaar",
			"is_active": 1,
		}).insert(ignore_permissions=True)
		match = VisitorBlacklist.find_active_match(
			id_proof_number=None,
			visitor_name="Test Blacklist Name Only",
			mobile_number="+91 9000011111",
		)
		self.assertIsNotNone(match)

	def test_find_match_name_and_mobile_corroboration_is_case_and_spacing_insensitive(self):
		frappe.get_doc({
			"doctype": "Visitor Blacklist",
			"reason": "Case+spacing test",
			"visitor_name": "Test Blacklist Name Only",
			"mobile_number": "+91 9000022222",
			"id_proof_type": "Passport",
			"is_active": 1,
		}).insert(ignore_permissions=True)
		match = VisitorBlacklist.find_active_match(
			visitor_name="  test   blacklist NAME only  ",
			# Local form, no country code — _mobile_match_tail compares on the
			# national significant number, so this must still match the
			# stored "+91 ..." number.
			mobile_number="9000022222",
		)
		self.assertIsNotNone(match)

	def test_find_match_name_only_without_mobile_corroboration_does_not_block(self):
		"""THE BUG THIS SESSION FIXED: a name-only blacklist entry (no mobile
		on the row at all) used to hard-block anyone sharing that name alone —
		a real namesake, different ID, different phone. It must now return
		None; find_weak_match is the companion for warning instead of
		blocking (see below)."""
		frappe.get_doc({
			"doctype": "Visitor Blacklist",
			"reason": "Name-only blacklist",
			"visitor_name": "Test Blacklist Name Only",
			"id_proof_type": "Aadhaar",
			"is_active": 1,
		}).insert(ignore_permissions=True)
		match = VisitorBlacklist.find_active_match(
			id_proof_number="UNRELATED-ID-999",
			visitor_name="Test Blacklist Name Only",
			mobile_number="+91 9999999999",
		)
		self.assertIsNone(match, "a name-only blacklist entry must not hard-block on name alone")

	def test_find_match_mobile_only_without_name_corroboration_does_not_block(self):
		"""Same fix, the phone-only side: a mobile match with a DIFFERENT name
		must not hard-block either — phone numbers get reassigned and shared
		within a family."""
		frappe.get_doc({
			"doctype": "Visitor Blacklist",
			"reason": "Mobile-bearing blacklist entry",
			"visitor_name": "Test Blacklist Name Only",
			"mobile_number": "+91 9000033333",
			"id_proof_type": "Aadhaar",
			"is_active": 1,
		}).insert(ignore_permissions=True)
		match = VisitorBlacklist.find_active_match(
			visitor_name="Someone Completely Different",
			mobile_number="+91 9000033333",
		)
		self.assertIsNone(match, "mobile matching alone (different name) must not hard-block")

	def test_find_match_id_proof_number_still_blocks_despite_evasion(self):
		"""The one identifier strong enough to block alone, unaffected by this
		session's fix — still normalised for case/spacing/separator evasion."""
		frappe.get_doc({
			"doctype": "Visitor Blacklist",
			"reason": "Evasion test",
			"id_proof_number": "ABCD-1234-5678",
			"is_active": 1,
		}).insert(ignore_permissions=True)
		for variant in ("abcd12345678", "AB CD-1234 5678", "abcd-1234-5678", "ABCD/1234/5678"):
			with self.subTest(variant=variant):
				self.assertIsNotNone(VisitorBlacklist.find_active_match(id_proof_number=variant))

	# ---------------- find_weak_match: the non-blocking warning ----------------

	def test_find_weak_match_reports_a_name_hit(self):
		entry = frappe.get_doc({
			"doctype": "Visitor Blacklist",
			"reason": "Weak name test",
			"visitor_name": "Test Blacklist Name Only",
			"id_proof_type": "Aadhaar",
			"is_active": 1,
		}).insert(ignore_permissions=True)
		weak = VisitorBlacklist.find_weak_match(visitor_name="Test Blacklist Name Only")
		self.assertEqual(weak, {"name": entry.name, "matched_on": "name"})

	def test_find_weak_match_reports_a_mobile_hit(self):
		entry = frappe.get_doc({
			"doctype": "Visitor Blacklist",
			"reason": "Weak mobile test",
			"visitor_name": "Test Blacklist Name Only",
			"mobile_number": "+91 9000044444",
			"id_proof_type": "Aadhaar",
			"is_active": 1,
		}).insert(ignore_permissions=True)
		weak = VisitorBlacklist.find_weak_match(
			visitor_name="Nobody Matching This Name", mobile_number="+91 9000044444"
		)
		self.assertEqual(weak, {"name": entry.name, "matched_on": "mobile"})

	def test_find_weak_match_returns_none_when_neither_matches(self):
		self.assertIsNone(
			VisitorBlacklist.find_weak_match(
				visitor_name="Nobody At All", mobile_number="+91 9111111199"
			)
		)

	def test_find_match_inactive_entry_returns_none(self):
		frappe.get_doc({
			"doctype": "Visitor Blacklist",
			"reason": "Inactive",
			"id_proof_number": "TEST-BL-NUM-1",
			"is_active": 0,
		}).insert(ignore_permissions=True)
		match = VisitorBlacklist.find_active_match(id_proof_number="TEST-BL-NUM-1")
		self.assertIsNone(match)

	def test_find_match_no_input_returns_none(self):
		self.assertIsNone(VisitorBlacklist.find_active_match())
