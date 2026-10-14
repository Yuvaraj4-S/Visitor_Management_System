# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from visitormanagement.visitor_management.doctype.zone.zone import Zone


class TestZone(FrappeTestCase):
	"""Tests for Zone.is_visitor_type_allowed — the zone-based access restriction check."""

	def setUp(self):
		frappe.db.delete("Zone", {"zone_name": ["in", ["TEST-ZONE-1", "TEST-ZONE-2"]]})
		frappe.db.commit()  # nosemgrep: frappe-manual-commit

	def tearDown(self):
		frappe.db.rollback()  # nosemgrep: frappe-manual-commit

	def _make_zone(self, zone_name, rules):
		zone = frappe.new_doc("Zone")
		zone.zone_name = zone_name
		for rule in rules:
			zone.append("access_rules", rule)
		zone.insert(ignore_permissions=True)
		return zone

	def test_allows_matching_visitor_type_with_no_vip_category_restriction(self):
		zone = self._make_zone("TEST-ZONE-1", [{"visitor_type": "Contractor"}])
		self.assertTrue(Zone.is_visitor_type_allowed(zone.name, "Contractor"))

	def test_denies_visitor_type_with_no_matching_rule(self):
		zone = self._make_zone("TEST-ZONE-1", [{"visitor_type": "Contractor"}])
		self.assertFalse(Zone.is_visitor_type_allowed(zone.name, "Customer"))

	def test_denies_when_zone_has_no_rules_at_all(self):
		zone = self._make_zone("TEST-ZONE-1", [])
		self.assertFalse(Zone.is_visitor_type_allowed(zone.name, "Contractor"))

	def test_vip_category_specific_rule_matches_only_that_category(self):
		zone = self._make_zone(
			"TEST-ZONE-2",
			[{"visitor_type": "VIP", "vip_category": "Government Official"}],
		)
		self.assertTrue(
			Zone.is_visitor_type_allowed(zone.name, "VIP", "Government Official")
		)
		self.assertFalse(Zone.is_visitor_type_allowed(zone.name, "VIP", "Investor"))
		self.assertFalse(Zone.is_visitor_type_allowed(zone.name, "VIP", None))

	def test_blank_vip_category_rule_matches_any_vip_category(self):
		zone = self._make_zone("TEST-ZONE-2", [{"visitor_type": "VIP"}])
		self.assertTrue(Zone.is_visitor_type_allowed(zone.name, "VIP", "Investor"))
		self.assertTrue(Zone.is_visitor_type_allowed(zone.name, "VIP", None))

	def test_blank_zone_or_visitor_type_is_denied(self):
		self.assertFalse(Zone.is_visitor_type_allowed(None, "Contractor"))
		self.assertFalse(Zone.is_visitor_type_allowed("TEST-ZONE-1", None))
