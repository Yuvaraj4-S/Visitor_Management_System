# Copyright (c) 2026, Harthesh and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from visitormanagement.visitor_management import lifecycle
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

	def test_populate_hospitality_request_from_pass_copies_special_diet(self):
		visitor_pass = frappe._dict(
			visit_date="2026-04-09",
			expected_checkin="08:30:00",
			expected_checkout="14:30:00",
			refreshments_required=1,
			number_of_people=3,
			conference_room="Board Room",
			special_diet="Jain",
		)
		hospitality_request = SimpleNamespace()

		lifecycle.populate_hospitality_request_from_pass(
			hospitality_request,
			visitor_pass=visitor_pass,
		)

		self.assertEqual(hospitality_request.special_diet, "Jain")

	def test_sync_hospitality_to_pass_pushes_special_diet(self):
		request_doc = SimpleNamespace(
			name="HOSP-0001",
			visitor_pass="VP-0001",
			status="Confirmed",
			assigned_staff="HR-EMP-00001",
			conference_room="Board Room",
			service_time="2026-04-09 13:00:00",
			meal_required=1,
			meal_type="Lunch",
			assigned_meal_slots="Lunch",
			hospitality_type="Single Meal",
			special_diet="Vegan",
		)

		with patch(
			"visitormanagement.visitor_management.lifecycle.frappe.db.set_value"
		) as mock_set_value:
			lifecycle.sync_hospitality_to_pass(request_doc)

		mock_set_value.assert_called_once()
		pass_updates = mock_set_value.call_args.args[2]
		self.assertEqual(pass_updates["special_diet"], "Vegan")
