# Regression tests for the six high-severity fixes.
#
# Each test locks in one fix so it cannot silently regress:
#   #1/#6  custom_nationality defaults to the home country (portal + API paths)
#          so mandatory validation never blocks a submission / drops items.
#   #2     Visitor Pass badge_colour accepts the full 8-colour palette.
#   #3     Approval routes by the Visitor Type's approver_role, so custom
#          Visitor Types are submittable and approvable.
#   #4/#5  the duplicate "VMS Host Alert" / "VMS Food Dept Alert" notifications
#          stay disabled (the app code sends those emails once).
#
# IntegrationTestCase wraps each test in a transaction that is rolled back, so the
# test Visitor Types / Passes created here never persist.

import base64
import json

import frappe
from frappe.model.workflow import apply_workflow, get_transitions
from frappe.tests import IntegrationTestCase
from frappe.utils import nowdate

from visitormanagement.tests.test_regression import (
    _employee_for,
    _fresh_pan,
    _make_pass,
    _user_with_role,
)

PNG_DATA_URI = (
    "data:image/png;base64,"
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _home_country():
    return frappe.db.get_single_value("VMS Settings", "home_country") or "India"


def _active_host():
    return frappe.db.get_value("Employee", {"status": "Active"}, "name")


class TestNationalityDefault(IntegrationTestCase):
    """#1 + #6 — custom_nationality is mandatory; it must default to the home
    country so any creation path saves instead of failing mandatory validation."""

    def test_pass_without_nationality_defaults_to_home_country(self):
        host = _active_host()
        if not host:
            self.skipTest("no active Employee")
        vp = frappe.new_doc("Visitor Pass")
        vp.update({
            "visitor_type": "Contractor",
            "visitor_full_name": "HSF Nationality",
            "mobile_number": "+91 9876500001",
            "email_id": "hsf-nat@example.com",
            "id_proof_type": "PAN Card",
            "id_proof_number": _fresh_pan(),
            "id_proof_scan": PNG_DATA_URI,
            "visitor_photo": PNG_DATA_URI,
            "person_to_visit": host,
            "visit_date": nowdate(),
            "expected_checkin": "10:00:00",
            "expected_checkout": "17:00:00",
            "purpose_of_visit": "High-severity fix test",
        })
        # No custom_nationality supplied and NO ignore_mandatory: this used to
        # raise MandatoryError. The controller default must make it succeed.
        vp.insert(ignore_permissions=True)
        self.assertEqual(vp.custom_nationality, _home_country())

    def test_portal_submit_without_nationality_saves_items(self):
        host = _active_host()
        if not host:
            self.skipTest("no active Employee")
        from visitormanagement.visitor_management import portal

        payload = {
            "visitor_type": "Contractor",
            "entry_type": "New",
            "visitor_full_name": "HSF Portal Items",
            "mobile_number": "9876500002",
            "email_id": "hsf-portal@example.com",
            "id_proof_type": "PAN Card",
            "id_proof_number": _fresh_pan(),
            "id_proof_scan": PNG_DATA_URI,
            "visitor_photo": PNG_DATA_URI,
            "purpose_of_visit": "High-severity fix test",
            "person_to_visit": host,
            "visit_date": nowdate(),
            "expected_checkin": "10:00:00",
            "expected_checkout": "17:00:00",
            "submission_action": "submit",
            "visitor_items": [
                {"item_name": "Laptop", "quantity": 1},
                {"item_name": "Cable", "quantity": 2},
            ],
        }
        result = portal.submit_pre_registration(payload=json.dumps(payload))
        name = result["name"] if isinstance(result, dict) else result
        self.assertTrue(name, "portal submission returned no Visitor Pass")

        vp = frappe.get_doc("Visitor Pass", name)
        self.assertEqual(vp.custom_nationality, _home_country())
        self.assertEqual(len(vp.visitor_items), 2)
        self.assertIn("Laptop", vp.items_carried or "")


class TestBadgeColourPalette(IntegrationTestCase):
    """#2 — a Visitor Type using one of the new colours must not crash the pass."""

    def test_pass_of_blue_badge_type_saves(self):
        host = _active_host()
        if not host:
            self.skipTest("no active Employee")
        # field-level: the widen-options patch must have applied
        options = (frappe.get_meta("Visitor Pass").get_field("badge_colour").options or "").split("\n")
        self.assertIn("Blue", options)

        vt = frappe.get_doc({
            "doctype": "Visitor Type",
            "visitor_type_name": "HSF Blue Type",
            "approver_role": "System Manager",
            "badge_prefix": "HBL",
            "badge_colour": "Blue",
            "is_active": 1,
        })
        vt.insert(ignore_permissions=True)

        vp = _make_pass("HSF Blue Type", "HSF Blue Visitor", _fresh_pan(), host,
                        visit_date=nowdate())
        vp.reload()
        # before_save copies the type's colour onto the pass — this used to raise
        # a ValidationError because Blue was not an allowed pass option.
        self.assertEqual(vp.badge_colour, "Blue")


class TestCustomTypeApproval(IntegrationTestCase):
    """#3 — approval must route by the Visitor Type's approver_role so a custom
    type is submittable and approvable end-to-end."""

    def test_custom_visitor_type_routes_and_approves(self):
        employee = _user_with_role("Employee")
        approver = _user_with_role("System Manager")
        if not employee or not approver:
            self.skipTest("Employee/System Manager users missing")
        host = _employee_for(employee) or _active_host()

        frappe.get_doc({
            "doctype": "Visitor Type",
            "visitor_type_name": "HSF Delegate",
            "approver_role": "System Manager",
            "badge_prefix": "HDG",
            "badge_colour": "Green",
            "is_active": 1,
        }).insert(ignore_permissions=True)

        frappe.set_user(employee)
        try:
            vp = _make_pass("HSF Delegate", "HSF Custom Visitor", _fresh_pan(), host,
                            visit_date=nowdate())
            # the Submit transition must be available (it wasn't for custom types)
            actions = [t.action for t in get_transitions(vp)]
            self.assertIn("Submit", actions)
            apply_workflow(vp, "Submit")
        finally:
            frappe.set_user("Administrator")
        vp.reload()
        self.assertEqual(vp.workflow_state, "Pending System Manager")

        frappe.set_user(approver)
        try:
            apply_workflow(frappe.get_doc("Visitor Pass", vp.name), "Approve")
        finally:
            frappe.set_user("Administrator")
        vp.reload()
        self.assertEqual(vp.workflow_state, "Approved")
        self.assertEqual(vp.docstatus, 1)


class TestDuplicateNotificationsDisabled(IntegrationTestCase):
    """#4 + #5 — the notifications that duplicate the app's code emails must stay
    disabled so the host / kitchen each get exactly one email."""

    def test_host_and_food_notifications_disabled(self):
        for name in ("VMS Host Alert", "VMS Food Dept Alert"):
            if not frappe.db.exists("Notification", name):
                self.skipTest(f"{name} not present on this site")
            self.assertEqual(
                frappe.db.get_value("Notification", name, "enabled"), 0,
                f"{name} must be disabled (app code sends this email)",
            )
