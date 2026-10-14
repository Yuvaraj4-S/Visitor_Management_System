# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase

from visitormanagement.visitor_management.doctype.visitor_pass.visitor_pass import (
	_get_home_country,
)


class TestVMSSettings(FrappeTestCase):
	"""home_country regression guard — this field was referenced by visitor_pass.js
	and visitor_pass.py long before it actually existed on VMS Settings, which
	threw "Field home_country does not exist on VMS Settings" on every form
	refresh (frappe.db.get_single_value validates the field against the doctype
	meta). These tests confirm the field is real and the integration works."""

	def test_home_country_field_is_queryable(self):
		# Would raise frappe.exceptions.ValidationError ("Field ... does not
		# exist") if the field were still missing from the doctype.
		value = frappe.db.get_single_value("VMS Settings", "home_country")
		self.assertIsNotNone(value)

	def test_get_home_country_reads_configured_value(self):
		settings = frappe.get_single("VMS Settings")
		original = settings.home_country
		try:
			settings.home_country = "India"
			settings.save(ignore_permissions=True)
			frappe.clear_cache(doctype="VMS Settings")
			self.assertEqual(_get_home_country(), "India")
		finally:
			settings.reload()
			settings.home_country = original
			settings.save(ignore_permissions=True)
			frappe.clear_cache(doctype="VMS Settings")
