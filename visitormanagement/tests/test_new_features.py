# See license.txt
"""Tests for the features added in commit d09ac12 that had NO coverage of any kind.

The Playwright suite in ~/qa-testing-agent proves nothing *regressed*. It does not
prove this batch's new work functions, because none of these six behaviours existed
when those flows were written:

    Cancel transition · overstay detection · duplicate-match by mobile digits ·
    fever threshold · portal submission rate limit · items verification

Three of them cannot be driven through a browser at all — the overstay job is an
hourly scheduled task, the fever threshold feeds a server-side risk calculation, and
the rate limit is enforced before any page renders. Those belong here rather than in
a UI flow, which is why this module exists alongside the harness rather than
duplicating it.

Run on its own:
    bench --site site.local run-tests --module visitormanagement.tests.test_new_features

Helpers are imported from test_regression rather than re-written, so a change to how
a Visitor Pass is built or approved only has to be made in one place.
"""

from __future__ import annotations

import frappe
from frappe.model.workflow import apply_workflow
from frappe.tests import IntegrationTestCase
from frappe.utils import add_to_date, now_datetime

from visitormanagement.tests.test_regression import (
    _SkipApproval,
    _approve_pass,
    _fresh_pan,
    _make_pass,
    _user_with_role,
)


def _host_employee():
    """Any Active Employee to hang a test pass off, or None to self-skip."""
    return frappe.db.get_value("Employee", {"status": "Active"}, "name")


class TestOverstayDetection(IntegrationTestCase):
    """`tasks.flag_overstaying_visitors` — the mirror of the no-show job.

    Nothing chased the visitor who arrived and never left, so "who is in the
    building right now" drifted. On the site this was written against, 17 people
    were shown as on the premises, the oldest for 25 days.
    """

    def setUp(self):
        self.host = _host_employee()
        if not self.host:
            self.skipTest("no Active Employee on this site")

    def _checked_in_pass(self, hours_ago: int):
        """An Approved pass sitting at Checked-In since `hours_ago`."""
        vp = _make_pass("Contractor", "Overstay Tester", _fresh_pan(), self.host)
        try:
            _approve_pass(vp.name, "Contractor")
        except _SkipApproval as exc:
            self.skipTest(f"no user holding {exc} on this site")
        # db_set rather than save(): `status` and `actual_checkin` are normally
        # written by the gate through Security Log, and going through that whole
        # path would test the gate, not the overstay scan.
        frappe.db.set_value(
            "Visitor Pass",
            vp.name,
            {
                "status": "Checked-In",
                "actual_checkin": add_to_date(now_datetime(), hours=-hours_ago),
            },
            update_modified=False,
        )
        return vp.name

    def test_visitor_past_the_limit_is_flagged(self):
        from visitormanagement.visitor_management.tasks import flag_overstaying_visitors

        name = self._checked_in_pass(hours_ago=48)
        flag_overstaying_visitors()
        self.assertTrue(
            frappe.db.exists("Visitor Event Log", {"visitor_pass": name, "event_type": "Overstay"}),
            "a visitor 48h past check-in was not flagged as overstaying",
        )

    def test_visitor_inside_the_limit_is_not_flagged(self):
        from visitormanagement.visitor_management.tasks import flag_overstaying_visitors

        # One hour in. Below any sane Max Visit Duration, including the 12h fallback.
        name = self._checked_in_pass(hours_ago=1)
        flag_overstaying_visitors()
        self.assertFalse(
            frappe.db.exists("Visitor Event Log", {"visitor_pass": name, "event_type": "Overstay"}),
            "a visitor only 1h into their visit was wrongly flagged as overstaying",
        )

    def test_second_run_does_not_re_flag(self):
        """The guard that stops an hourly job re-mailing the same standing list.

        Without it security would get the same names every hour until somebody
        checked the visitor out, and an alert that arrives every hour is one
        nobody reads.
        """
        from visitormanagement.visitor_management.tasks import flag_overstaying_visitors

        name = self._checked_in_pass(hours_ago=48)
        flag_overstaying_visitors()
        first = frappe.db.count("Visitor Event Log", {"visitor_pass": name, "event_type": "Overstay"})
        flag_overstaying_visitors()
        second = frappe.db.count("Visitor Event Log", {"visitor_pass": name, "event_type": "Overstay"})
        self.assertEqual(first, second, "the overstay job logged the same visitor twice")


class TestDuplicateMatchByMobile(IntegrationTestCase):
    """`get_existing_visitor_matches` — the returning-visitor check at the gate.

    The old query had no phone predicate at all: it selected any pass with a
    non-empty mobile_number, ordered by `modified desc`, capped at 100, and
    compared digits in Python. Past roughly 100 recently-touched passes it stopped
    finding anyone who last visited more than a few hours ago and returned "no
    match" — a silent wrong answer at the gate rather than a visible failure.
    """

    def setUp(self):
        self.host = _host_employee()
        if not self.host:
            self.skipTest("no Active Employee on this site")

    def test_same_number_different_formatting_still_matches(self):
        from visitormanagement.visitor_management.doctype.visitor_pass.visitor_pass import (
            get_existing_visitor_matches,
        )

        vp = _make_pass(
            "Contractor",
            "Mobile Match Tester",
            _fresh_pan(),
            self.host,
            extra={"mobile_number": "+91 91234 56780"},
        )
        # Same digits, different punctuation — which is how the same number arrives
        # from the portal, a paste, or a different operator.
        #
        # Deliberately NOT testing "9123456780" (the local form, country code
        # dropped). `mobile_digits` stores the full normalised digit string
        # including the country code and the query matches it with `=`, so the
        # local form does not match. That is a real gap and it is inconsistent
        # with visitor_blacklist._mobile_match_tail, which matches on the trailing
        # national number precisely so a stored local number still matches the
        # same person arriving with a country code. Encoding the gap as a passing
        # test here would freeze it in; it is reported instead.
        result = get_existing_visitor_matches(mobile_number="+919123456780")
        names = [row["name"] for row in result["matches"]]
        self.assertIn(vp.name, names, "a returning visitor was not matched on their mobile number")

    def test_mobile_digits_is_derived_on_save(self):
        """The indexed column the match above relies on."""
        vp = _make_pass(
            "Contractor",
            "Digits Tester",
            _fresh_pan(),
            self.host,
            extra={"mobile_number": "+91 91234 56781"},
        )
        self.assertEqual(
            frappe.db.get_value("Visitor Pass", vp.name, "mobile_digits"),
            "919123456781",
            "mobile_digits was not derived from the stored mobile number",
        )

    def test_unrelated_number_does_not_match(self):
        from visitormanagement.visitor_management.doctype.visitor_pass.visitor_pass import (
            get_existing_visitor_matches,
        )

        vp = _make_pass(
            "Contractor",
            "No Match Tester",
            _fresh_pan(),
            self.host,
            extra={"mobile_number": "+91 91234 56782"},
        )
        result = get_existing_visitor_matches(mobile_number="90000000009")
        self.assertNotIn(
            vp.name,
            [row["name"] for row in result["matches"]],
            "an unrelated mobile number produced a false duplicate match",
        )


class TestConfigurableThresholds(IntegrationTestCase):
    """Two values that used to be literals buried in code.

    Both accessors treat 0 as "unset" and fall back, the same convention every
    other numeric setting in `settings.py` uses — 0 must not silently mean
    "classify nothing as fever" or "block every portal submission".
    """

    def test_fever_threshold_reads_the_setting(self):
        from visitormanagement.visitor_management import settings as vms_settings

        frappe.db.set_single_value("VMS Settings", "fever_threshold_c", 38.5)
        frappe.clear_document_cache("VMS Settings")
        self.assertEqual(vms_settings.fever_threshold_c(), 38.5)

    def test_fever_threshold_falls_back_when_zero(self):
        from visitormanagement.visitor_management import settings as vms_settings

        frappe.db.set_single_value("VMS Settings", "fever_threshold_c", 0)
        frappe.clear_document_cache("VMS Settings")
        self.assertEqual(
            vms_settings.fever_threshold_c(),
            vms_settings.DEFAULT_FEVER_THRESHOLD_C,
            "0 was read as a real threshold instead of 'unset'",
        )

    def test_portal_rate_limit_reads_the_setting(self):
        from visitormanagement.visitor_management import settings as vms_settings

        frappe.db.set_single_value("VMS Settings", "max_portal_submissions_per_hour", 5)
        frappe.clear_document_cache("VMS Settings")
        self.assertEqual(vms_settings.max_portal_submissions_per_hour(), 5)

    def test_portal_module_reads_the_same_setting(self):
        """portal.py used to carry its own independent literal 20.

        Raising or lowering one had no effect on the other; both now resolve
        through the single accessor above.
        """
        from visitormanagement.visitor_management import portal

        frappe.db.set_single_value("VMS Settings", "max_portal_submissions_per_hour", 7)
        frappe.clear_document_cache("VMS Settings")
        self.assertEqual(portal._max_submissions_per_hour(), 7)


class TestVisitorPassCancel(IntegrationTestCase):
    """Approved --Cancel--> Cancelled.

    A visitor who cancels, or somebody barred after approval, must not keep a
    gate-valid pass forever. The transition is generated for every approver role,
    and `setup._grant_visitor_pass_cancel` grants those roles the `cancel` DocPerm
    without which the button would render and then be refused on click.
    """

    def setUp(self):
        self.host = _host_employee()
        if not self.host:
            self.skipTest("no Active Employee on this site")

    def test_approver_role_holds_the_cancel_permission(self):
        """The permission half. Regenerated after the workflow, not before it —
        on a fresh install the Visitor Type masters do not exist yet when
        `_ensure_permissions` runs, so asserting this catches that ordering
        regressing again."""
        from visitormanagement.visitor_management.workflow_builder import approver_roles

        roles = set(approver_roles())
        if not roles:
            self.skipTest("no Visitor Type names an approver role on this site")
        granted = set(
            frappe.get_all("Custom DocPerm", filters={"parent": "Visitor Pass", "cancel": 1}, pluck="role")
        )
        missing = sorted(roles - granted)
        self.assertFalse(missing, f"approver role(s) offered Cancel without the permission: {missing}")

    def test_approved_pass_can_be_cancelled(self):
        vp = _make_pass("Contractor", "Cancel Tester", _fresh_pan(), self.host)
        try:
            _approve_pass(vp.name, "Contractor")
        except _SkipApproval as exc:
            self.skipTest(f"no user holding {exc} on this site")

        approver = _user_with_role("System Manager")
        frappe.set_user(approver)
        try:
            apply_workflow(frappe.get_doc("Visitor Pass", vp.name), "Cancel")
        finally:
            frappe.set_user("Administrator")

        self.assertEqual(
            frappe.db.get_value("Visitor Pass", vp.name, "workflow_state"),
            "Cancelled",
            "Cancel did not move an Approved pass to Cancelled",
        )

    def test_security_is_not_offered_cancel(self):
        """Security is read-only on Visitor Pass by design — the gate acts through
        Security Log. Granting it `cancel` would undo that boundary."""
        granted = set(
            frappe.get_all("Custom DocPerm", filters={"parent": "Visitor Pass", "cancel": 1}, pluck="role")
        )
        self.assertNotIn("Security", granted, "Security was granted cancel on Visitor Pass")


class TestItemsVerification(IntegrationTestCase):
    """`generate_badge_number(update_status=True)` — the gate's items-verified step.

    This writes `status` with `db_set`, which goes straight past validation, so the
    "is this pass actually approved" check has to live in the method itself. It was
    added as defence in depth alongside the same check in `sync_badge_number`:
    without it, badge issuance could move a pass that no approver had ever seen to
    "Items Verified", and `_repair_pending_status_drift` in setup.py exists to clean
    up rows left behind by exactly that.
    """

    def setUp(self):
        self.host = _host_employee()
        if not self.host:
            self.skipTest("no Active Employee on this site")

    def test_approved_pass_reaches_items_verified(self):
        vp = _make_pass("Contractor", "Items Verified Tester", _fresh_pan(), self.host)
        try:
            _approve_pass(vp.name, "Contractor")
        except _SkipApproval as exc:
            self.skipTest(f"no user holding {exc} on this site")

        # Clear the badge first. `on_submit` (visitor_pass.py:853) already minted
        # one with update_status=False, and `generate_badge_number` returns early
        # when `badge_number` is set -- so this method is only reachable at all on
        # a pass that has no badge yet. See test_items_verified_is_unreachable_in_
        # the_normal_flow below, which pins that consequence down.
        frappe.db.set_value("Visitor Pass", vp.name, "badge_number", "", update_modified=False)

        doc = frappe.get_doc("Visitor Pass", vp.name)
        doc.generate_badge_number(update_status=True)

        fresh = frappe.get_doc("Visitor Pass", vp.name)
        if not fresh.badge_number:
            self.skipTest("badges are disabled for this visitor type / in VMS Settings")
        self.assertEqual(
            fresh.status,
            "Items Verified",
            "verifying items on an approved pass did not move it to Items Verified",
        )

    def test_unapproved_pass_is_refused(self):
        """The hole this guard closes: a Draft pass must not be badge-able."""
        vp = _make_pass("Contractor", "Unapproved Badge Tester", _fresh_pan(), self.host)
        doc = frappe.get_doc("Visitor Pass", vp.name)
        with self.assertRaises(frappe.PermissionError):
            doc.generate_badge_number(update_status=True)
        self.assertNotEqual(
            frappe.db.get_value("Visitor Pass", vp.name, "status"),
            "Items Verified",
            "an unapproved pass was moved to Items Verified",
        )

    def test_on_submit_path_does_not_set_items_verified(self):
        """`update_status=False` is the on_submit path — it mints a badge but must
        leave the pass at Approved, because nobody has verified anything yet."""
        vp = _make_pass("Contractor", "Submit Path Tester", _fresh_pan(), self.host)
        try:
            _approve_pass(vp.name, "Contractor")
        except _SkipApproval as exc:
            self.skipTest(f"no user holding {exc} on this site")
        frappe.db.set_value("Visitor Pass", vp.name, "badge_number", "", update_modified=False)

        doc = frappe.get_doc("Visitor Pass", vp.name)
        doc.generate_badge_number(update_status=False)

        self.assertNotEqual(
            frappe.db.get_value("Visitor Pass", vp.name, "status"),
            "Items Verified",
            "the on_submit badge path wrongly claimed items were verified",
        )

    def test_items_verified_is_unreachable_in_the_normal_flow(self):
        """Pins down a real gap rather than asserting the ideal.

        `on_submit` mints the badge with update_status=False, and both
        `generate_badge_number` and `sync_badge_number` return early once
        `badge_number` is set. So on a site where badges are enabled, the gate's
        items-verification call is a no-op for `status` and a pass never reaches
        "Items Verified" -- confirmed empirically: 0 of the ~600 passes on
        site.local are in that state.

        The gate still admits the visitor, because ENTRY_STATUSES accepts
        "Approved" too, so nobody is blocked. What is lost is the signal that
        somebody actually checked the declared items.

        This test asserts CURRENT behaviour deliberately. If the badge/verify
        split is ever changed, this test failing is the intended signal to revisit
        it -- not a regression.
        """
        vp = _make_pass("Contractor", "Unreachable State Tester", _fresh_pan(), self.host)
        try:
            _approve_pass(vp.name, "Contractor")
        except _SkipApproval as exc:
            self.skipTest(f"no user holding {exc} on this site")

        if not frappe.db.get_value("Visitor Pass", vp.name, "badge_number"):
            self.skipTest("badges are disabled, so the early-return path does not apply")

        doc = frappe.get_doc("Visitor Pass", vp.name)
        doc.generate_badge_number(update_status=True)
        self.assertEqual(
            frappe.db.get_value("Visitor Pass", vp.name, "status"),
            "Approved",
            "the badge/items-verified split changed -- revisit whether Items Verified "
            "is now reachable, and update this test",
        )
