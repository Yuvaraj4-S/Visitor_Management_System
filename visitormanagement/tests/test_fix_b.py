# See license.txt
"""Gate and reports — fix round B.

G1  The three VMS Settings gate switches are enforced on the server when a
    Check-In / Check-Out is recorded, each on its own: QR scan, a live gate photo
    (not a typed URL, not the visitor's own pass photo), both identity ticks. With
    all three off (a fresh install) a plain record still saves.
G2  A gate event that is refused leaves nothing behind: no Security Log, and the
    pass does not move. Removing a declared item's row does not get past item
    verification. A recorded log can be corrected by a System Manager, but its
    pass and event type cannot be changed. An officer logs on their own behalf.
G3  The log stores names (officer, host), not just Employee IDs, and only the
    masked ID number.
A1  Gate API: one answer for every pass the caller may not read (unknown, draft,
    pending) — nothing about its state. A pass for another day is refused at the
    button, not on save. The blacklist follows VMS Settings "Blacklist Action" on
    every gate path. No response carries a full ID number.
R1  Reports run for every role listed on them, through Frappe's own report
    runner, with the row scope of the Visitor Pass list: a guard sees gate-stage
    passes only, a host their own, a System Manager everything. The host is shown
    by name in a Data column; no report links to another app's DocType. A visitor
    who went in and out twice is one row. Gate Wise Count lists an unused active
    gate. Visitor Identity Match shows masked ID numbers only.

Every fixture is created inside the test transaction (employees, users, gates,
passes) and rolled back with it; nothing depends on the site's own data. The gate
settings these tests change are restored and the settings cache cleared.
"""

from __future__ import annotations

import json
import os
import re
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, get_datetime, nowdate

from visitormanagement.tests.test_regression import (
	PNG_BYTES,
	_approve_pass,
	_fresh_pan,
	_make_pass,
	_SkipApproval,
)
from visitormanagement.visitor_management.api import visitor_gate
from visitormanagement.visitor_management.doctype.security_log import security_log
from visitormanagement.visitor_management.id_masking import mask_id

GATE_FLAGS = (
	"qr_scan_required_at_gate",
	"require_visitor_photo",
	"block_check_in_without_verification",
	"allow_gate_without_item_verification",
)
GUARD = "fixb.guard@example.com"
HOST_USER = "fixb.host@example.com"
SYSTEM_MANAGER = "fixb.sysmgr@example.com"
NO_ROLE_USER = "fixb.norole@example.com"
GATE = "FixB Main Gate"
UNUSED_GATE = "FixB Unused Gate"
INACTIVE_GATE = "FixB Closed Gate"
APP_MODULES = ("Visitor Management", "Conference Room")

# The runner walks Link dependencies before setUpClass; through Employee that
# reaches ERPNext's Company -> Fiscal Year test records, which collide with a
# real Fiscal Year on a working site. Every fixture here is built by hand.
test_ignore = ["Employee", "ID Proof Type", "Visitor Gate", "Visitor Pass", "Visitor Blacklist"]


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


def _employee(first_name, user=None):
	"""A new Active Employee, optionally the Employee record of `user`."""
	company = frappe.db.get_value("Company", {}, "name")
	if not company:
		return None
	gender = "Male" if frappe.db.exists("Gender", "Male") else frappe.db.get_value("Gender", {}, "name")
	values = {
		"doctype": "Employee",
		"first_name": first_name,
		"gender": gender,
		"date_of_birth": "1990-01-01",
		"date_of_joining": "2020-01-01",
		"company": company,
		"status": "Active",
	}
	if user:
		values["user_id"] = user
	emp = frappe.get_doc(values)
	emp.insert(ignore_permissions=True)
	return emp.name


def _gate(name, is_active=1):
	if not frappe.db.exists("Visitor Gate", name):
		frappe.get_doc({"doctype": "Visitor Gate", "gate_name": name, "is_active": is_active}).insert(
			ignore_permissions=True
		)
	return name


def _blacklist(id_number):
	"""An active blacklist entry for an ID number, however the DocType takes one."""
	values = {
		"doctype": "Visitor Blacklist",
		"reason": "FixB blacklist test",
		"is_active": 1,
		"id_proof_number": id_number,
	}
	# Where a number is typed, when the DocType has a separate entry field.
	entry_field = "id_proof_number_entry"
	if frappe.get_meta("Visitor Blacklist").has_field(entry_field):
		values[entry_field] = id_number
	return frappe.get_doc(values).insert(ignore_permissions=True)


class _GateBase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		frappe.set_user("Administrator")
		cls.roles_present = all(frappe.db.exists("Role", r) for r in ("Security", "Host Employee"))
		cls.guard = _user(GUARD, ["Security"]) if cls.roles_present else None
		cls.host_user = _user(HOST_USER, ["Host Employee"]) if cls.roles_present else None
		cls.system_manager = _user(SYSTEM_MANAGER, ["System Manager"])
		cls.no_role_user = _user(NO_ROLE_USER, [])
		# The host has a login; HRMS then restricts that login to its own Employee
		# record with a User Permission — the situation that emptied the reports.
		cls.host = _employee("FixB Host Person", cls.host_user) if cls.host_user else None
		cls.other_host = _employee("FixB Other Host")
		cls.gate = _gate(GATE)

	@classmethod
	def tearDownClass(cls):
		# Roll back now (the class cleanup does it again, harmlessly) so the cache
		# is cleared AFTER the rollback: a VMS Settings doc cached mid-test must not
		# outlive the transaction it was read in.
		frappe.set_user("Administrator")
		frappe.db.rollback()
		frappe.clear_document_cache("VMS Settings")
		super().tearDownClass()

	def setUp(self):
		frappe.set_user("Administrator")
		if not (self.roles_present and self.host and self.other_host):
			self.skipTest("needs the app's roles and a Company to create Employees in")
		self._saved = {f: frappe.db.get_single_value("VMS Settings", f) for f in GATE_FLAGS}
		self._saved["blacklist_action"] = frappe.db.get_single_value("VMS Settings", "blacklist_action")
		# The defaults of a fresh install: items enforced, the three switches off.
		self._settings({**dict.fromkeys(GATE_FLAGS, 0), "blacklist_action": "Block Entry"})

	def tearDown(self):
		frappe.set_user("Administrator")
		self._settings(self._saved)

	def _settings(self, values):
		frappe.db.set_single_value("VMS Settings", values)
		frappe.clear_document_cache("VMS Settings")

	# ---------------- fixtures ----------------

	def _pass(self, host=None, visit_date=None, items=(), approve=True, name="FixB Visitor"):
		"""A Contractor pass (its approver role is System Manager). Returns (pass, PAN)."""
		pan = _fresh_pan()
		extra = {}
		if items:
			extra["visitor_items"] = [
				{"item_name": item, "item_category": "Electronics", "quantity": 1} for item in items
			]
		vp = _make_pass(
			"Contractor", name, pan, host or self.host, visit_date=visit_date or nowdate(), extra=extra
		)
		if approve:
			try:
				_approve_pass(vp.name, "Contractor")
			except _SkipApproval as exc:
				self.skipTest(f"no user holding {exc} on this site")
			vp.reload()
			self.assertEqual(vp.status, "Approved", "test setup: pass did not reach Approved")
		return vp, pan

	def _new_log(self, vp, event_type="Check-In", skip_items=(), **values):
		doc = frappe.get_doc(
			{
				"doctype": "Security Log",
				"visitor_pass": vp.name,
				"event_type": event_type,
				"gate_name": self.gate,
			}
		)
		if event_type == "Check-In":
			for item in vp.get("visitor_items") or []:
				if item.item_name in skip_items:
					continue
				doc.append(
					"items_verification",
					{
						"visitor_item_row_name": item.name,
						"item_name": item.item_name,
						"quantity_declared": item.quantity,
						"quantity_found": item.quantity,
						"item_verified": 1,
					},
				)
		doc.update(values)
		return doc

	def _record(self, vp, event_type="Check-In", **values):
		doc = self._new_log(vp, event_type, **values)
		doc.insert()
		return doc

	def _status(self, vp):
		return frappe.db.get_value("Visitor Pass", vp.name, "status")

	def _gate_photo(self, attached_to="new-security-log-fixbtest"):
		"""A file uploaded against an unsaved Security Log's Photo at Gate field."""
		file = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"fixb_gate_{frappe.generate_hash(length=8)}.png",
				"content": PNG_BYTES,
				"decode": False,
				"is_private": 1,
				"attached_to_doctype": "Security Log",
				"attached_to_name": attached_to,
				"attached_to_field": "photo_at_gate",
			}
		)
		file.flags.ignore_permissions = True
		file.insert()
		return file


# ─── G1: the three switches, enforced on the server ──────────────────────
class TestGateSwitches(_GateBase):
	def _assert_refused(self, vp, fragment, event_type="Check-In", **values):
		before = self._status(vp)
		with self.assertRaises(frappe.ValidationError) as ctx:
			self._record(vp, event_type, **values)
		self.assertIn(fragment, str(ctx.exception))
		self.assertEqual(self._status(vp), before, "a refused gate event moved the pass")
		self.assertFalse(
			frappe.db.exists("Security Log", {"visitor_pass": vp.name, "event_type": event_type}),
			"a refused gate event left a Security Log behind",
		)

	def test_all_switches_off_records_a_plain_check_in(self):
		vp, _pan = self._pass()
		log = self._record(vp)
		self.assertEqual(self._status(vp), "Checked-In")
		self.assertEqual(log.id_proof_match, 0)

	def test_qr_scan_switch(self):
		self._settings({"qr_scan_required_at_gate": 1})
		vp, _pan = self._pass()
		self._assert_refused(vp, "Scan the visitor's QR code")
		self._record(vp, qr_code_scanned=1)
		self.assertEqual(self._status(vp), "Checked-In")

	def test_photo_switch_needs_a_photo(self):
		self._settings({"require_visitor_photo": 1})
		vp, _pan = self._pass()
		self._assert_refused(vp, "Capture a live gate photo")

	def test_photo_switch_refuses_the_pass_photo_and_a_typed_url(self):
		self._settings({"require_visitor_photo": 1})
		vp, _pan = self._pass()
		for url in (vp.visitor_photo, vp.id_proof_scan, "/private/files/fixb-not-a-real-upload.png"):
			with self.subTest(url=url):
				self._assert_refused(vp, "must be a photo captured or attached", photo_at_gate=url)

	def test_photo_switch_accepts_an_upload_made_on_the_unsaved_log(self):
		self._settings({"require_visitor_photo": 1})
		vp, _pan = self._pass()
		photo = self._gate_photo()
		log = self._record(vp, photo_at_gate=photo.file_url)
		self.assertEqual(self._status(vp), "Checked-In")
		# The upload now belongs to the recorded log, and the pass shows it.
		self.assertEqual(frappe.db.get_value("File", photo.name, "attached_to_name"), log.name)
		self.assertEqual(frappe.db.get_value("Visitor Pass", vp.name, "gate_verified_photo"), photo.file_url)

	def test_identity_switch_needs_both_ticks(self):
		self._settings({"block_check_in_without_verification": 1})
		vp, _pan = self._pass()
		self._assert_refused(vp, "matches the ID proof")
		self._assert_refused(vp, "matches the ID proof", pass_photo_match=1)
		self._assert_refused(vp, "pass creation photo", id_proof_match=1)
		self._record(vp, id_proof_match=1, pass_photo_match=1)
		self.assertEqual(self._status(vp), "Checked-In")

	def test_switches_apply_to_check_out_too(self):
		vp, _pan = self._pass()
		self._record(vp, check_in_date_time=get_datetime(f"{nowdate()} 00:00:01"))
		self._settings(
			{
				"qr_scan_required_at_gate": 1,
				"require_visitor_photo": 1,
				"block_check_in_without_verification": 1,
			}
		)
		self._assert_refused(vp, "Scan the visitor's QR code", event_type="Check-Out")
		self._assert_refused(vp, "Capture a live gate photo", event_type="Check-Out", qr_code_scanned=1)
		photo = self._gate_photo()
		self._assert_refused(
			vp,
			"matches the ID proof",
			event_type="Check-Out",
			qr_code_scanned=1,
			photo_at_gate=photo.file_url,
		)
		self._record(
			vp,
			"Check-Out",
			qr_code_scanned=1,
			photo_at_gate=photo.file_url,
			id_proof_match=1,
			pass_photo_match=1,
		)
		self.assertEqual(self._status(vp), "Checked-Out")

	def test_all_three_on_as_after_an_upgrade(self):
		self._settings(
			{
				"qr_scan_required_at_gate": 1,
				"require_visitor_photo": 1,
				"block_check_in_without_verification": 1,
			}
		)
		vp, _pan = self._pass()
		photo = self._gate_photo()
		# The photo alone — what the Attach button's automatic save used to send.
		self._assert_refused(vp, "Scan the visitor's QR code", photo_at_gate=photo.file_url)
		self._record(
			vp, qr_code_scanned=1, photo_at_gate=photo.file_url, id_proof_match=1, pass_photo_match=1
		)
		self.assertEqual(self._status(vp), "Checked-In")

	def test_other_events_are_not_gated(self):
		self._settings(
			{
				"qr_scan_required_at_gate": 1,
				"require_visitor_photo": 1,
				"block_check_in_without_verification": 1,
			}
		)
		vp, _pan = self._pass()
		self._record(vp, "Alert", remarks="FixB alert")
		self.assertEqual(self._status(vp), "Approved")

	def test_gate_policy_reports_the_switches(self):
		self._settings({"require_visitor_photo": 1})
		frappe.set_user(self.guard)
		policy = security_log.get_gate_policy()
		self.assertEqual(
			(policy["qr_scan_required"], policy["photo_required"], policy["identity_match_required"]),
			(False, True, False),
		)
		self.assertTrue(policy["items_verification_required"])
		frappe.set_user(self.no_role_user)
		with self.assertRaises(frappe.PermissionError):
			security_log.get_gate_policy()
		with self.assertRaises(frappe.PermissionError):
			security_log.get_approved_vip_queue()


# ─── G2 / G3: what a recorded log is, and what it stores ─────────────────
class TestGateRecord(_GateBase):
	def test_removed_item_row_does_not_skip_item_verification(self):
		vp, _pan = self._pass(items=("FixB Laptop", "FixB Drill"))
		with self.assertRaises(frappe.ValidationError) as ctx:
			self._record(vp, skip_items=("FixB Drill",))
		self.assertIn("FixB Drill", str(ctx.exception))
		self.assertNotIn("FixB Laptop", str(ctx.exception))
		self.assertEqual(self._status(vp), "Approved")
		self.assertFalse(frappe.db.exists("Security Log", {"visitor_pass": vp.name}))

	def test_photo_first_with_items_is_refused_cleanly_and_can_be_completed(self):
		"""The old Attach auto-save: a photo and nothing else, on a pass with items."""
		vp, _pan = self._pass(items=("FixB Laptop",))
		photo = self._gate_photo()
		with self.assertRaises(frappe.ValidationError):
			self._record(vp, skip_items=("FixB Laptop",), photo_at_gate=photo.file_url)
		self.assertEqual(self._status(vp), "Approved")
		self.assertFalse(frappe.db.exists("Security Log", {"visitor_pass": vp.name}))
		# The officer then verifies the item and records — same photo.
		self._record(vp, photo_at_gate=photo.file_url)
		self.assertEqual(self._status(vp), "Checked-In")
		self.assertEqual(frappe.db.count("Security Log", {"visitor_pass": vp.name}), 1)

	def test_log_stores_names_and_only_the_masked_id(self):
		vp, pan = self._pass()
		log = self._record(vp, security_officer=self.other_host, id_proof_number=pan)
		log.reload()
		self.assertEqual(log.host_name, frappe.db.get_value("Employee", self.host, "employee_name"))
		self.assertEqual(
			log.security_officer_name, frappe.db.get_value("Employee", self.other_host, "employee_name")
		)
		self.assertEqual(log.id_proof_number, mask_id("PAN Card", pan))
		self.assertNotEqual(log.id_proof_number, pan)
		self.assertNotIn(pan, frappe.as_json(log.as_dict()))

	def test_officer_records_on_their_own_behalf(self):
		vp, _pan = self._pass()
		frappe.set_user(self.guard)
		log = self._record(vp, security_officer=self.other_host)
		frappe.set_user("Administrator")
		self.assertNotEqual(
			frappe.db.get_value("Security Log", log.name, "security_officer"),
			self.other_host,
			"a guard recorded a gate event in another employee's name",
		)
		self.assertEqual(frappe.db.get_value("Security Log", log.name, "owner"), self.guard)

	def test_recorded_log_is_locked_for_the_officer(self):
		vp, _pan = self._pass()
		frappe.set_user(self.guard)
		log = self._record(vp)
		log.reload()
		log.verification_notes = "changed afterwards"
		with self.assertRaises(frappe.ValidationError):
			log.save()

	def test_system_manager_can_correct_but_not_repurpose_a_recorded_log(self):
		vp, _pan = self._pass()
		log = self._record(vp)
		# A requirement switched on later must not make the old record uneditable.
		self._settings({"require_visitor_photo": 1, "block_check_in_without_verification": 1})
		frappe.set_user(self.system_manager)
		log = frappe.get_doc("Security Log", log.name)
		log.verification_notes = "gate was mis-keyed"
		log.save()  # used to fail: "Visitor ... is already Checked-In"
		self.assertEqual(self._status(vp), "Checked-In")

		log.reload()
		log.event_type = "Check-Out"
		with self.assertRaises(frappe.ValidationError):
			log.save()
		self.assertEqual(self._status(vp), "Checked-In")

	def test_upgrade_step_fills_names_on_older_logs(self):
		from visitormanagement.visitor_management.upgrades import gate_steps

		vp, _pan = self._pass()
		log = self._record(vp, security_officer=self.other_host)
		# A log recorded before the name fields existed.
		frappe.db.set_value(
			"Security Log",
			log.name,
			{"host_name": None, "security_officer_name": None},
			update_modified=False,
		)
		modified = frappe.db.get_value("Security Log", log.name, "modified")

		gate_steps.run()
		gate_steps.run()  # idempotent: the second run has nothing to fill

		row = frappe.db.get_value(
			"Security Log", log.name, ["host_name", "security_officer_name", "modified"], as_dict=True
		)
		self.assertEqual(row.host_name, frappe.db.get_value("Employee", self.host, "employee_name"))
		self.assertEqual(
			row.security_officer_name, frappe.db.get_value("Employee", self.other_host, "employee_name")
		)
		self.assertEqual(row.modified, modified, "the backfill must not look like an edit of the gate record")


# ─── A1: gate API ────────────────────────────────────────────────────────
class TestGateApi(_GateBase):
	@staticmethod
	def _qr(vp):
		return f"PASS:{vp.name}|VISITOR:{vp.visitor_full_name}|VISIT_DATE:{vp.visit_date}"

	def _as(self, user, fn, *args, **kwargs):
		frappe.set_user(user)
		try:
			return fn(*args, **kwargs)
		finally:
			frappe.set_user("Administrator")

	def _refusal(self, user, fn, *args):
		with self.assertRaises(frappe.ValidationError) as ctx:
			self._as(user, fn, *args)
		self.assertNotIsInstance(ctx.exception, frappe.PermissionError)
		return str(ctx.exception)

	def test_out_of_scope_passes_all_get_the_same_answer(self):
		draft, _pan = self._pass(approve=False, name="FixB Secret Draft")
		pending, _pan = self._pass(approve=False, name="FixB Secret Pending")
		frappe.set_user(frappe.db.get_value("Visitor Pass", pending.name, "owner"))
		from frappe.model.workflow import apply_workflow

		apply_workflow(frappe.get_doc("Visitor Pass", pending.name), "Submit")
		frappe.set_user("Administrator")
		self.assertNotEqual(self._status(pending), self._status(draft))
		self.assertFalse(frappe.has_permission("Visitor Pass", "read", doc=draft.name, user=self.guard))

		unknown = "VP-FIXB-DOES-NOT-EXIST"
		answers = set()
		for name in (unknown, draft.name, pending.name):
			answers.add(self._refusal(self.guard, visitor_gate.visitor_checkin, name))
			answers.add(self._refusal(self.guard, visitor_gate.visitor_checkout, name))
			answers.add(self._refusal(self.guard, visitor_gate.scan_qr_checkin, f"PASS:{name}"))
			answers.add(self._refusal(self.guard, visitor_gate.get_gate_context, name))
		self.assertEqual(len(answers), 1, f"the gate API told passes apart: {answers}")
		answer = answers.pop()
		for leak in ("Draft", "Pending", "Secret", "does not exist", draft.name, pending.name):
			self.assertNotIn(leak, answer)

	def test_a_log_saved_directly_gets_the_same_answer(self):
		"""The Security Log copies the visitor's details from the pass it names.

		The endpoints above refuse an out-of-scope pass; a log posted straight to
		the DocType (the form, /api/resource) must not be the way around them,
		whatever its event type.
		"""
		draft, _pan = self._pass(approve=False, name="FixB Secret Direct")
		answers = set()
		for event_type in ("Alert", "Check-In", "Badge Collected"):

			def save(event_type=event_type):
				return self._record(draft, event_type)

			answers.add(self._refusal(self.guard, save))
		answers.add(self._refusal(self.guard, visitor_gate.get_gate_context, draft.name))
		self.assertEqual(len(answers), 1, f"the log's own save told more than the gate API: {answers}")
		answer = answers.pop()
		for leak in ("Draft", "Secret", draft.name):
			self.assertNotIn(leak, answer)
		self.assertFalse(frappe.db.exists("Security Log", {"visitor_pass": draft.name}))

		# A pass the officer may read can still have an alert recorded against it.
		approved, _pan = self._pass()
		log = self._as(self.guard, self._record, approved, "Alert")
		self.assertEqual(log.visitor_name, approved.visitor_full_name)

	def test_name_and_date_fallback_does_not_reach_out_of_scope_passes(self):
		draft, _pan = self._pass(approve=False, name="FixB Fallback Draft")
		qr = f"VISITOR:{draft.visitor_full_name}|VISIT_DATE:{draft.visit_date}"
		self.assertEqual(
			self._refusal(self.guard, visitor_gate.scan_qr_checkin, qr),
			self._refusal(self.guard, visitor_gate.scan_qr_checkin, "PASS:VP-FIXB-DOES-NOT-EXIST"),
		)

	def test_name_and_date_fallback_refuses_an_ambiguous_match(self):
		first, _pan = self._pass(name="FixB Same Name")
		self._pass(name="FixB Same Name")
		qr = f"VISITOR:{first.visitor_full_name}|VISIT_DATE:{first.visit_date}"
		self.assertIn("More than one pass", self._refusal(self.guard, visitor_gate.scan_qr_checkin, qr))

	def test_check_in_button_refuses_a_pass_for_another_day(self):
		vp, _pan = self._pass(visit_date=add_days(nowdate(), 1))
		self.assertTrue(frappe.has_permission("Visitor Pass", "read", doc=vp.name, user=self.guard))
		self.assertIn("not today", self._refusal(self.guard, visitor_gate.visitor_checkin, vp.name))
		self.assertIn("not today", self._refusal(self.guard, visitor_gate.scan_qr_checkin, self._qr(vp)))
		# The form is told the same thing, as data, so an Alert can still be logged.
		context = self._as(self.guard, visitor_gate.get_gate_context, vp.name)
		self.assertIsNone(context["event_type"])
		self.assertIn("not today", context["refusal"]["message"])

	def test_valid_pass_routes_and_responses_carry_no_full_id(self):
		vp, pan = self._pass(items=("FixB Laptop",))
		checkin = self._as(self.guard, visitor_gate.visitor_checkin, vp.name)
		scan = self._as(self.guard, visitor_gate.scan_qr_checkin, self._qr(vp))
		context = self._as(self.guard, visitor_gate.get_gate_context, vp.name)

		self.assertEqual(checkin["event_type"], "Check-In")
		self.assertNotIn("qr_code_scanned", checkin["route"])
		self.assertIn("qr_code_scanned=1", scan["route"])
		self.assertEqual(context["event_type"], "Check-In")
		self.assertIsNone(context["refusal"])
		self.assertEqual(context["id_proof_number_masked"], mask_id("PAN Card", pan))
		self.assertEqual(context["host_name"], frappe.db.get_value("Employee", self.host, "employee_name"))
		self.assertEqual([i["item_name"] for i in context["items"]], ["FixB Laptop"])
		self.assertIn("policy", context)

		for payload in (checkin, scan, context):
			text = frappe.as_json(payload)
			self.assertNotIn(pan, text, "a gate API response carried the full ID number")
			self.assertNotIn('id_proof_number"', text.replace("id_proof_number_masked", ""))

	def test_checked_in_pass_routes_to_check_out_whatever_the_blacklist_says(self):
		vp, pan = self._pass()
		self._record(vp)
		_blacklist(pan)
		self.assertEqual(
			self._as(self.guard, visitor_gate.scan_qr_checkin, self._qr(vp))["event_type"], "Check-Out"
		)
		self.assertEqual(
			self._as(self.guard, visitor_gate.visitor_checkout, vp.name)["event_type"], "Check-Out"
		)

	def test_blacklist_action_block_entry(self):
		vp, pan = self._pass()
		_blacklist(pan)
		self._settings({"blacklist_action": "Block Entry"})
		# A refusal is logged outside the transaction (it would be rolled back with
		# the request otherwise). Stubbed here so the test leaves no Error Log
		# behind, and checked for having been asked to do exactly that.
		with (
			patch.object(visitor_gate, "record_gate_blacklist") as api_log,
			patch.object(security_log, "record_gate_blacklist") as form_log,
		):
			for fn, arg in (
				(visitor_gate.scan_qr_checkin, self._qr(vp)),
				(visitor_gate.visitor_checkin, vp.name),
			):
				self.assertIn("blacklist", self._refusal(self.guard, fn, arg).lower())
			context = self._as(self.guard, visitor_gate.get_gate_context, vp.name)
			self.assertIsNone(context["event_type"])
			self.assertIn("blacklist", context["refusal"]["message"].lower())
			with self.assertRaises(frappe.ValidationError):
				self._record(vp)
		self.assertEqual(self._status(vp), "Approved")
		self.assertEqual(api_log.call_count, 2)
		self.assertTrue(all(call.kwargs.get("refused") for call in api_log.call_args_list))
		self.assertEqual(form_log.call_count, 1)
		self.assertTrue(form_log.call_args.kwargs.get("refused"))

	def test_blacklist_action_alert_only(self):
		vp, pan = self._pass()
		_blacklist(pan)
		self._settings({"blacklist_action": "Alert Only"})
		for fn, arg in (
			(visitor_gate.scan_qr_checkin, self._qr(vp)),
			(visitor_gate.visitor_checkin, vp.name),
		):
			result = self._as(self.guard, fn, arg)
			self.assertEqual(result["event_type"], "Check-In")
			self.assertIn("blacklist", result["warning"].lower())
		context = self._as(self.guard, visitor_gate.get_gate_context, vp.name)
		self.assertEqual(context["event_type"], "Check-In")
		self.assertIn("blacklist", context["warning"]["message"].lower())
		self._record(vp)
		self.assertEqual(self._status(vp), "Checked-In")

	def test_blacklist_action_log_only(self):
		vp, pan = self._pass()
		_blacklist(pan)
		self._settings({"blacklist_action": "Log Only"})
		for fn, arg in (
			(visitor_gate.scan_qr_checkin, self._qr(vp)),
			(visitor_gate.visitor_checkin, vp.name),
		):
			result = self._as(self.guard, fn, arg)
			self.assertEqual(result["event_type"], "Check-In")
			self.assertNotIn("warning", result)
		context = self._as(self.guard, visitor_gate.get_gate_context, vp.name)
		self.assertEqual(context["event_type"], "Check-In")
		self.assertIsNone(context["warning"])
		self._record(vp)
		self.assertEqual(self._status(vp), "Checked-In")

	def test_endpoints_need_the_gate_role(self):
		vp, _pan = self._pass()
		for fn, arg in (
			(visitor_gate.visitor_checkin, vp.name),
			(visitor_gate.visitor_checkout, vp.name),
			(visitor_gate.scan_qr_checkin, self._qr(vp)),
			(visitor_gate.get_gate_context, vp.name),
		):
			with self.subTest(fn=fn.__name__), self.assertRaises(frappe.PermissionError):
				self._as(self.host_user, fn, arg)


# ─── R1: reports ─────────────────────────────────────────────────────────
class TestReports(_GateBase):
	def _run(self, report, user, filters=None):
		from frappe.desk.query_report import run

		frappe.set_user(user)
		try:
			return run(report, filters=filters or {})
		finally:
			frappe.set_user("Administrator")

	def _window(self):
		return {"from_date": nowdate(), "to_date": add_days(nowdate(), 3)}

	def _fixtures(self):
		mine_approved, _pan = self._pass(name="FixB Mine Approved")
		mine_draft, _pan = self._pass(
			approve=False, visit_date=add_days(nowdate(), 2), name="FixB Mine Draft"
		)
		other_approved, _pan = self._pass(host=self.other_host, name="FixB Other Approved")
		other_draft, _pan = self._pass(
			host=self.other_host, approve=False, visit_date=add_days(nowdate(), 2), name="FixB Other Draft"
		)
		return mine_approved, mine_draft, other_approved, other_draft

	def test_host_login_is_restricted_to_its_own_employee(self):
		"""The precondition of the original defect, so the tests below mean something."""
		from frappe.core.doctype.user_permission.user_permission import get_user_permissions

		allowed = [p.get("doc") for p in get_user_permissions(self.host_user).get("Employee") or []]
		if allowed:
			self.assertEqual(allowed, [self.host])

	def test_daily_visitor_log_scope_per_role(self):
		mine_approved, mine_draft, other_approved, other_draft = self._fixtures()
		everything = {mine_approved.name, mine_draft.name, other_approved.name, other_draft.name}
		expected = {
			self.guard: {mine_approved.name, other_approved.name},
			self.host_user: {mine_approved.name, mine_draft.name},
			self.system_manager: everything,
		}
		host_names = {
			mine_approved.name: frappe.db.get_value("Employee", self.host, "employee_name"),
			other_approved.name: frappe.db.get_value("Employee", self.other_host, "employee_name"),
		}
		for user, visible in expected.items():
			with self.subTest(user=user):
				out = self._run("Daily Visitor Log", user, self._window())
				rows = {r["visitor_pass"]: r for r in out["result"] if isinstance(r, dict)}
				self.assertEqual(set(rows) & everything, visible)
				for name in visible & set(host_names):
					self.assertEqual(rows[name]["host"], host_names[name])
				for row in rows.values():
					self.assertNotIn("person_to_visit", row)
					self.assertNotIn("id_proof_number", row)

	def test_active_visitors_scope_and_one_row_per_visitor(self):
		mine, _pan = self._pass(name="FixB Inside Mine")
		other, _pan = self._pass(host=self.other_host, name="FixB Inside Other")
		day = nowdate()
		# In, out, and back in: three gate events, still one visitor inside.
		self._record(mine, check_in_date_time=get_datetime(f"{day} 00:00:01"))
		self._record(mine, "Check-Out", check_out_date_time=get_datetime(f"{day} 00:00:02"))
		self._record(mine, check_in_date_time=get_datetime(f"{day} 00:00:03"))
		self._record(other, check_in_date_time=get_datetime(f"{day} 00:00:01"))

		expected = {
			self.guard: {mine.name, other.name},
			self.host_user: {mine.name},
			self.system_manager: {mine.name, other.name},
		}
		for user, visible in expected.items():
			with self.subTest(user=user):
				out = self._run("Active Visitors", user)
				names = [r["visitor_pass"] for r in out["result"] if isinstance(r, dict)]
				self.assertEqual(set(names) & {mine.name, other.name}, visible)
				self.assertEqual(names.count(mine.name), 1, "a re-entry multiplied the visitor's row")
				row = next(r for r in out["result"] if isinstance(r, dict) and r["visitor_pass"] == mine.name)
				self.assertEqual(row["gate_name"], self.gate)
				self.assertEqual(str(row["checkin_time"]), f"{day} 00:00:03")
				self.assertEqual(row["host"], frappe.db.get_value("Employee", self.host, "employee_name"))

		out = self._run("Daily Visitor Log", self.guard, self._window())
		rows = [r for r in out["result"] if isinstance(r, dict) and r["visitor_pass"] == mine.name]
		self.assertEqual(len(rows), 1, "a re-entry multiplied the pass's row")
		self.assertEqual(rows[0]["entries"], 2)
		self.assertEqual(str(rows[0]["checkin"]), f"{day} 00:00:01")
		self.assertFalse(rows[0]["checkout"], "the visitor is inside again; there is no last check-out")

		gate_filtered = self._run("Active Visitors", self.guard, {"gate_name": UNUSED_GATE})
		self.assertFalse(
			[r for r in gate_filtered["result"] if isinstance(r, dict) and r["visitor_pass"] == mine.name]
		)

	def test_no_report_links_to_another_apps_doctype(self):
		self._fixtures()
		reports = {
			"Daily Visitor Log": self._window(),
			"Active Visitors": {},
			"Gate Wise Count": {"date": nowdate()},
			"Visitor Identity Match Report": {},
			"Daily Hospitality Schedule": {"date": nowdate()},
			"Daily Booking Schedule": {"date": nowdate()},
			"Room Utilization": {"from_date": nowdate(), "to_date": nowdate()},
		}
		for report, filters in reports.items():
			if not frappe.db.exists("Report", report):
				continue
			with self.subTest(report=report):
				out = self._run(report, self.system_manager, filters)
				for column in out["columns"]:
					if column.get("fieldtype") != "Link":
						continue
					module = frappe.db.get_value("DocType", column.get("options"), "module")
					self.assertIn(
						module,
						APP_MODULES,
						f"{report}: column {column.get('fieldname')} links to {column.get('options')}",
					)

	def test_gate_reports_run_for_a_security_only_user(self):
		self._fixtures()
		for report, filters in (
			("Daily Visitor Log", self._window()),
			("Active Visitors", {}),
			("Gate Wise Count", {"date": nowdate()}),
		):
			with self.subTest(report=report):
				self.assertIn("result", self._run(report, self.guard, filters))

	def test_gate_wise_count_lists_an_unused_active_gate(self):
		_gate(UNUSED_GATE)
		_gate(INACTIVE_GATE, is_active=0)
		vp, _pan = self._pass()
		self._record(vp)
		for user in (self.guard, self.system_manager):
			with self.subTest(user=user):
				out = self._run("Gate Wise Count", user, {"date": nowdate()})
				rows = {r["gate_name"]: r for r in out["result"] if isinstance(r, dict)}
				self.assertIn(UNUSED_GATE, rows)
				self.assertEqual(
					(
						rows[UNUSED_GATE]["checkins"],
						rows[UNUSED_GATE]["checkouts"],
						rows[UNUSED_GATE]["inside"],
					),
					(0, 0, 0),
				)
				self.assertNotIn(INACTIVE_GATE, rows)
				self.assertGreaterEqual(rows[self.gate]["checkins"], 1)
				self.assertGreaterEqual(rows[self.gate]["inside"], 1)

	def test_identity_match_report_shows_masked_ids_only(self):
		first, pan = self._pass(name="FixB Twin One")
		second, _other_pan = self._pass(name="FixB Twin Two", visit_date=add_days(nowdate(), 1))
		# Same ID on both, written underneath: the report is what is under test,
		# not the pass's own duplicate checks.
		frappe.db.set_value("Visitor Pass", second.name, "id_proof_number", pan, update_modified=False)

		out = self._run("Visitor Identity Match Report", self.system_manager, self._window())
		pair = [
			r
			for r in out["result"]
			if isinstance(r, dict) and {r["primary_pass"], r["matched_pass"]} == {first.name, second.name}
		]
		self.assertEqual(len(pair), 1, "the two passes sharing an ID number were not matched")
		self.assertIn("ID Proof", pair[0]["match_basis"])
		self.assertEqual(pair[0]["id_proof_number"], mask_id("PAN Card", pan))
		self.assertNotIn(pan, frappe.as_json(out["result"]), "the report carried a full ID number")

		# The report is for System Managers; a guard is not offered it.
		with self.assertRaises(frappe.PermissionError):
			self._run("Visitor Identity Match Report", self.guard, self._window())

	def test_hospitality_schedule_scopes_a_viewer_without_read(self):
		from visitormanagement.visitor_management.report.daily_hospitality_schedule import (
			daily_hospitality_schedule as schedule,
		)

		frappe.set_user(self.guard)
		self.assertFalse(frappe.has_permission("Hospitality Request", "read"))
		scope = schedule._row_scope()
		self.assertIn("`tabHospitality Request`.`owner`", scope)
		linked = [c for c in schedule._get_columns() if c.get("fieldname") == "hospitality_request"]
		self.assertEqual(linked[0]["fieldtype"], "Data")
		self.assertEqual(schedule._requests_for_day(frappe.utils.getdate(nowdate())), [])

		frappe.set_user("Administrator")
		linked = [c for c in schedule._get_columns() if c.get("fieldname") == "hospitality_request"]
		self.assertEqual(linked[0]["fieldtype"], "Link")
		columns, data = schedule.execute({"date": nowdate()})
		self.assertTrue(columns)
		self.assertIsInstance(data, list)


# ─── Form script: the parts of DEF-2 / DEF-3 / masking that live in the browser ──
class TestSecurityLogFormScript(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		folder = frappe.get_app_path("visitormanagement", "visitor_management", "doctype", "security_log")
		with open(os.path.join(folder, "security_log.js"), encoding="utf-8") as handle:
			source = handle.read()
		# Code only: what a comment says must not satisfy (or fail) a check.
		cls.code = "\n".join(line for line in source.splitlines() if not line.strip().startswith("//"))
		with open(os.path.join(folder, "security_log.json"), encoding="utf-8") as handle:
			cls.meta = json.load(handle)

	def test_new_log_is_saved_only_by_the_record_button(self):
		self.assertIn("frappe.validated = false", self.code)
		self.assertIn("frm.disable_save(true)", self.code)
		self.assertIn("frm.page.set_primary_action(gate_record_label(frm)", self.code)
		# The press is noted immediately before the save it starts, nowhere else.
		self.assertEqual(self.code.count("__gate_record_intent = Date.now()"), 1)
		self.assertRegex(self.code, r"__gate_record_intent = Date\.now\(\);[\s\S]{0,120}?frm\.save\(")

	def test_form_never_reads_the_full_id_number(self):
		self.assertNotIn('frappe.db.get_value(\n\t\t\t"Visitor Pass"', self.code)
		self.assertNotIn("with_doc", self.code)
		self.assertNotIn("build_masked_id", self.code)
		self.assertIn("id_proof_number_masked", self.code)
		self.assertFalse(re.search(r"r\.id_proof_number\b", self.code))

	def test_guidance_is_set_after_clearing_not_before(self):
		self.assertNotIn("clear_headline(", self.code)
		self.assertIn("function render_gate_intro", self.code)

	def test_required_marks_are_not_hardcoded_in_the_doctype(self):
		fields = {f["fieldname"]: f for f in self.meta["fields"]}
		for fieldname in ("photo_at_gate", "id_proof_match", "pass_photo_match"):
			self.assertNotIn("mandatory_depends_on", fields[fieldname])
			self.assertFalse(fields[fieldname].get("reqd"))
		for fieldname in ("security_officer_name", "host_name"):
			self.assertEqual(fields[fieldname]["fieldtype"], "Data")
			self.assertEqual(fields[fieldname].get("read_only"), 1)
		# Links to Employee stay, but may never restrict a guard by User Permission.
		for fieldname in ("security_officer", "person_to_visit"):
			self.assertEqual(fields[fieldname].get("ignore_user_permissions"), 1)
