# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from visitormanagement.visitor_management.doctype.visitor_blacklist.visitor_blacklist import (
	VisitorBlacklist,
)


class TestVisitorBlacklist(FrappeTestCase):
	"""Tests for Visitor Blacklist doctype validations and the find_active_match helper."""

	def setUp(self):
		# Clean any leftovers from prior runs
		frappe.db.delete(
			"Visitor Blacklist",
			{"id_proof_number": ["in", ["TEST-BL-NUM-1", "TEST-BL-NUM-2"]]},
		)
		frappe.db.delete(
			"Visitor Blacklist",
			{"visitor_name": ["in", ["Test Blacklist Name Only", "TEST BLACKLIST NAME ONLY"]]},
		)
		frappe.db.commit()

	def tearDown(self):
		frappe.db.rollback()

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

	def test_find_match_falls_back_to_name_and_type(self):
		frappe.get_doc({
			"doctype": "Visitor Blacklist",
			"reason": "Name-only blacklist",
			"visitor_name": "Test Blacklist Name Only",
			"id_proof_type": "Aadhaar",
			"is_active": 1,
		}).insert(ignore_permissions=True)
		match = VisitorBlacklist.find_active_match(
			id_proof_number=None,
			visitor_name="Test Blacklist Name Only",
			id_proof_type="Aadhaar",
		)
		self.assertIsNotNone(match)

	def test_find_match_name_lookup_is_case_insensitive(self):
		frappe.get_doc({
			"doctype": "Visitor Blacklist",
			"reason": "Case test",
			"visitor_name": "Test Blacklist Name Only",
			"id_proof_type": "Passport",
			"is_active": 1,
		}).insert(ignore_permissions=True)
		match = VisitorBlacklist.find_active_match(
			visitor_name="TEST BLACKLIST NAME ONLY",
			id_proof_type="Passport",
		)
		self.assertIsNotNone(match)

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
