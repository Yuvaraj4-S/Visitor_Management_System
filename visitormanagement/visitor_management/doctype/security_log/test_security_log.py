# See license.txt

"""Security Log — the gate's check-in/check-out record.

This DocType shipped with zero test coverage. One of the two Blocker bugs
found and fixed this session lived here (the multi-day re-entry window in
`before_save` — see visitor_management/doctype/security_log/security_log.py),
which is exactly the failure mode empty coverage produces: nobody could see
the regression until a real contractor was turned away at the gate on day two
of a pass that was still valid.

Helpers are imported from test_regression rather than re-written, so a change
to how a Visitor Pass is built or approved only has to be made in one place.
"""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, get_datetime, getdate, nowdate

from visitormanagement.tests.test_regression import (
	_SkipApproval,
	_approve_pass,
	_fresh_pan,
	_make_pass,
)


def _host_employee():
	"""Any Active Employee to hang a test pass off, or None to self-skip."""
	return frappe.db.get_value("Employee", {"status": "Active"}, "name")


def _active_gate(label):
	"""A fresh, active Visitor Gate scoped to this test.

	Deliberately not reusing whatever gates already exist on site.local — this
	is a shared dev site other agents/tests mutate concurrently, and a gate's
	`is_active` flag flipping under us would make an unrelated assertion here
	fail. `Visitor Gate` autonames on `gate_name`, so `name == gate_name`.
	"""
	name = f"TSL Active Gate {label}"
	if not frappe.db.exists("Visitor Gate", name):
		frappe.get_doc({"doctype": "Visitor Gate", "gate_name": name, "is_active": 1}).insert(
			ignore_permissions=True
		)
	return name


def _inactive_gate(label):
	name = f"TSL Inactive Gate {label}"
	if not frappe.db.exists("Visitor Gate", name):
		frappe.get_doc({"doctype": "Visitor Gate", "gate_name": name, "is_active": 0}).insert(
			ignore_permissions=True
		)
	return name


class TestSecurityLog(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		# `IntegrationTestCase.setUpClass` auto-creates test records for this
		# doctype's Link-field dependency chain — Security Log links Employee
		# (security_officer, person_to_visit via Visitor Pass) which pulls in
		# Company -> Fiscal Year. ERPNext's Fiscal Year test bootstrap collides
		# with the real 2026-2027 Fiscal Year already on this shared dev site
		# (the same pre-existing, non-app issue CLAUDE.md documents for
		# test_visitor_pass.py) and would fail setUpClass before a single test
		# in this file ran. Every fixture here is built by hand against real
		# site data (test_regression's _make_pass / _approve_pass), so none of
		# Frappe's auto-generated records are needed — pre-marking this
		# doctype as already resolved skips that walk without touching the
		# shared dependency chain other doctypes' test classes still go
		# through on their own (see test_visitor_pass.py, which still hits it).
		frappe.local.test_objects.setdefault("Security Log", [])
		super().setUpClass()

	def setUp(self):
		self.host = _host_employee()
		if not self.host:
			self.skipTest("no Active Employee on this site")

		# Pin the gate's configurable checklist off. These tests are about
		# status-sequencing (check-in/out ordering, the multi-day window), not
		# the QR/photo/identity checklist, and all three default to 0 — but this
		# is a shared dev site another agent's test can flip them on
		# concurrently, which would fail an unrelated assertion here for a
		# reason that has nothing to do with what the test is checking.
		frappe.db.set_single_value(
			"VMS Settings",
			{
				"qr_scan_required_at_gate": 0,
				"require_visitor_photo": 0,
				"block_check_in_without_verification": 0,
			},
		)
		frappe.clear_document_cache("VMS Settings")
		self.gate = _active_gate("main")

	def _approved_contractor_pass(self, visit_date=None, extra=None):
		"""Contractor was picked deliberately: its approver role is System
		Manager (see test_regression._approve_pass), which Administrator
		already holds on every site, so these tests never self-skip for lack
		of a seeded approver."""
		vp = _make_pass("Contractor", "Gate Tester", _fresh_pan(), self.host, visit_date=visit_date, extra=extra)
		try:
			_approve_pass(vp.name, "Contractor")
		except _SkipApproval as exc:
			self.skipTest(f"no user holding {exc} on this site")
		vp.reload()
		return vp

	def _multi_day_pass(self):
		visit_date = add_days(nowdate(), 2)
		valid_until = add_days(nowdate(), 6)
		vp = self._approved_contractor_pass(
			visit_date=visit_date,
			extra={"multi_day_pass": 1, "pass_valid_until": valid_until},
		)
		return vp, visit_date, valid_until

	def _check_in(self, vp, gate=None, check_in_dt=None):
		# `_make_pass` schedules `visit_date` 2 days out by default, so a bare
		# check-in with no explicit time (today) would itself be "before the
		# scheduled visit date" and get refused — a self-inflicted false
		# negative in every test that isn't specifically about that guard.
		# Default to 09:00 on the pass's own visit_date so a plain _check_in()
		# call lands inside the window it should.
		check_in_dt = check_in_dt or get_datetime(f"{vp.visit_date} 09:00:00")
		doc = frappe.get_doc(
			{
				"doctype": "Security Log",
				"visitor_pass": vp.name,
				"event_type": "Check-In",
				"gate_name": gate or self.gate,
				"check_in_date_time": check_in_dt,
			}
		)
		doc.insert()
		return doc

	def _check_out(self, vp, gate=None, check_out_dt=None):
		check_out_dt = check_out_dt or get_datetime(f"{vp.visit_date} 17:00:00")
		doc = frappe.get_doc(
			{
				"doctype": "Security Log",
				"visitor_pass": vp.name,
				"event_type": "Check-Out",
				"gate_name": gate or self.gate,
				"check_out_date_time": check_out_dt,
			}
		)
		if check_out_dt:
			doc.check_out_date_time = check_out_dt
		doc.insert()
		return doc

	# ---------------- happy path ----------------

	def test_check_in_then_check_out_happy_path(self):
		vp = self._approved_contractor_pass()

		self._check_in(vp)
		vp.reload()
		self.assertEqual(vp.status, "Checked-In")
		self.assertTrue(
			frappe.db.exists("Visitor Event Log", {"visitor_pass": vp.name, "event_type": "Check-In"}),
			"check-in did not log a Visitor Event Log row",
		)
		self.assertTrue(
			frappe.db.exists("Contact Trace Record", {"visitor_pass": vp.name}),
			"check-in did not create a Contact Trace Record",
		)

		self._check_out(vp)
		vp.reload()
		self.assertEqual(vp.status, "Checked-Out")
		self.assertTrue(
			frappe.db.exists("Visitor Event Log", {"visitor_pass": vp.name, "event_type": "Check-Out"}),
			"check-out did not log a Visitor Event Log row",
		)

	# ---------------- the multi-day regression ----------------

	def test_multi_day_pass_allows_reentry_on_a_later_date(self):
		"""The Blocker this file exists to pin down: a multi-day Contractor pass
		must be checkable in and out on more than one day inside its
		`visit_date .. pass_valid_until` window."""
		vp, visit_date, valid_until = self._multi_day_pass()

		self._check_in(vp, check_in_dt=get_datetime(f"{visit_date} 09:00:00"))
		vp.reload()
		self.assertEqual(vp.status, "Checked-In")

		self._check_out(vp, check_out_dt=get_datetime(f"{visit_date} 17:00:00"))
		vp.reload()
		self.assertEqual(vp.status, "Checked-Out")

		later_date = add_days(visit_date, 2)
		self.assertLessEqual(
			getdate(later_date), getdate(valid_until), "test setup: later_date must stay inside the window"
		)
		self._check_in(vp, check_in_dt=get_datetime(f"{later_date} 09:00:00"))
		vp.reload()
		self.assertEqual(
			vp.status,
			"Checked-In",
			"a multi-day pass must allow re-entry on a later date inside its validity window",
		)

	def test_single_day_pass_cannot_check_in_after_visit_date(self):
		"""The counter-test: without `multi_day_pass`, "Checked-Out" must stay a
		permanent retirement — the carve-out above must not swallow the
		ordinary single-day case."""
		visit_date = add_days(nowdate(), 2)
		vp = self._approved_contractor_pass(visit_date=visit_date)
		late = get_datetime(f"{add_days(visit_date, 1)} 09:00:00")
		with self.assertRaises(frappe.ValidationError):
			self._check_in(vp, check_in_dt=late)

	# ---------------- early check-in refused for both kinds ----------------

	def test_checkin_before_visit_date_refused_single_day(self):
		visit_date = add_days(nowdate(), 5)
		vp = self._approved_contractor_pass(visit_date=visit_date)
		early = get_datetime(f"{add_days(visit_date, -1)} 09:00:00")
		with self.assertRaises(frappe.ValidationError):
			self._check_in(vp, check_in_dt=early)

	def test_checkin_before_visit_date_refused_multi_day(self):
		vp, visit_date, _valid_until = self._multi_day_pass()
		early = get_datetime(f"{add_days(visit_date, -1)} 09:00:00")
		with self.assertRaises(frappe.ValidationError):
			self._check_in(vp, check_in_dt=early)

	# ---------------- gate + sequencing guards ----------------

	def test_inactive_gate_rejected(self):
		vp = self._approved_contractor_pass()
		gate = _inactive_gate("main")
		with self.assertRaises(frappe.ValidationError):
			self._check_in(vp, gate=gate)

	def test_checkout_without_checkin_refused(self):
		vp = self._approved_contractor_pass()
		with self.assertRaises(frappe.ValidationError):
			self._check_out(vp)

	def test_double_checkin_refused(self):
		vp = self._approved_contractor_pass()
		self._check_in(vp)
		with self.assertRaises(frappe.ValidationError):
			self._check_in(vp)
