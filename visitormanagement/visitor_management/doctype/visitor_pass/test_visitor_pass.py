# Copyright (c) 2026, Harthesh and Contributors
# See license.txt

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from visitormanagement.visitor_management import lifecycle


class TestVisitorPass(FrappeTestCase):
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
