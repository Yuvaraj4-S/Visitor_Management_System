# Copyright (c) 2026, Harthesh and Contributors
# See license.txt

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from visitormanagement.visitor_management.doctype.visitor_pass.visitor_pass import (
	get_customer_visit_details,
)


class TestVisitorPass(FrappeTestCase):
	def test_get_customer_visit_details_from_lead(self):
		lead_row = frappe._dict(
			lead_name="Ravi Kumar",
			mobile_no="+919876543210",
			email_id="ravi@example.com",
			company_name="Acme Industries",
			lead_owner="sales@example.com",
		)

		with patch(
			"visitormanagement.visitor_management.doctype.visitor_pass.visitor_pass.frappe.db.get_value",
			return_value=lead_row,
		), patch(
			"visitormanagement.visitor_management.doctype.visitor_pass.visitor_pass._get_employee_for_user",
			return_value="HR-EMP-00007",
		):
			details = get_customer_visit_details("Lead", "CRM-LEAD-0001")

		self.assertEqual(details["visitor_full_name"], "Ravi Kumar")
		self.assertEqual(details["mobile_number"], "+919876543210")
		self.assertEqual(details["email_id"], "ravi@example.com")
		self.assertEqual(details["company__organisation"], "Acme Industries")
		self.assertEqual(details["sales_executive"], "HR-EMP-00007")

	def test_get_customer_visit_details_from_opportunity_uses_contact(self):
		opportunity_row = frappe._dict(
			customer_name="Beta Manufacturing",
			contact_person="CONTACT-0001",
			contact_email="",
			contact_mobile="",
			opportunity_owner="owner@example.com",
			opportunity_from="Lead",
			party_name="CRM-LEAD-0002",
		)
		contact_row = frappe._dict(
			full_name="Anita Sharma",
			mobile_no="+919999888877",
			email_id="anita@example.com",
			company_name="Beta Manufacturing",
		)
		lead_row = frappe._dict(lead_name="Anita Sharma", company_name="Beta Manufacturing")

		def fake_get_value(doctype, name_or_filters, fieldname=None, as_dict=False):
			if doctype == "Opportunity":
				return opportunity_row
			if doctype == "Contact":
				return contact_row
			if doctype == "Lead":
				return lead_row
			return None

		with patch(
			"visitormanagement.visitor_management.doctype.visitor_pass.visitor_pass.frappe.db.get_value",
			side_effect=fake_get_value,
		), patch(
			"visitormanagement.visitor_management.doctype.visitor_pass.visitor_pass._get_employee_for_user",
			return_value="HR-EMP-00009",
		):
			details = get_customer_visit_details("Opportunity", "CRM-OPP-0001")

		self.assertEqual(details["visitor_full_name"], "Anita Sharma")
		self.assertEqual(details["mobile_number"], "+919999888877")
		self.assertEqual(details["email_id"], "anita@example.com")
		self.assertEqual(details["company__organisation"], "Beta Manufacturing")
		self.assertEqual(details["sales_executive"], "HR-EMP-00009")
