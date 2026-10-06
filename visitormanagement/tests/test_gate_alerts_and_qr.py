# See license.txt
"""Gate alerts and QR resolution, end to end through the real gate records.

X1  The visitor's thank-you mail. `SecurityLog._advance_pass` moves the pass with
    `frappe.db.set_value` (status is not allow_on_submit), which never fires
    `on_change`, so it replays the Value Change notifications by hand. These tests
    drive a real Check-In -> Check-Out through Security Log inserts and prove the
    "VMS Visitor Thank You" notification is evaluated and handed to
    `frappe.sendmail` for the visitor's address — and that a pass with no email
    sends nothing.

X2  `visitor_gate.scan_qr_checkin`: the QR payload resolves to the Security Log
    the officer must complete (Check-In for an approved pass valid today,
    Check-Out for a checked-in one) and is refused for a pass dated another day,
    a blacklisted visitor, an unknown pass and garbage input. A route reached
    through the scan carries `qr_code_scanned=1` (the officer already scanned);
    the Visitor Pass "Check In" / "Check Out" buttons' route does not.

site.local has no Active Employee, so the host Employee (and the gate officer
user) are created inside the test transaction. FrappeTestCase rolls the class
back when it finishes, and the gate settings these tests touch are restored and
their document cache cleared, so nothing persists.
"""

from __future__ import annotations

from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, get_datetime, nowdate

from visitormanagement.tests.test_regression import (
	_approve_pass,
	_fresh_pan,
	_make_pass,
	_SkipApproval,
)
from visitormanagement.visitor_management.api.visitor_gate import (
	scan_qr_checkin,
	visitor_checkin,
	visitor_checkout,
)

THANK_YOU = "VMS Visitor Thank You"
THANK_YOU_SUBJECT = "Thank you for visiting"
GATE_FLAGS = (
	"qr_scan_required_at_gate",
	"require_visitor_photo",
	"block_check_in_without_verification",
	"allow_gate_without_item_verification",
)
GATE_OFFICER = "x2.gate.officer@example.com"
NO_ROLE_USER = "x2.no.gate.role@example.com"
GATE_NAME = "X2 Gate Alerts Test Gate"

# The runner walks Link dependencies before setUpClass; through Employee that
# reaches ERPNext's Company -> Fiscal Year test records, which collide with a
# real Fiscal Year on a working site. Every fixture here is built by hand.
test_ignore = ["Employee", "ID Proof Type", "Visitor Gate", "Visitor Pass", "Visitor Blacklist"]


def _host_employee():
	host = frappe.db.get_value("Employee", {"status": "Active"}, "name")
	if host:
		return host
	company = frappe.db.get_value("Company", {}, "name")
	if not company:
		return None
	emp = frappe.get_doc(
		{
			"doctype": "Employee",
			"first_name": "X2 Gate Test Host",
			"gender": "Male"
			if frappe.db.exists("Gender", "Male")
			else frappe.db.get_value("Gender", {}, "name"),
			"date_of_birth": "1990-01-01",
			"date_of_joining": "2020-01-01",
			"company": company,
			"status": "Active",
		}
	)
	emp.insert(ignore_permissions=True)
	return emp.name


def _user(email, roles):
	if not frappe.db.exists("User", email):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": email.split("@")[0],
				"send_welcome_email": 0,
				"user_type": "System User",
			}
		)
		user.insert(ignore_permissions=True)
		if roles:
			user.add_roles(*roles)
	return email


class _GateTestBase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.host = _host_employee()
		if not frappe.db.exists("Visitor Gate", GATE_NAME):
			frappe.get_doc({"doctype": "Visitor Gate", "gate_name": GATE_NAME, "is_active": 1}).insert(
				ignore_permissions=True
			)

	@classmethod
	def tearDownClass(cls):
		# Roll back now (the class cleanup does it again, harmlessly) so the cache
		# is cleared AFTER the rollback: a VMS Settings doc cached mid-test must not
		# outlive the transaction it was read in.
		frappe.db.rollback()
		frappe.clear_document_cache("VMS Settings")
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		if not self.host:
			self.skipTest("no Company on this site to create a host Employee in")
		self._saved_flags = {f: frappe.db.get_single_value("VMS Settings", f) for f in GATE_FLAGS}
		# Pin the configurable checklist to its defaults: items enforced, the rest off.
		self._set_flags(dict.fromkeys(GATE_FLAGS, 0))

	def tearDown(self):
		frappe.set_user("Administrator")
		self._set_flags(self._saved_flags)

	def _set_flags(self, values):
		frappe.db.set_single_value("VMS Settings", values)
		frappe.clear_document_cache("VMS Settings")

	# ---------------- fixtures ----------------

	def _approved_pass(self, visit_date=None, email=True, items=True):
		"""Contractor: approver role is System Manager, which this site has."""
		extra = {}
		if items:
			extra["visitor_items"] = [
				{"item_name": "Gate Test Laptop", "item_category": "Electronics", "quantity": 1}
			]
		vp = _make_pass(
			"Contractor",
			"Gate Alert Tester",
			_fresh_pan(),
			self.host,
			visit_date=visit_date or nowdate(),
			extra=extra,
		)
		try:
			_approve_pass(vp.name, "Contractor")
		except _SkipApproval as exc:
			self.skipTest(f"no user holding {exc} on this site")
		if not email:
			# email_id is reqd on Visitor Pass, so a pass without one cannot be
			# saved through the form; it exists only as legacy / imported data.
			# Clear it underneath the approved pass to model that.
			frappe.db.set_value("Visitor Pass", vp.name, "email_id", None, update_modified=False)
		vp.reload()
		self.assertEqual(vp.status, "Approved", "test setup: pass did not reach Approved")
		return vp

	def _log(self, vp, event_type, **values):
		"""A Security Log as the officer completes it: QR scanned, identity
		confirmed, every declared item verified."""
		doc = frappe.get_doc(
			{
				"doctype": "Security Log",
				"visitor_pass": vp.name,
				"event_type": event_type,
				"gate_name": GATE_NAME,
				"qr_code_scanned": 1,
				"id_proof_match": 1,
				"pass_photo_match": 1,
			}
		)
		if event_type == "Check-In":
			doc.check_in_date_time = get_datetime(f"{vp.visit_date} 09:00:00")
			for item in vp.get("visitor_items") or []:
				doc.append(
					"items_verification",
					{
						"visitor_item_row_name": item.name,
						"item_name": item.item_name,
						"item_category": item.item_category,
						"item_type": item.item_category,
						"quantity_declared": item.quantity,
						"quantity_found": item.quantity,
						"item_verified": 1,
					},
				)
		elif event_type == "Check-Out":
			doc.check_out_date_time = get_datetime(f"{vp.visit_date} 17:00:00")
		doc.update(values)
		doc.insert()
		return doc


# ─── X1: thank-you mail on check-out ─────────────────────────────────────
class TestThankYouOnCheckOut(_GateTestBase):
	def setUp(self):
		super().setUp()
		enabled = frappe.db.get_value("Notification", THANK_YOU, "enabled")
		if enabled is None:
			self.skipTest(f"Notification {THANK_YOU!r} is not on this site")
		self.assertEqual(enabled, 1, f"{THANK_YOU} must ship enabled")
		self.notification_cls = type(frappe.get_doc("Notification", THANK_YOU))

	def _check_out_capturing(self, vp):
		"""Check out with Notification.send spied on and frappe.sendmail stubbed.
		Returns (thank-you send calls, thank-you sendmail calls, error logs added)."""
		original_send = self.notification_cls.send
		errors_before = frappe.db.count("Error Log")
		with (
			patch.object(self.notification_cls, "send", autospec=True, side_effect=original_send) as send,
			patch("frappe.sendmail", return_value=None) as sendmail,
		):
			try:
				self._log(vp, "Check-Out")
			except Exception as exc:  # the caller (gate officer) must never see an alert failure
				self.fail(f"check-out raised {type(exc).__name__}: {exc}")
		alert_calls = [c for c in send.call_args_list if c.args[0].name == THANK_YOU]
		mail_calls = [c for c in sendmail.call_args_list if c.kwargs.get("subject") == THANK_YOU_SUBJECT]
		added = frappe.db.count("Error Log") - errors_before
		# limit=0 means "no limit" to get_all, so only query when rows were added.
		new_errors = (
			frappe.get_all("Error Log", fields=["method"], order_by="creation desc", limit=added)
			if added > 0
			else []
		)
		return alert_calls, mail_calls, new_errors

	def test_check_out_sends_thank_you_to_visitor(self):
		vp = self._approved_pass()
		self._log(vp, "Check-In")
		vp.reload()
		self.assertEqual(vp.status, "Checked-In")
		self.assertEqual(vp.items_verified, 1, "declared item was verified at check-in")

		alert_calls, mail_calls, new_errors = self._check_out_capturing(vp)

		vp.reload()
		self.assertEqual(vp.status, "Checked-Out")
		self.assertEqual(len(alert_calls), 1, f"{THANK_YOU} was not evaluated/sent exactly once on check-out")
		self.assertEqual(len(mail_calls), 1, "frappe.sendmail was not called for the thank-you mail")
		kwargs = mail_calls[0].kwargs
		self.assertIn(vp.email_id, kwargs.get("recipients") or [])
		self.assertEqual(kwargs.get("reference_doctype"), "Visitor Pass")
		self.assertEqual(kwargs.get("reference_name"), vp.name)
		self.assertIn(vp.visitor_full_name, kwargs.get("message") or "")
		self.assertEqual(new_errors, [], f"check-out logged errors: {new_errors}")
		# Core records the automated mail as a Communication on the pass.
		self.assertTrue(
			frappe.db.exists(
				"Communication",
				{
					"reference_doctype": "Visitor Pass",
					"reference_name": vp.name,
					"subject": THANK_YOU_SUBJECT,
				},
			)
		)

	def test_check_in_does_not_send_thank_you(self):
		vp = self._approved_pass()
		with patch("frappe.sendmail", return_value=None) as sendmail:
			self._log(vp, "Check-In")
		self.assertFalse(
			[c for c in sendmail.call_args_list if c.kwargs.get("subject") == THANK_YOU_SUBJECT],
			"thank-you mail went out on check-in",
		)

	def test_pass_without_email_sends_nothing(self):
		vp = self._approved_pass(email=False)
		self.assertFalse(vp.email_id, "test setup: pass must have no email")
		self._log(vp, "Check-In")

		_alert_calls, mail_calls, new_errors = self._check_out_capturing(vp)

		vp.reload()
		self.assertEqual(vp.status, "Checked-Out")
		self.assertEqual(mail_calls, [], "thank-you mail sent for a pass with no email address")
		self.assertEqual(new_errors, [], f"check-out logged errors: {new_errors}")


# ─── X2: QR scan resolves the gate movement ──────────────────────────────
class TestScanQrCheckin(_GateTestBase):
	def setUp(self):
		super().setUp()
		self.officer = _user(GATE_OFFICER, ["Security"])
		if not frappe.has_permission("Security Log", "create", user=self.officer):
			# Fall back to the superuser rather than skip: the endpoint's own
			# permission gate is exercised separately below.
			self.officer = "Administrator"

	@staticmethod
	def _qr(vp):
		return f"PASS:{vp.name}|VISITOR:{vp.visitor_full_name}|VISIT_DATE:{vp.visit_date}"

	def _scan(self, qr_data, user=None):
		frappe.set_user(user or self.officer)
		try:
			return scan_qr_checkin(qr_data=qr_data)
		finally:
			frappe.set_user("Administrator")

	def _assert_routes_to(self, result, vp, event_type):
		self.assertIsInstance(result, dict)
		self.assertEqual(result.get("event_type"), event_type)
		self.assertEqual(result.get("visitor_pass"), vp.name)
		route = result.get("route") or ""
		self.assertTrue(route.startswith("/app/security-log/new?"), route)
		params = parse_qs(urlsplit(route).query)
		self.assertEqual(params.get("visitor_pass"), [vp.name])
		self.assertEqual(params.get("event_type"), [event_type])
		return params

	def _scan_without_qr_error(self, qr_data):
		try:
			return self._scan(qr_data)
		except frappe.ValidationError as exc:
			if "Scan the visitor's QR code" in str(exc):
				self.fail(f"scan_qr_checkin raised the QR-scan error: {exc}")
			raise

	def test_officer_has_gate_permission(self):
		self.assertTrue(frappe.has_permission("Security Log", "create", user=self.officer))

	def test_approved_pass_valid_today_routes_to_check_in(self):
		vp = self._approved_pass(items=False)
		self._assert_routes_to(self._scan_without_qr_error(self._qr(vp)), vp, "Check-In")

	def test_qr_scan_required_setting_still_routes_to_check_in(self):
		vp = self._approved_pass(items=False)
		self._set_flags({"qr_scan_required_at_gate": 1})
		self.assertEqual(frappe.get_cached_doc("VMS Settings").qr_scan_required_at_gate, 1)
		params = self._assert_routes_to(self._scan_without_qr_error(self._qr(vp)), vp, "Check-In")

		# Follow the route the way the desk form does (route params prefilled) and
		# complete it as the officer does. qr_code_scanned comes from the route
		# alone: start it at 0 so the save only passes if the scan carried it over.
		values = {k: v[0] for k, v in params.items()}
		self.assertEqual(values.pop("visitor_pass"), vp.name)
		self.assertEqual(values.pop("event_type"), "Check-In")
		self.assertEqual(values.get("qr_code_scanned"), "1")
		self._log(vp, "Check-In", **{"qr_code_scanned": 0, **values})
		vp.reload()
		self.assertEqual(vp.status, "Checked-In")

	def test_checked_in_pass_routes_to_check_out(self):
		vp = self._approved_pass(items=False)
		self._log(vp, "Check-In")
		self._assert_routes_to(self._scan(self._qr(vp)), vp, "Check-Out")

	def test_scan_route_carries_qr_code_scanned(self):
		vp = self._approved_pass(items=False)
		params = self._assert_routes_to(self._scan(self._qr(vp)), vp, "Check-In")
		self.assertEqual(params.get("qr_code_scanned"), ["1"])

		self._log(vp, "Check-In")
		params = self._assert_routes_to(self._scan(self._qr(vp)), vp, "Check-Out")
		self.assertEqual(params.get("qr_code_scanned"), ["1"])

	def _call_as_officer(self, fn, docname):
		frappe.set_user(self.officer)
		try:
			return fn(docname)
		finally:
			frappe.set_user("Administrator")

	def test_gate_button_route_does_not_carry_qr_code_scanned(self):
		# The Visitor Pass "Check In" / "Check Out" buttons call these directly;
		# no QR was read there, so the officer must still scan on the log.
		vp = self._approved_pass(items=False)
		params = self._assert_routes_to(self._call_as_officer(visitor_checkin, vp.name), vp, "Check-In")
		self.assertNotIn("qr_code_scanned", params)

		self._log(vp, "Check-In")
		params = self._assert_routes_to(self._call_as_officer(visitor_checkout, vp.name), vp, "Check-Out")
		self.assertNotIn("qr_code_scanned", params)

	def test_gate_button_route_still_requires_scan_when_configured(self):
		vp = self._approved_pass(items=False)
		self._set_flags({"qr_scan_required_at_gate": 1})
		params = self._assert_routes_to(self._call_as_officer(visitor_checkin, vp.name), vp, "Check-In")
		values = {k: v[0] for k, v in params.items() if k not in ("visitor_pass", "event_type")}
		with self.assertRaises(frappe.ValidationError) as ctx:
			self._log(vp, "Check-In", **{"qr_code_scanned": 0, **values})
		self.assertIn("Scan the visitor's QR code", str(ctx.exception))

	def test_pass_dated_tomorrow_refused(self):
		vp = self._approved_pass(visit_date=add_days(nowdate(), 1), items=False)
		with self.assertRaises(frappe.ValidationError) as ctx:
			self._scan(self._qr(vp))
		self.assertIn("not today", str(ctx.exception))

	def test_blacklisted_visitor_refused(self):
		vp = self._approved_pass(items=False)
		frappe.get_doc(
			{
				"doctype": "Visitor Blacklist",
				"reason": "X2 QR blacklist test",
				"id_proof_number": vp.id_proof_number,
				"is_active": 1,
			}
		).insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError) as ctx:
			self._scan(self._qr(vp))
		self.assertIn("blacklist", str(ctx.exception).lower())

	def test_garbage_qr_refused(self):
		for qr_data in ("garbage-without-any-structure", "PASS:VP-DOES-NOT-EXIST-X2", "|||:::"):
			with self.subTest(qr_data=qr_data), self.assertRaises(frappe.ValidationError):
				self._scan(qr_data)

	def test_user_without_gate_role_refused(self):
		vp = self._approved_pass(items=False)
		outsider = _user(NO_ROLE_USER, [])
		self.assertFalse(frappe.has_permission("Security Log", "create", user=outsider))
		with self.assertRaises(frappe.PermissionError):
			self._scan(self._qr(vp), user=outsider)
