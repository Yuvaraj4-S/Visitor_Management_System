# See license.txt
"""Regression test suite for the Visitor Management app.

Run on its own:
    bench --site vms.local run-tests --module visitormanagement.tests.test_regression

Run a single class:
    bench --site vms.local run-tests --module visitormanagement.tests.test_regression \\
        --test TestVisitorPassValidation

Each test class targets one concern and uses FrappeTestCase, which wraps every
test method in a transaction that rolls back on completion — so test data
doesn't leak into the live DB.

If a required role/user is missing on the site, that specific test self-skips
with a clear message. Run `bench list-apps` and ensure roles like Sales Manager,
HR Manager, HOD, CEO, Security, Hospitality Manager are seeded before running.
"""

from __future__ import annotations

import base64
import datetime as _dt
from typing import Optional

import frappe
from frappe.model.workflow import apply_workflow
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, nowdate


# ─── Shared fixtures ─────────────────────────────────────────────────────
PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="
)

def _fresh_pan() -> str:
    """Generate a unique valid PAN: 5 letters + 4 digits + 1 letter.
    Using random data so per-test-method calls never collide with each other
    or with the live demo dataset's `_validate_duplicate_pass` guard."""
    import random as _random
    import string as _string
    letters = "".join(_random.choices(_string.ascii_uppercase, k=5))
    digits = "".join(_random.choices(_string.digits, k=4))
    letter = _random.choice(_string.ascii_uppercase)
    return f"{letters}{digits}{letter}"


def _file(label: str) -> str:
    f = frappe.get_doc({
        "doctype": "File",
        "file_name": f"reg_{label}.png",
        "content": PNG_BYTES,
        "decode": False,
        "is_private": 0,
    })
    f.flags.ignore_permissions = True
    f.insert()
    return f.file_url


def _user_with_role(role: str) -> Optional[str]:
    rows = frappe.db.sql(
        """SELECT u.name FROM `tabUser` u
           JOIN `tabHas Role` h ON h.parent = u.name AND h.parenttype = 'User'
           WHERE h.role = %s AND u.enabled = 1
           ORDER BY u.creation
           LIMIT 1""",
        (role,),
    )
    return rows[0][0] if rows else None


def _employee_for(user: str) -> Optional[str]:
    return frappe.db.get_value("Employee", {"user_id": user, "status": "Active"}, "name")


def _make_pass(visitor_type: str, visitor_full_name: str, id_number: str,
               host: str, visit_date=None, extra: Optional[dict] = None):
    visit_date = visit_date or add_days(nowdate(), 2)
    vp = frappe.new_doc("Visitor Pass")
    vp.update({
        "visitor_type": visitor_type,
        "visitor_full_name": visitor_full_name,
        "mobile_number": "+91 9876543210",
        "email_id": f"reg-{id_number.lower()}@example.com",
        "id_proof_type": "PAN Card",
        "id_proof_number": id_number,
        "id_proof_scan": _file(f"id_{id_number}"),
        "visitor_photo": _file(f"photo_{id_number}"),
        "person_to_visit": host,
        "visit_date": visit_date,
        "expected_checkin": "10:00:00",
        "expected_checkout": "17:00:00",
        "purpose_of_visit": "Regression test",
    })
    if extra:
        vp.update(extra)
    vp.flags.ignore_mandatory = True
    vp.insert()
    return vp


def _approve_pass(vp_name: str, visitor_type: str) -> None:
    """Walk a Visitor Pass to Approved state. Skips test if approver missing."""
    approver_role = {
        "Customer": "Sales Manager",
        "Candidate": "HR Manager",
        "Contractor": "System Manager",
        "Supplier": "System Manager",
        "VIP": "HOD",
    }[visitor_type]
    approver_user = _user_with_role(approver_role)
    if not approver_user:
        raise _SkipApproval(approver_role)
    owner = frappe.db.get_value("Visitor Pass", vp_name, "owner")
    frappe.set_user(owner)
    apply_workflow(frappe.get_doc("Visitor Pass", vp_name), "Submit")
    frappe.set_user(approver_user)
    apply_workflow(frappe.get_doc("Visitor Pass", vp_name), "Approve")
    if visitor_type == "VIP":
        ceo = _user_with_role("CEO")
        if not ceo:
            raise _SkipApproval("CEO")
        frappe.db.set_value("Visitor Pass", vp_name, "mdceo_notified", 1, update_modified=False)
        frappe.set_user(ceo)
        apply_workflow(frappe.get_doc("Visitor Pass", vp_name), "Approve")
    frappe.set_user("Administrator")


class _SkipApproval(Exception):
    """Raised when a required approver role has no user on the site."""


# ─── 1. Permission matrix — locks in the design intent for each role ─────
class TestPermissionMatrix(FrappeTestCase):
    """Each role × doctype combination is the contract this app ships with.
    These checks are role-level (no doc filter)."""

    def _check(self, doctype: str, role: str, expected: dict):
        user = _user_with_role(role)
        if not user:
            self.skipTest(f"no user with role {role!r}")
        for ptype, want in expected.items():
            got = frappe.permissions.has_permission(doctype, ptype, user=user)
            self.assertEqual(
                got, want,
                f"{role}/{user} on {doctype}.{ptype}: expected {want}, got {got}",
            )

    def test_employee_can_create_hospitality_request(self):
        self._check("Hospitality Request", "Employee", {"read": True, "write": True, "create": True})

    def test_employee_can_create_conference_room_booking(self):
        self._check(
            "Conference Room Booking", "Employee",
            {"read": True, "write": True, "create": True},
        )

    def test_employee_can_read_page(self):
        # Resolves the misleading "No permission for Page" error path.
        self._check("Page", "Employee", {"read": True})

    def test_hospitality_manager_has_full_hr_perms(self):
        self._check(
            "Hospitality Request", "Hospitality Manager",
            {"read": True, "write": True, "create": True, "submit": True},
        )

    def test_facility_manager_has_full_crb_perms(self):
        self._check(
            "Conference Room Booking", "Facility Manager",
            {"read": True, "write": True, "create": True, "submit": True},
        )

    def test_security_can_read_visitor_pass(self):
        # Security must be able to see approved passes at the role level.
        self._check("Visitor Pass", "Security", {"read": True})

    def test_security_python_hook_is_read_only_on_visitor_pass(self):
        """`has_visitor_pass_permission` must NOT short-circuit `True` for write/
        submit when the user only has Security role. Otherwise Security can
        modify approved passes via the per-doc hook even though the role-level
        DocPerm is read-only."""
        from visitormanagement.permissions import has_visitor_pass_permission

        sec = _user_with_role("Security")
        if not sec:
            self.skipTest("no Security user")

        class _StubApprovedCustomer:
            owner = "someone-else@example.com"
            person_to_visit = "HR-EMP-DOES-NOT-EXIST"
            visitor_type = "Customer"
            status = "Approved"
            docstatus = 1
            def get(self, key, default=None):
                return getattr(self, key, default)

        stub = _StubApprovedCustomer()
        self.assertTrue(has_visitor_pass_permission(stub, sec, "read"),
                         "Security must read approved passes")
        self.assertFalse(has_visitor_pass_permission(stub, sec, "write"),
                          "Security must NOT have write on approved passes")
        self.assertFalse(has_visitor_pass_permission(stub, sec, "create"),
                          "Security must NOT have create on approved passes")
        self.assertFalse(has_visitor_pass_permission(stub, sec, "submit"),
                          "Security must NOT have submit on approved passes")


# ─── 2. Visitor Pass validations — bad input is rejected ─────────────────
class TestVisitorPassValidation(FrappeTestCase):

    def setUp(self):
        self.host = frappe.db.get_value("Employee", {"status": "Active"}, "name")
        if not self.host:
            self.skipTest("no active Employee to use as host")

    def _build_payload(self, **overrides):
        payload = {
            "doctype": "Visitor Pass",
            "visitor_type": "Customer",
            "visitor_full_name": "Reg Test",
            "mobile_number": "+91 9876543210",
            "email_id": "reg-test@example.com",
            "id_proof_type": "PAN Card",
            "id_proof_number": "ABCDE1234F",
            "id_proof_scan": _file("v_id"),
            "visitor_photo": _file("v_photo"),
            "person_to_visit": self.host,
            "visit_date": add_days(nowdate(), 2),
            "expected_checkin": "10:00:00",
            "expected_checkout": "17:00:00",
            "purpose_of_visit": "Regression test",
        }
        payload.update(overrides)
        return payload

    def test_past_visit_date_rejected(self):
        payload = self._build_payload(visit_date=add_days(nowdate(), -1))
        with self.assertRaises(frappe.ValidationError):
            doc = frappe.get_doc(payload)
            doc.flags.ignore_mandatory = True
            doc.insert()

    def test_far_future_visit_date_rejected(self):
        payload = self._build_payload(visit_date=add_days(nowdate(), 100))
        with self.assertRaises(frappe.ValidationError):
            doc = frappe.get_doc(payload)
            doc.flags.ignore_mandatory = True
            doc.insert()

    def test_aadhaar_format_rejected(self):
        # Aadhaar must be exactly 12 digits — any non-12-digit input is rejected.
        # (Whether the live system enforces the Verhoeff checksum varies; this
        # test exercises the format check that is always present.)
        payload = self._build_payload(id_proof_type="Aadhaar", id_proof_number="abc-not-12-digits")
        with self.assertRaises(frappe.ValidationError):
            doc = frappe.get_doc(payload)
            doc.flags.ignore_mandatory = True
            doc.insert()

    def test_invalid_pan_format_rejected(self):
        payload = self._build_payload(id_proof_number="NOT-A-VALID-PAN")
        with self.assertRaises(frappe.ValidationError):
            doc = frappe.get_doc(payload)
            doc.flags.ignore_mandatory = True
            doc.insert()

    def test_invalid_email_rejected(self):
        payload = self._build_payload(email_id="not-an-email")
        with self.assertRaises(frappe.ValidationError):
            doc = frappe.get_doc(payload)
            doc.flags.ignore_mandatory = True
            doc.insert()

    def test_checkin_after_checkout_rejected(self):
        payload = self._build_payload(expected_checkin="18:00:00", expected_checkout="10:00:00")
        with self.assertRaises(frappe.ValidationError):
            doc = frappe.get_doc(payload)
            doc.flags.ignore_mandatory = True
            doc.insert()

    def test_duplicate_id_on_same_date_rejected(self):
        first = frappe.get_doc(self._build_payload(id_proof_number="DUPID1234A"))
        first.flags.ignore_mandatory = True
        first.insert()
        with self.assertRaises(frappe.ValidationError):
            second = frappe.get_doc(self._build_payload(
                id_proof_number="DUPID1234A",
                email_id="dup-second@example.com",
            ))
            second.flags.ignore_mandatory = True
            second.insert()


# ─── 3. Visitor Pass approval workflow per visitor type ──────────────────
class TestVisitorPassWorkflow(FrappeTestCase):

    def _run_chain(self, visitor_type: str, id_key: str):
        owner_user = _user_with_role("Employee")
        if not owner_user:
            self.skipTest("no Employee user available")
        host = _employee_for(owner_user) or frappe.db.get_value("Employee", {"status": "Active"}, "name")
        frappe.set_user(owner_user)
        try:
            vp = _make_pass(visitor_type, f"Reg {visitor_type}", _fresh_pan(), host)
        finally:
            frappe.set_user("Administrator")
        try:
            _approve_pass(vp.name, visitor_type)
        except _SkipApproval as e:
            self.skipTest(f"approver role {e.args[0]!r} has no user on the site")
        vp.reload()
        self.assertEqual(vp.workflow_state, "Approved")
        self.assertEqual(vp.docstatus, 1)

    def test_customer_chain(self):
        self._run_chain("Customer", "Customer")

    def test_candidate_chain(self):
        self._run_chain("Candidate", "Candidate")

    def test_contractor_chain(self):
        self._run_chain("Contractor", "Contractor")

    def test_supplier_chain(self):
        self._run_chain("Supplier", "Supplier")

    def test_vip_chain_requires_mdceo_notified(self):
        owner_user = _user_with_role("Employee")
        if not owner_user:
            self.skipTest("no Employee user")
        hod = _user_with_role("HOD")
        ceo = _user_with_role("CEO")
        if not hod or not ceo:
            self.skipTest("HOD/CEO users missing")
        host = _employee_for(owner_user) or frappe.db.get_value("Employee", {"status": "Active"}, "name")
        frappe.set_user(owner_user)
        vp = _make_pass("VIP", "Reg VIP", _fresh_pan(), host)
        apply_workflow(vp, "Submit")
        frappe.set_user("Administrator")

        frappe.set_user(hod)
        apply_workflow(frappe.get_doc("Visitor Pass", vp.name), "Approve")
        frappe.set_user("Administrator")
        vp.reload()
        self.assertEqual(vp.workflow_state, "Pending CEO")

        # CEO can't approve until mdceo_notified=1
        frappe.set_user(ceo)
        with self.assertRaises(frappe.ValidationError):
            apply_workflow(frappe.get_doc("Visitor Pass", vp.name), "Approve")
        frappe.set_user("Administrator")

        frappe.db.set_value("Visitor Pass", vp.name, "mdceo_notified", 1, update_modified=False)
        frappe.set_user(ceo)
        apply_workflow(frappe.get_doc("Visitor Pass", vp.name), "Approve")
        frappe.set_user("Administrator")
        vp.reload()
        self.assertEqual(vp.workflow_state, "Approved")
        self.assertEqual(vp.docstatus, 1)


# ─── 4. Hospitality Request flow + visitor-pass-approved gate ────────────
class TestHospitalityRequestFlow(FrappeTestCase):

    def setUp(self):
        self.employee_user = _user_with_role("Employee")
        self.hospmgr_user = _user_with_role("Hospitality Manager")
        if not self.employee_user or not self.hospmgr_user:
            self.skipTest("Employee or Hospitality Manager user missing")
        host = _employee_for(self.employee_user) or frappe.db.get_value("Employee", {"status": "Active"}, "name")
        frappe.set_user(self.employee_user)
        try:
            self.draft_vp = _make_pass("Customer", "Reg HR Draft", _fresh_pan(), host)
            self.approved_vp = _make_pass("Customer", "Reg HR Appr", _fresh_pan(), host)
        finally:
            frappe.set_user("Administrator")
        try:
            _approve_pass(self.approved_vp.name, "Customer")
        except _SkipApproval as e:
            self.skipTest(f"approver role {e.args[0]!r} has no user on the site")

    def _new_hr(self, vp_name):
        hr = frappe.new_doc("Hospitality Request")
        hr.visitor_pass = vp_name
        hr.meal_required = 1
        hr.meal_type = "Lunch"
        return hr

    def test_employee_creates_hr_in_draft(self):
        frappe.set_user(self.employee_user)
        try:
            hr = self._new_hr(self.draft_vp.name)
            hr.insert()
            self.assertEqual(hr.workflow_state or "Draft", "Draft")
        finally:
            frappe.set_user("Administrator")

    def test_employee_cannot_submit_while_visitor_pass_is_draft(self):
        # Real-world rule: hospitality prep can't begin until visitor confirmed.
        frappe.set_user(self.employee_user)
        try:
            hr = self._new_hr(self.draft_vp.name)
            hr.insert()
            with self.assertRaises(frappe.ValidationError) as ctx:
                apply_workflow(hr, "Submit for Approval")
            self.assertIn("Visitor Pass", str(ctx.exception))
        finally:
            frappe.set_user("Administrator")

    def test_employee_can_submit_when_visitor_pass_is_approved(self):
        frappe.set_user(self.employee_user)
        try:
            hr = self._new_hr(self.approved_vp.name)
            hr.insert()
            apply_workflow(hr, "Submit for Approval")
            hr.reload()
            self.assertEqual(hr.workflow_state, "Pending Manager Approval")
        finally:
            frappe.set_user("Administrator")

    def test_hospitality_manager_approves(self):
        frappe.set_user(self.employee_user)
        try:
            hr = self._new_hr(self.approved_vp.name)
            hr.insert()
            apply_workflow(hr, "Submit for Approval")
        finally:
            frappe.set_user("Administrator")
        frappe.set_user(self.hospmgr_user)
        try:
            apply_workflow(frappe.get_doc("Hospitality Request", hr.name), "Approve")
        finally:
            frappe.set_user("Administrator")
        hr.reload()
        self.assertEqual(hr.workflow_state, "Approved")


# ─── 5. Conference Room Booking gate ─────────────────────────────────────
class TestConferenceRoomBookingFlow(FrappeTestCase):

    def setUp(self):
        self.employee_user = _user_with_role("Employee")
        if not self.employee_user:
            self.skipTest("no Employee user")
        room = frappe.db.get_value("Conference Room", {"is_active": 1}, "name")
        if not room:
            self.skipTest("no active Conference Room")
        self.room = room
        host = _employee_for(self.employee_user) or frappe.db.get_value("Employee", {"status": "Active"}, "name")
        self.host = host
        frappe.set_user(self.employee_user)
        try:
            self.draft_vp = _make_pass("Customer", "Reg CRB Draft", _fresh_pan(), host)
            self.approved_vp = _make_pass("Customer", "Reg CRB Appr", _fresh_pan(), host)
        finally:
            frappe.set_user("Administrator")
        try:
            _approve_pass(self.approved_vp.name, "Customer")
        except _SkipApproval as e:
            self.skipTest(f"approver role {e.args[0]!r} has no user on the site")

    def _build_crb(self, visitor_pass_name: Optional[str]):
        # Randomise the slot so we don't collide with the live demo bookings
        # or with another test in the same suite that hasn't been rolled back yet.
        import random as _random
        booking_offset = _random.randint(15, 75)
        start_hour = _random.randint(13, 17)
        crb = frappe.new_doc("Conference Room Booking")
        crb.update({
            "meeting_title": "Reg CRB",
            "conference_room": self.room,
            "meeting_type": "External" if visitor_pass_name else "Internal",
            "booking_date": add_days(nowdate(), booking_offset),
            "start_time": f"{start_hour:02d}:00:00",
            "end_time": f"{start_hour + 1:02d}:00:00",
            "booked_by": self.host,
            "expected_attendees": 4,
            "visitor_pass": visitor_pass_name,
        })
        crb.flags.ignore_mandatory = True
        return crb

    def test_internal_booking_workflow_works(self):
        frappe.set_user(self.employee_user)
        try:
            crb = self._build_crb(None)
            crb.insert()
            apply_workflow(crb, "Submit")
            crb.reload()
            self.assertEqual(crb.workflow_state, "Pending Approval")
        finally:
            frappe.set_user("Administrator")

    def test_visitor_linked_booking_blocked_while_vp_is_draft(self):
        # Same gate as Hospitality Request: room prep can't start until VP is confirmed.
        frappe.set_user(self.employee_user)
        try:
            crb = self._build_crb(self.draft_vp.name)
            crb.insert()
            with self.assertRaises(frappe.ValidationError) as ctx:
                apply_workflow(crb, "Submit")
            self.assertIn("Visitor Pass", str(ctx.exception))
        finally:
            frappe.set_user("Administrator")

    def test_visitor_linked_booking_with_approved_vp_works(self):
        frappe.set_user(self.employee_user)
        try:
            crb = self._build_crb(self.approved_vp.name)
            crb.insert()
            apply_workflow(crb, "Submit")
            crb.reload()
            self.assertEqual(crb.workflow_state, "Pending Approval")
        finally:
            frappe.set_user("Administrator")


# ─── 6. Search helper for the existing_visitor_pass dropdown ─────────────
class TestSearchHelpers(FrappeTestCase):

    def test_existing_visitor_pass_dropdown_returns_phone(self):
        from visitormanagement.visitor_management.doctype.visitor_pass.visitor_pass import (
            search_visitor_passes,
        )
        host = frappe.db.get_value("Employee", {"status": "Active"}, "name")
        if not host:
            self.skipTest("no host Employee")
        vp = _make_pass("Customer", "Reg DropDown", _fresh_pan(), host,
                        extra={"mobile_number": "+91 9101010101"})
        rows = search_visitor_passes(
            "Visitor Pass", "", "name", 0, 100, {"visitor_type": "Customer"},
        )
        match = next((r for r in rows if r[0] == vp.name), None)
        self.assertIsNotNone(match, f"created pass {vp.name} not in dropdown rows")
        self.assertIn("Reg DropDown", match[1])
        self.assertIn("9101010101", match[1].replace(" ", "").replace("-", ""))
        # The dropdown is computed live with CONCAT_WS — the chosen separator is
        # the middle-dot (`·`). If anyone reverts to the cached `visitor_summary`
        # column the separator becomes `|` and this fails — that's the regression
        # we want to catch.
        self.assertIn(" · ", match[1],
                      f"dropdown should use CONCAT_WS with `·` separator, got: {match[1]!r}")

    def test_existing_visitor_pass_dropdown_phone_search(self):
        from visitormanagement.visitor_management.doctype.visitor_pass.visitor_pass import (
            search_visitor_passes,
        )
        host = frappe.db.get_value("Employee", {"status": "Active"}, "name")
        if not host:
            self.skipTest("no host Employee")
        vp = _make_pass("Customer", "Reg PhoneSearch", _fresh_pan(), host,
                        extra={"mobile_number": "+91 8202020202"})
        rows = search_visitor_passes(
            "Visitor Pass", "8202020202", "name", 0, 50, {"visitor_type": "Customer"},
        )
        names = [r[0] for r in rows]
        self.assertIn(vp.name, names)


# ─── 7. Lifecycle helper robustness ──────────────────────────────────────
class TestLifecycleHelpers(FrappeTestCase):
    """Locks in the strong contract for `_combine_visit_datetime`:
    accepts every shape the system might pass it (time string, full datetime
    string, `datetime.time`, `timedelta`) and always returns a tz-naive
    datetime — so meal-window comparisons in `_overlaps_time_window` never
    crash with `can't compare offset-naive and offset-aware datetimes`."""

    def test_combine_visit_datetime_handles_all_input_shapes(self):
        from visitormanagement.visitor_management.lifecycle import _combine_visit_datetime
        d = nowdate()
        cases = [
            ("HH:MM:SS string",  "10:00:00"),
            ("datetime string",  f"{d} 10:00:00"),
            ("datetime.time",    _dt.time(10, 0, 0)),
            ("timedelta",        _dt.timedelta(hours=10)),
        ]
        for label, t in cases:
            with self.subTest(input=label):
                r = _combine_visit_datetime(d, t)
                self.assertIsNotNone(r, f"unexpected None for {label!r}")
                self.assertEqual(r.hour, 10, f"hour mismatch for {label!r}: {r}")
                self.assertEqual(r.minute, 0, f"minute mismatch for {label!r}: {r}")
                self.assertIsNone(
                    r.tzinfo,
                    f"result must be tz-naive for {label!r}; got tzinfo={r.tzinfo!r}",
                )

    def test_combine_visit_datetime_handles_none(self):
        from visitormanagement.visitor_management.lifecycle import _combine_visit_datetime
        self.assertIsNone(_combine_visit_datetime(None, "10:00:00"))
        self.assertIsNone(_combine_visit_datetime(nowdate(), None))

    def test_overlaps_time_window_does_not_crash_on_mixed_inputs(self):
        # Was the original lifecycle bug: comparing tz-aware vs tz-naive
        # datetimes raised TypeError. With the helper normalising every input
        # shape this comparison is now safe.
        from visitormanagement.visitor_management.lifecycle import (
            _combine_visit_datetime,
            _overlaps_time_window,
        )
        d = nowdate()
        slot_start = _combine_visit_datetime(d, "08:00:00")
        slot_end = _combine_visit_datetime(d, "09:00:00")
        visit_start = _combine_visit_datetime(d, f"{d} 08:30:00")
        visit_end = _combine_visit_datetime(d, f"{d} 09:30:00")
        # Should return True/False, never raise
        self.assertTrue(_overlaps_time_window(visit_start, visit_end, slot_start, slot_end))

    def test_overlaps_time_window_returns_false_for_disjoint_windows(self):
        from visitormanagement.visitor_management.lifecycle import (
            _combine_visit_datetime,
            _overlaps_time_window,
        )
        d = nowdate()
        slot = (_combine_visit_datetime(d, "08:00:00"), _combine_visit_datetime(d, "09:00:00"))
        visit = (_combine_visit_datetime(d, "10:00:00"), _combine_visit_datetime(d, "11:00:00"))
        self.assertFalse(_overlaps_time_window(visit[0], visit[1], slot[0], slot[1]))


# ─── 8. Auto-creation: meal-flagged pass triggers a Hospitality Request ──
class TestAutoCreation(FrappeTestCase):

    def test_meal_required_pass_creates_hospitality_request(self):
        owner = _user_with_role("Employee")
        if not owner:
            self.skipTest("no Employee user")
        host = _employee_for(owner) or frappe.db.get_value("Employee", {"status": "Active"}, "name")
        frappe.set_user(owner)
        try:
            vp = _make_pass(
                "Customer", "Reg Auto-HR", _fresh_pan(), host,
                extra={"meal_required": 1, "meal_type": "Lunch"},
            )
        finally:
            frappe.set_user("Administrator")
        try:
            _approve_pass(vp.name, "Customer")
        except _SkipApproval as e:
            self.skipTest(f"approver role {e.args[0]!r} has no user on the site")
        hr_name = frappe.db.get_value("Hospitality Request", {"visitor_pass": vp.name}, "name")
        self.assertIsNotNone(hr_name, "approval of meal-flagged pass should auto-create HR")


# ─── 9. Workspace + key doctype loadability (smoke check) ────────────────
class TestWorkspaceAndDoctypes(FrappeTestCase):
    """Smoke: every doctype this app cares about loads its meta successfully.
    Catches workspace/JSON breakage that doesn't surface until a form view
    is rendered."""

    DOCTYPES = [
        "Visitor Pass", "Visitor Invitation",
        "Security Log", "Hospitality Request",
        "Conference Room", "Conference Room Booking",
        "Visitor Blacklist", "Visitor Event Log",
        "Contact Trace Record",
        "VMS Settings",
    ]

    def test_every_active_doctype_meta_loads(self):
        for dt in self.DOCTYPES:
            with self.subTest(doctype=dt):
                meta = frappe.get_meta(dt)
                self.assertIsNotNone(meta)
                self.assertGreater(len(meta.fields), 0,
                                    f"{dt} has no fields — schema corruption?")

    def test_visitor_management_workspace_exists(self):
        self.assertTrue(frappe.db.exists("Workspace", "Visitor Management"))
        ws = frappe.get_doc("Workspace", "Visitor Management")
        self.assertGreater(len(ws.links), 0)

    def test_visitor_pass_workflow_active(self):
        self.assertTrue(frappe.db.exists("Workflow", "Visitor Pass Approval"))
        w = frappe.get_doc("Workflow", "Visitor Pass Approval")
        self.assertEqual(w.is_active, 1)

    def test_hospitality_request_workflow_active(self):
        self.assertTrue(frappe.db.exists("Workflow", "Hospitality Request Approval"))
        w = frappe.get_doc("Workflow", "Hospitality Request Approval")
        self.assertEqual(w.is_active, 1)

    def test_conference_room_booking_workflow_active(self):
        self.assertTrue(frappe.db.exists("Workflow", "Conference Room Booking Approval"))
        w = frappe.get_doc("Workflow", "Conference Room Booking Approval")
        self.assertEqual(w.is_active, 1)


# ─── 10. Removed features must stay removed ──────────────────────────────
class TestRemovedFeatures(FrappeTestCase):
    """Health Screening was deliberately removed. These tests fail loudly if
    anyone re-adds the doctype, table, columns, or symbols. Same for the
    five orphan visit-type doctype folders."""

    def test_health_screening_doctype_gone(self):
        self.assertFalse(
            frappe.db.exists("DocType", "Health Screening"),
            "Health Screening DocType must stay removed",
        )

    def test_health_screening_table_gone(self):
        self.assertEqual(
            frappe.db.sql("SHOW TABLES LIKE 'tabHealth Screening'"),
            (),
            "tabHealth Screening table must stay dropped",
        )

    def test_no_health_screening_columns_on_related_tables(self):
        for table, column in [
            ("tabVisitor Pass", "last_health_screening"),
            ("tabVisitor Pass", "health_screening_status"),
            ("tabSecurity Log", "health_screening"),
            ("tabSecurity Log", "health_screening_status"),
        ]:
            with self.subTest(column=f"{table}.{column}"):
                cols = frappe.db.sql(f"SHOW COLUMNS FROM `{table}` LIKE %s", (column,))
                self.assertEqual(cols, (), f"{table}.{column} should stay dropped")

    def test_no_orphan_visit_type_doctypes(self):
        for orphan in ("Customer Visit", "Candidate Visit", "Contractor Visit",
                       "Supplier Visit", "VIP Visit"):
            self.assertFalse(
                frappe.db.exists("DocType", orphan),
                f"{orphan} was removed and must stay removed",
            )

    def test_lifecycle_no_health_screening_symbols(self):
        from visitormanagement.visitor_management import lifecycle
        for symbol in ("sync_health_screening", "derive_health_screening_status",
                       "HEALTH_SCREENING_OK_STATUSES",
                       "HEALTH_REVIEW_TEMPERATURE", "HEALTH_DENY_TEMPERATURE"):
            with self.subTest(symbol=symbol):
                self.assertFalse(
                    hasattr(lifecycle, symbol),
                    f"lifecycle.{symbol} should be removed",
                )

    def test_no_workspace_link_to_health_screening(self):
        self.assertFalse(
            frappe.db.exists("Workspace Link", {"link_to": "Health Screening"}),
            "Workspace must not link to the removed Health Screening doctype",
        )

    def test_no_dashboard_or_card_referencing_health_screening(self):
        for child_dt in ("Number Card", "Dashboard Chart"):
            with self.subTest(doctype=child_dt):
                rows = frappe.get_all(child_dt, filters={"document_type": "Health Screening"}, pluck="name")
                self.assertEqual(rows, [], f"{child_dt} must not reference Health Screening")
