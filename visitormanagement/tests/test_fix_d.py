# Platform fixes of the 1.1.0 fix round (Developer D).
#
# Locks in:
#   - the ID masking helper, the audited reveal and who may use it;
#   - Visitor Blacklist: the number is stored, masked and never serialised, and
#     matching still works on the stored value;
#   - who may open an ID scan / visa copy, and that no other app's file is touched;
#   - Facility / Hospitality Manager see only the passes their work is about;
#   - link_query honours "Ignore User Permissions" for one named field only;
#   - the one-time upgrade steps (gate checks, walk-ins, blacklist, export);
#   - the retention purge reaches every copy of a visitor's identity, stays off
#     by default and never touches the blacklist;
#   - the upgrade-steps caller names the step that failed.
#
# Every fixture is created inside the test and rolled back. Rows that need
# masters this suite must not depend on (Employee, Visitor Pass, ...) are written
# with db_insert, which runs no validation and no hooks.

import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.permissions import setup_custom_perms
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, now_datetime, nowdate

from visitormanagement import permissions, setup
from visitormanagement.visitor_management import id_masking, link_details, tasks, upgrades
from visitormanagement.visitor_management import settings as vms_settings
from visitormanagement.visitor_management.doctype.visitor_blacklist.visitor_blacklist import (
	VisitorBlacklist,
)
from visitormanagement.visitor_management.upgrades import platform_steps

TEST_DOMAIN = "vms-fix-d.example.com"


# ─────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────
def _hash(length=8):
	return frappe.generate_hash(length=length)


def _make_user(prefix, roles):
	"""A System User holding exactly `roles` (plus Frappe's automatic ones)."""
	email = f"{prefix}-{_hash(6)}@{TEST_DOMAIN}"
	frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": prefix,
			"send_welcome_email": 0,
			"user_type": "System User",
		}
	).insert(ignore_permissions=True)
	# Written directly: other apps' User hooks drop some roles on save (ERPNext
	# removes "Employee" from a user with no Employee record).
	for role in roles:
		row = frappe.get_doc(
			{
				"doctype": "Has Role",
				"parent": email,
				"parenttype": "User",
				"parentfield": "roles",
				"role": role,
			}
		)
		row.name = _hash(10)
		row.db_insert()
	frappe.clear_cache(user=email)
	return email


def _raw_insert(doctype, name=None, owner="Administrator", **values):
	"""Insert one row as it is: no validation, no naming, no hooks."""
	doc = frappe.get_doc({"doctype": doctype, **values})
	doc.name = name or f"ZZFIXD-{_hash(10)}"
	doc.owner = owner
	doc.creation = doc.modified = now_datetime()
	doc.modified_by = owner
	doc.db_insert()
	return doc


def _make_employee(user):
	return _raw_insert(
		"Employee", first_name="Fix D", employee_name="Fix D Host", user_id=user, status="Active"
	).name


def _make_visitor_type(approver_role, secondary_approver_role=None):
	name = f"ZZ Fix D {_hash(6)}"
	return _raw_insert(
		"Visitor Type",
		name=name,
		visitor_type_name=name,
		is_active=1,
		approver_role=approver_role,
		secondary_approver_role=secondary_approver_role,
	).name


def _make_pass(**values):
	defaults = {
		"visitor_full_name": "Fix D Visitor",
		"mobile_number": "+919876500000",
		"email_id": "visitor@example.com",
		"status": "Approved",
		"workflow_state": "Approved",
		"request_channel": "Desk",
		"visit_date": nowdate(),
		"docstatus": 0,
	}
	defaults.update(values)
	return _raw_insert("Visitor Pass", **defaults)


def _make_file_row(file_url, doctype=None, name=None, fieldname=None, owner="Administrator"):
	"""A File row without bytes on disk: enough for every permission decision."""
	return _raw_insert(
		"File",
		name=_hash(10),
		owner=owner,
		file_name=file_url.rsplit("/", 1)[-1],
		file_url=file_url,
		is_private=1,
		attached_to_doctype=doctype,
		attached_to_name=name,
		attached_to_field=fieldname,
	)


def _clear_marker(key):
	frappe.defaults.clear_default(key=key, parent="__default")


_NOT_SET = object()


class _FixDTestCase(FrappeTestCase):
	_saved_request = _NOT_SET

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.db.rollback()
		# Nothing read during a test may outlive the rollback.
		frappe.clear_cache()
		frappe.cache.delete_value("vms_id_proof_types")
		if self._saved_request is not _NOT_SET:
			saved_request, saved_form_dict = self._saved_request
			if saved_request is _NOT_SET:
				if hasattr(frappe.local, "request"):
					del frappe.local.request
			else:
				frappe.local.request = saved_request
			frappe.local.form_dict = saved_form_dict
			self._saved_request = _NOT_SET

	def fake_request(self, path="/api/method/ping", **form_dict):
		"""Make this look like a web request for `path` with these parameters."""
		if self._saved_request is _NOT_SET:
			self._saved_request = (
				getattr(frappe.local, "request", _NOT_SET),
				getattr(frappe.local, "form_dict", None) or frappe._dict(),
			)
		frappe.local.request = SimpleNamespace(path=path, method="GET", headers={})
		frappe.local.form_dict = frappe._dict(form_dict)


# ─────────────────────────────────────────────────────────
# 1. Masking helper
# ─────────────────────────────────────────────────────────
class TestMaskId(_FixDTestCase):
	def test_aadhaar_uses_the_uidai_layout(self):
		for raw in ("234567890123", "2345 6789 0123", "2345-6789-0123", " 2345 67890123 "):
			with self.subTest(raw=raw):
				self.assertEqual(id_masking.mask_id("Aadhaar", raw), "XXXX-XXXX-0123")
		# The alias resolves to the same type.
		self.assertEqual(id_masking.mask_id("aadhar", "234567890123"), "XXXX-XXXX-0123")

	def test_other_types_keep_the_last_four_and_their_separators(self):
		self.assertEqual(id_masking.mask_id("PAN Card", "AABPR2345T"), "XXXXXX345T")
		self.assertEqual(id_masking.mask_id("Passport", "P1234567"), "XXXX4567")
		self.assertEqual(id_masking.mask_id("Driving License", "TN05 20210012345"), "XXXX XXXXXXX2345")
		self.assertEqual(id_masking.mask_id("Voter ID", "ABC/123-4567"), "XXX/XXX-4567")
		self.assertEqual(id_masking.mask_id("Foreign Passport", "AB1234567"), "XXXXX4567")
		# An unknown or missing type still masks.
		self.assertEqual(id_masking.mask_id(None, "AABPR2345T"), "XXXXXX345T")
		self.assertEqual(id_masking.mask_id("No Such Type", "AABPR2345T"), "XXXXXX345T")

	def test_short_values_never_show_more_than_half(self):
		self.assertEqual(id_masking.mask_id(None, "1234"), "XX34")
		self.assertEqual(id_masking.mask_id(None, "12345"), "XXX45")
		self.assertEqual(id_masking.mask_id(None, "123"), "XX3")
		self.assertEqual(id_masking.mask_id(None, "7"), "X")
		self.assertEqual(id_masking.mask_id("PAN Card", "AB12"), "XX12")

	def test_empty_and_odd_input(self):
		for empty in (None, "", "   "):
			self.assertEqual(id_masking.mask_id("Aadhaar", empty), "")
		# Nothing recognisable: nothing shown.
		self.assertEqual(id_masking.mask_id(None, "--/--"), "XXXX")
		# A number, not a string.
		self.assertEqual(id_masking.mask_id("Aadhaar", 234567890123), "XXXX-XXXX-0123")
		# A legacy Aadhaar of the wrong length falls back to the general rule.
		self.assertEqual(id_masking.mask_id("Aadhaar", "12345678"), "XXXX5678")

	def test_masking_is_idempotent(self):
		for id_type, raw in (
			("Aadhaar", "234567890123"),
			("PAN Card", "AABPR2345T"),
			(None, "DL-TN-05210099"),
			(None, "1234"),
		):
			once = id_masking.mask_id(id_type, raw)
			self.assertEqual(id_masking.mask_id(id_type, once), once)

	def test_never_raises(self):
		with patch.object(id_masking, "_mask", side_effect=RuntimeError("boom")):
			self.assertEqual(id_masking.mask_id("Aadhaar", "234567890123"), "XXXX")
		with patch.object(id_masking, "_type_config", side_effect=RuntimeError("boom")):
			self.assertEqual(id_masking.mask_id("Aadhaar", "234567890123"), "XXXX")

	def test_visible_characters_follow_the_id_proof_type(self):
		name = f"ZZ Fix D Permit {_hash(5)}"
		doc = frappe.get_doc(
			{
				"doctype": "ID Proof Type",
				"id_proof_type_name": name,
				"validation_method": "None",
				"normalisation": "None",
				"visible_trailing_chars": 2,
			}
		).insert(ignore_permissions=True)
		self.assertEqual(id_masking.mask_id(name, "RP-123456"), "XX-XXXX56")

		doc.visible_trailing_chars = 0
		doc.save(ignore_permissions=True)
		self.assertEqual(id_masking.mask_id(name, "RP-123456"), "XX-XXXXXX")

		doc.visible_trailing_chars = 7
		self.assertRaises(frappe.ValidationError, doc.save, ignore_permissions=True)

	def test_set_masked_and_consume_entry(self):
		row = frappe._dict(id_proof_type="PAN Card", id_proof_number="AABPR2345T")
		row.set = row.__setitem__
		id_masking.set_masked(row)
		self.assertEqual(row.id_proof_number_masked, "XXXXXX345T")

		row.id_proof_number_entry = " zzzpr9999k "
		self.assertTrue(id_masking.consume_entry(row, normalise=False))
		self.assertEqual(row.id_proof_number, "zzzpr9999k")
		self.assertIsNone(row.id_proof_number_entry)
		self.assertEqual(row.id_proof_number_masked, "XXXXXX999k")
		# Nothing typed: the stored number stays.
		self.assertFalse(id_masking.consume_entry(row, normalise=False))
		self.assertEqual(row.id_proof_number, "zzzpr9999k")

	def test_can_view_full_id_is_system_manager_only(self):
		manager = _make_user("fixd-sm", ["System Manager"])
		guard = _make_user("fixd-guard", ["Security"])
		self.assertTrue(id_masking.can_view_full_id("Administrator"))
		self.assertTrue(id_masking.can_view_full_id(manager))
		self.assertFalse(id_masking.can_view_full_id(guard))
		self.assertFalse(id_masking.can_view_full_id("Guest"))


# ─────────────────────────────────────────────────────────
# 2. Visitor Blacklist: stored, masked, matched, revealed
# ─────────────────────────────────────────────────────────
class TestBlacklistMasking(_FixDTestCase):
	def _entry(self, **values):
		doc = frappe.get_doc(
			{"doctype": "Visitor Blacklist", "reason": "Fix D test", "is_active": 1, **values}
		)
		return doc.insert(ignore_permissions=True)

	def test_typed_number_is_stored_masked_and_never_serialised(self):
		number = f"ZZ{_hash(6).upper()}-77-4321"
		entry = self._entry(id_proof_number_entry=f"  {number}  ")

		stored = frappe.db.get_value(
			"Visitor Blacklist",
			entry.name,
			["id_proof_number", "id_proof_number_masked", "id_proof_number_entry"],
			as_dict=True,
		)
		self.assertEqual(stored.id_proof_number, number)
		self.assertTrue(stored.id_proof_number_masked.endswith("4321"))
		self.assertNotIn(number[:6], stored.id_proof_number_masked)
		self.assertFalse(stored.id_proof_number_entry)

		for payload in (entry.as_dict(), frappe.get_doc("Visitor Blacklist", entry.name).as_dict()):
			self.assertNotIn("id_proof_number", payload)
			self.assertNotIn("id_proof_number_entry", payload)
			self.assertNotIn(number, frappe.as_json(payload))

	def test_number_sent_in_the_stored_field_by_a_non_admin_is_not_lost(self):
		manager = _make_user("fixd-sm", ["System Manager"])
		number = f"LEGACY{_hash(6).upper()}"
		frappe.set_user(manager)
		entry = frappe.get_doc(
			{
				"doctype": "Visitor Blacklist",
				"reason": "API caller",
				"is_active": 1,
				"id_proof_number": number,
			}
		).insert()
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Visitor Blacklist", entry.name, "id_proof_number"), number)

	def test_saving_as_a_user_keeps_the_stored_number(self):
		manager = _make_user("fixd-sm", ["System Manager"])
		number = f"KEEP{_hash(6).upper()}"
		entry = self._entry(id_proof_number_entry=number)

		frappe.set_user(manager)
		# What the browser sends back: the document without the hidden fields.
		doc = frappe.get_doc(json.loads(frappe.get_doc("Visitor Blacklist", entry.name).as_json()))
		doc.reason = "Edited by a System Manager"
		doc.save()
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Visitor Blacklist", entry.name, "id_proof_number"), number)

	def test_matching_still_uses_the_full_number(self):
		number = f"ABCD-{_hash(4).upper()}-5678"
		entry = self._entry(id_proof_number_entry=number)
		for variant in (number, number.lower(), number.replace("-", ""), number.replace("-", " ")):
			with self.subTest(variant=variant):
				self.assertEqual(VisitorBlacklist.find_active_match(id_proof_number=variant), entry.name)
		# The masked value is not the number.
		self.assertIsNone(
			VisitorBlacklist.find_active_match(
				id_proof_number=frappe.db.get_value("Visitor Blacklist", entry.name, "id_proof_number_masked")
			)
		)

	def test_duplicate_check_compares_like_the_gate(self):
		number = f"DUP-{_hash(6).upper()}-01"
		self._entry(id_proof_number_entry=number)
		with self.assertRaises(frappe.ValidationError) as ctx:
			self._entry(id_proof_number_entry=number.lower().replace("-", " "))
		self.assertIn("already exists", str(ctx.exception))

	def test_identification_rules_match_what_the_matcher_can_enforce(self):
		# Nothing that identifies anyone.
		self.assertRaises(frappe.ValidationError, self._entry)
		self.assertRaises(frappe.ValidationError, self._entry, id_proof_type=None, visitor_name="  ")
		# A mobile number too short to be matched.
		self.assertRaises(
			frappe.ValidationError, self._entry, visitor_name="Fix D Short", mobile_number="12345"
		)

		# Name + mobile blocks: accepted (it used to be refused).
		name = f"Fix D Barred {_hash(5)}"
		entry = self._entry(visitor_name=name, mobile_number="+919812345670")
		self.assertEqual(
			VisitorBlacklist.find_active_match(visitor_name=name.upper(), mobile_number="9812345670"),
			entry.name,
		)

		# A name alone only warns: saved, with a message saying so.
		frappe.clear_messages()
		weak_name = f"Fix D Warn Only {_hash(5)}"
		weak = self._entry(visitor_name=weak_name)
		self.assertIn(
			"only raise a warning", " ".join(frappe.as_unicode(m) for m in frappe.get_message_log())
		)
		self.assertIsNone(
			VisitorBlacklist.find_active_match(visitor_name=weak_name, mobile_number="9000000001")
		)
		self.assertEqual(VisitorBlacklist.find_weak_match(visitor_name=weak_name)["name"], weak.name)

	def test_version_history_never_holds_the_full_number(self):
		first, second = f"OLD{_hash(6).upper()}1111", f"NEW{_hash(6).upper()}2222"
		entry = self._entry(id_proof_number_entry=first)
		entry.id_proof_number_entry = second
		entry.reason = "Number corrected"
		# Document.save keeps no Version while tests run unless asked to.
		entry.save(ignore_permissions=True, ignore_version=False)

		history = " ".join(
			frappe.get_all(
				"Version", filters={"ref_doctype": "Visitor Blacklist", "docname": entry.name}, pluck="data"
			)
		)
		self.assertIn("Number corrected", history)
		self.assertNotIn(first, history)
		self.assertNotIn(second, history)
		self.assertEqual(frappe.db.get_value("Visitor Blacklist", entry.name, "id_proof_number"), second)

	def test_old_version_rows_are_scrubbed(self):
		number = f"HIST{_hash(6).upper()}9876"
		entry = self._entry(id_proof_number_entry=number)
		version = _raw_insert(
			"Version",
			name=_hash(10),
			ref_doctype="Visitor Blacklist",
			docname=entry.name,
			data=frappe.as_json(
				{"changed": [["id_proof_number", "", number], ["reason", "a", "b"]]}, indent=None
			),
		)
		self.assertEqual(
			id_masking.scrub_versions("Visitor Blacklist", ("id_proof_number",), [entry.name]), 1
		)
		data = frappe.db.get_value("Version", version.name, "data")
		self.assertNotIn(number, data)
		self.assertIn("9876", data)
		self.assertIn('"reason"', data)
		# Idempotent.
		self.assertEqual(
			id_masking.scrub_versions("Visitor Blacklist", ("id_proof_number",), [entry.name]), 0
		)

	def test_backfill_fills_existing_rows(self):
		number = f"BACK{_hash(6).upper()}4455"
		row = _raw_insert("Visitor Blacklist", reason="legacy row", is_active=1, id_proof_number=number)
		self.assertGreaterEqual(id_masking.backfill_masked("Visitor Blacklist"), 1)
		masked = frappe.db.get_value("Visitor Blacklist", row.name, "id_proof_number_masked")
		self.assertTrue(masked.endswith("4455"))
		self.assertNotEqual(masked, number)

	def test_reveal_is_for_system_manager_and_is_recorded(self):
		manager = _make_user("fixd-sm", ["System Manager"])
		guard = _make_user("fixd-guard", ["Security"])
		number = f"SEE{_hash(6).upper()}0042"
		entry = self._entry(id_proof_number_entry=number)

		frappe.set_user(guard)
		self.assertRaises(frappe.PermissionError, id_masking.reveal_id, "Visitor Blacklist", entry.name)
		frappe.set_user("Guest")
		self.assertRaises(frappe.PermissionError, id_masking.reveal_id, "Visitor Blacklist", entry.name)

		frappe.set_user(manager)
		self.assertEqual(id_masking.reveal_id("Visitor Blacklist", entry.name), number)
		# Only a field with a masked companion, on one of this app's DocTypes.
		self.assertRaises(
			frappe.PermissionError, id_masking.reveal_id, "Visitor Blacklist", entry.name, "mobile_number"
		)
		self.assertRaises(frappe.PermissionError, id_masking.reveal_id, "User", manager, "email")
		frappe.set_user("Administrator")

		log = frappe.get_all(
			"Activity Log",
			filters={"reference_doctype": "Visitor Blacklist", "reference_name": entry.name, "user": manager},
			fields=["subject", "content"],
		)
		self.assertEqual(len(log), 1, "one audit row per reveal, none for the refused attempts")
		self.assertIn(entry.name, log[0].subject)
		self.assertIn("id_proof_number", log[0].content)
		self.assertNotIn(number, log[0].content)
		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{
					"reference_doctype": "Visitor Blacklist",
					"reference_name": entry.name,
					"comment_type": "Info",
					"subject": id_masking.REVEAL_COMMENT_SUBJECT,
				},
			)
		)

	def test_the_revealer_cannot_edit_or_delete_the_audit_row(self):
		manager = _make_user("fixd-sm", ["System Manager"])
		for ptype in ("write", "delete", "create"):
			self.assertFalse(
				frappe.has_permission("Activity Log", ptype, user=manager),
				f"System Manager must not hold {ptype} on Activity Log",
			)
		self.assertTrue(frappe.has_permission("Activity Log", "read", user=manager))

	def test_no_audit_no_reveal(self):
		manager = _make_user("fixd-sm", ["System Manager"])
		entry = self._entry(id_proof_number_entry=f"NOLOG{_hash(6).upper()}")
		frappe.set_user(manager)
		with patch.object(id_masking, "log_reveal", side_effect=frappe.ValidationError("no audit")):
			self.assertRaises(frappe.ValidationError, id_masking.reveal_id, "Visitor Blacklist", entry.name)

	def test_legacy_audit_output_is_masked(self):
		from visitormanagement.visitor_management import validators

		number = f"NOTAPAN{_hash(5).upper()}"
		visitor_pass = _make_pass(id_proof_type="PAN Card", id_proof_number=number)
		failures = [row for row in validators.audit_legacy_id_proofs() if row["name"] == visitor_pass.name]
		self.assertEqual(len(failures), 1)
		self.assertNotIn("id_proof_number", failures[0])
		self.assertNotIn(number, frappe.as_json(failures[0]))
		self.assertTrue(failures[0]["id_proof_number_masked"].endswith(number[-4:]))


# ─────────────────────────────────────────────────────────
# 3. ID scans and visa copies; Facility / Hospitality Manager scope
# ─────────────────────────────────────────────────────────
class TestIdDocumentAccess(_FixDTestCase):
	def setUp(self):
		self.approver = _make_user("fixd-approver", ["Sales Manager"])
		self.second_approver = _make_user("fixd-second", ["HR Manager"])
		self.other_approver = _make_user("fixd-other", ["HOD"])
		self.guard = _make_user("fixd-guard", ["Security"])
		self.manager = _make_user("fixd-sm", ["System Manager"])
		self.host = _make_user("fixd-host", ["Employee", "Host Employee"])
		self.owner = _make_user("fixd-owner", ["Employee"])
		self.employee = _make_user("fixd-staff", ["Employee"])
		self.facility = _make_user("fixd-facility", ["Facility Manager"])
		self.hospitality = _make_user("fixd-hosp", ["Hospitality Manager"])

		self.visitor_type = _make_visitor_type("Sales Manager", "HR Manager")
		self.scan_url = f"/private/files/zz_fixd_scan_{_hash()}.png"
		self.visa_url = f"/private/files/zz_fixd_visa_{_hash()}.pdf"
		self.photo_url = f"/private/files/zz_fixd_photo_{_hash()}.png"
		self.visitor_pass = _make_pass(
			owner=self.owner,
			visitor_type=self.visitor_type,
			person_to_visit=_make_employee(self.host),
			id_proof_scan=self.scan_url,
			custom_visa_copy=self.visa_url,
			visitor_photo=self.photo_url,
			meal_required=1,
			conference_room=None,
		)
		# Uploaded by the owner of the pass, as a receptionist would.
		self.scan = _make_file_row(
			self.scan_url, "Visitor Pass", self.visitor_pass.name, "id_proof_scan", owner=self.owner
		)
		self.visa = _make_file_row(self.visa_url, "Visitor Pass", self.visitor_pass.name, "custom_visa_copy")
		self.photo = _make_file_row(self.photo_url, "Visitor Pass", self.visitor_pass.name, "visitor_photo")

	ALLOWED = ("guard", "approver", "second_approver", "manager")
	REFUSED = ("host", "owner", "employee", "facility", "hospitality", "other_approver")

	def test_audience_of_an_id_document(self):
		for who in self.ALLOWED:
			with self.subTest(allowed=who):
				user = getattr(self, who)
				self.assertTrue(permissions.can_open_id_documents(self.visitor_pass.name, user))
				self.assertTrue(permissions.can_open_id_documents(self.visitor_pass, user))
				for url in (self.scan_url, self.visa_url):
					self.assertTrue(permissions.id_document_access(url, user))
		for who in self.REFUSED:
			with self.subTest(refused=who):
				user = getattr(self, who)
				self.assertFalse(permissions.can_open_id_documents(self.visitor_pass.name, user))
				for url in (self.scan_url, self.visa_url):
					self.assertIs(permissions.id_document_access(url, user), False)
		self.assertFalse(permissions.can_open_id_documents(self.visitor_pass.name, "Guest"))
		self.assertTrue(permissions.can_open_id_documents(self.visitor_pass.name, "Administrator"))
		# Roles alone, no document: the two blanket roles only.
		self.assertTrue(permissions.can_open_id_documents(None, self.guard))
		self.assertFalse(permissions.can_open_id_documents(None, self.approver))

	def test_file_hook_only_ever_refuses(self):
		for who in self.ALLOWED:
			self.assertIsNone(
				permissions.has_id_document_file_permission(self.scan, getattr(self, who), "read")
			)
		for who in self.REFUSED:
			with self.subTest(refused=who):
				for ptype in ("read", "share", "print", "email", "export"):
					self.assertIs(
						permissions.has_id_document_file_permission(self.visa, getattr(self, who), ptype),
						False,
					)
				# Replacing or removing the attachment stays with the record's write permission.
				for ptype in ("write", "delete", "create"):
					self.assertIsNone(
						permissions.has_id_document_file_permission(self.scan, getattr(self, who), ptype)
					)
		# The photo is not an ID document.
		for who in self.ALLOWED + self.REFUSED:
			self.assertIsNone(
				permissions.has_id_document_file_permission(self.photo, getattr(self, who), "read")
			)
			self.assertIsNone(permissions.id_document_access(self.photo_url, getattr(self, who)))

	def test_the_hooks_are_registered(self):
		self.assertIn(
			"visitormanagement.permissions.has_id_document_file_permission",
			frappe.get_hooks("has_permission").get("File", []),
		)
		self.assertIn(
			"visitormanagement.permissions.guard_id_document_request", frappe.get_hooks("auth_hooks")
		)
		# Asked through Frappe, the hook's refusal is what decides.
		self.assertFalse(frappe.has_permission("File", "read", doc=self.scan, user=self.host))

	def test_request_guard_refuses_only_the_wrong_audience(self):
		for path, form in (
			(self.scan_url, {}),
			("/api/method/download_file", {"file_url": self.visa_url}),
		):
			for who in self.REFUSED:
				with self.subTest(path=path, refused=who):
					frappe.set_user(getattr(self, who))
					self.fake_request(path, **form)
					self.assertRaises(frappe.PermissionError, permissions.guard_id_document_request)
			for who in self.ALLOWED:
				with self.subTest(path=path, allowed=who):
					frappe.set_user(getattr(self, who))
					self.fake_request(path, **form)
					permissions.guard_id_document_request()

		# Not a file request, the photo, an unknown file, and a Guest (left to Frappe).
		frappe.set_user(self.host)
		for path, form in (
			("/api/method/frappe.client.get", {"doctype": "Visitor Pass"}),
			(self.photo_url, {}),
			("/private/files/zz_fixd_no_such_file.png", {}),
		):
			self.fake_request(path, **form)
			permissions.guard_id_document_request()
		frappe.set_user("Guest")
		self.fake_request(self.scan_url)
		permissions.guard_id_document_request()

	def test_uploader_keeps_the_file_only_while_the_form_is_unsaved(self):
		url = f"/private/files/zz_fixd_unsaved_{_hash()}.png"
		_make_file_row(url, "Visitor Pass", f"new-visitor-pass-{_hash(6)}", "id_proof_scan", owner=self.host)
		self.assertTrue(permissions.id_document_access(url, self.host))
		self.assertIs(permissions.id_document_access(url, self.employee), False)
		# Once the pass exists, having uploaded the scan no longer opens it.
		self.assertIs(permissions.id_document_access(self.scan_url, self.owner), False)

	def test_sidebar_copy_and_pasted_url_do_not_open_an_id_document(self):
		# The same scan attached again from the sidebar: no field on the File row.
		_make_file_row(self.scan_url, "Visitor Pass", self.visitor_pass.name, None, owner=self.host)
		self.assertIs(permissions.id_document_access(self.scan_url, self.host), False)

		# The scan's URL pasted into the photo field of a pass the host owns.
		own_pass = _make_pass(owner=self.host, visitor_type=self.visitor_type, visitor_photo=self.scan_url)
		_make_file_row(self.scan_url, "Visitor Pass", own_pass.name, "visitor_photo", owner=self.host)
		self.assertIs(permissions.id_document_access(self.scan_url, self.host), False)
		self.assertTrue(permissions.id_document_access(self.scan_url, self.guard))

	def test_security_log_copy_of_the_scan_follows_the_same_rule(self):
		log = _raw_insert(
			"Security Log",
			visitor_pass=self.visitor_pass.name,
			event_type="Check-In",
			id_proof_scan=self.scan_url,
		)
		row = _make_file_row(self.scan_url, "Security Log", log.name, "id_proof_scan")
		self.assertTrue(permissions.can_open_id_documents(log, self.approver))
		self.assertTrue(permissions.can_open_id_documents(log.name, self.guard, doctype="Security Log"))
		self.assertFalse(permissions.can_open_id_documents(log, self.host))
		self.assertIs(permissions.has_id_document_file_permission(row, self.host, "read"), False)

	def test_other_apps_files_are_unaffected(self):
		from frappe.core.doctype.file.file import has_permission as core_file_permission

		# A courier-style private file on a record of another app (here: a core ToDo).
		todo = frappe.get_doc(
			{"doctype": "ToDo", "description": "Courier label", "allocated_to": self.employee}
		).insert(ignore_permissions=True)
		url = f"/private/files/zz_fixd_courier_label_{_hash()}.pdf"
		courier = _make_file_row(url, "ToDo", todo.name, "attachment")
		unattached = _make_file_row(f"/private/files/zz_fixd_loose_{_hash()}.pdf", owner=self.employee)

		for user in (self.employee, self.host, self.guard, self.manager, "Guest"):
			with self.subTest(user=user):
				for ptype in ("read", "write", "delete", "share"):
					self.assertIsNone(permissions.has_id_document_file_permission(courier, user, ptype))
					self.assertIsNone(permissions.has_id_document_file_permission(unattached, user, ptype))
				self.assertIsNone(permissions.id_document_access(url, user))

		# The request guard lets it through untouched, and Frappe's own answer is the same as ever.
		frappe.set_user(self.employee)
		self.fake_request(url)
		permissions.guard_id_document_request()
		frappe.set_user("Administrator")
		self.assertTrue(core_file_permission(courier, "read", user=self.employee))
		self.assertTrue(frappe.has_permission("File", "read", doc=courier, user=self.employee))
		self.assertTrue(frappe.has_permission("File", "read", doc=unattached, user=self.employee))

		# Identical bytes share one URL. If another app's record holds the very file a pass
		# uses as its ID scan, that record's readers still get it through that record.
		shared = _make_file_row(self.scan_url, "ToDo", todo.name, "attachment")
		self.assertTrue(permissions.id_document_access(self.scan_url, self.employee))
		self.assertIs(permissions.id_document_access(self.scan_url, self.host), False)
		self.assertIsNone(permissions.has_id_document_file_permission(shared, self.host, "read"))

	def test_facility_and_hospitality_manager_see_only_their_passes(self):
		meal_pass = self.visitor_pass  # meal_required
		room_pass = _make_pass(visitor_type=self.visitor_type, conference_room="ZZ Fix D Room")
		plain_pass = _make_pass(visitor_type=self.visitor_type)
		linked_pass = _make_pass(visitor_type=self.visitor_type)
		_raw_insert("Hospitality Request", visitor_pass=linked_pass.name)
		booked_pass = _make_pass(visitor_type=self.visitor_type)
		_raw_insert("Conference Room Booking", visitor_pass=booked_pass.name, meeting_title="Fix D")

		def may(user, doc, ptype="read"):
			return permissions.has_visitor_pass_permission(doc, user=user, ptype=ptype)

		self.assertTrue(may(self.hospitality, meal_pass))
		self.assertTrue(may(self.hospitality, linked_pass))
		self.assertTrue(may(self.hospitality, linked_pass, "select"))
		self.assertFalse(may(self.hospitality, room_pass))
		self.assertFalse(may(self.hospitality, plain_pass))
		self.assertTrue(may(self.facility, room_pass))
		self.assertTrue(may(self.facility, booked_pass))
		self.assertFalse(may(self.facility, meal_pass))
		self.assertFalse(may(self.facility, plain_pass))
		# Never more than opening the pass.
		for ptype in ("write", "submit", "export", "print", "email", "report", "share", "delete"):
			self.assertFalse(may(self.hospitality, meal_pass, ptype))
			self.assertFalse(may(self.facility, room_pass, ptype))

		names = [meal_pass.name, room_pass.name, plain_pass.name, linked_pass.name, booked_pass.name]

		def listed(user):
			condition = permissions.get_visitor_pass_permission_query_conditions(user)
			self.assertTrue(condition, "these roles no longer see every pass")
			placeholders = ", ".join(["%s"] * len(names))
			return set(
				frappe.db.sql_list(
					f"select name from `tabVisitor Pass` where name in ({placeholders}) and {condition}",
					names,
				)
			)

		self.assertEqual(listed(self.hospitality), {meal_pass.name, linked_pass.name})
		self.assertEqual(listed(self.facility), {room_pass.name, booked_pass.name})

	def test_full_id_number_cannot_be_used_as_a_filter(self):
		frappe.set_user(self.host)
		for form in (
			{"doctype": "Visitor Pass", "filters": '[["Visitor Pass","id_proof_number","like","12%"]]'},
			{"filters": '{"id_proof_number": "ABCDE1234F"}'},
			{"doctype": "Visitor Pass", "order_by": "`tabVisitor Pass`.`id_proof_number` asc"},
			{"doctype": "Visitor Pass", "group_by_field": "id_proof_number"},
		):
			with self.subTest(form=form):
				self.fake_request("/api/method/frappe.desk.reportview.get", **form)
				self.assertRaises(
					frappe.PermissionError,
					permissions.get_visitor_pass_permission_query_conditions,
					self.host,
				)
		for form in (
			{
				"doctype": "Visitor Pass",
				"filters": '[["Visitor Pass","id_proof_number_masked","like","%1234"]]',
			},
			{"doctype": "Visitor Pass", "filters": '{"status": "Approved"}'},
			# Another DocType's own column of that name (the gate log holds a masked value).
			{"doctype": "Security Log", "filters": '{"id_proof_number": "XXXX-XXXX-1234"}'},
		):
			with self.subTest(form=form):
				self.fake_request("/api/method/frappe.desk.reportview.get", **form)
				self.assertTrue(permissions.get_visitor_pass_permission_query_conditions(self.host))

		# A System Manager may: they can read the number through the audited reveal anyway.
		frappe.set_user(self.manager)
		self.fake_request("/api/method/frappe.desk.reportview.get", filters='{"id_proof_number": "X"}')
		self.assertIsNone(permissions.get_visitor_pass_permission_query_conditions(self.manager))


# ─────────────────────────────────────────────────────────
# 4. VAPT F2: Ignore User Permissions is per field
# ─────────────────────────────────────────────────────────
class TestLinkQueryIgnoreFlag(_FixDTestCase):
	def _ignored(self, doctype, reference_doctype, flag, filters=None, link_fieldname=None):
		"""What link_query decides about User Permissions for this call."""
		with (
			patch.object(link_details, "can_pick", return_value=True),
			patch.object(link_details, "_find", return_value=[]) as find,
		):
			link_details.link_query(
				doctype,
				"",
				"name",
				0,
				20,
				filters or {},
				reference_doctype,
				flag,
				link_fieldname=link_fieldname,
			)
		return find.call_args.kwargs["ignore_user_permissions"]

	def _link_field(self, reference_doctype, target, marked):
		for df in frappe.get_meta(reference_doctype).get_link_fields():
			if df.options == target and bool(df.ignore_user_permissions) == marked:
				return df.fieldname
		return None

	def test_only_the_named_field_can_set_user_permissions_aside(self):
		marked = self._link_field("Visitor Pass", "Employee", True)
		if not marked:
			self.skipTest("Visitor Pass has no Employee link marked Ignore User Permissions")

		# The old hole: the flag alone, with no field named.
		self.assertFalse(self._ignored("Employee", "Visitor Pass", 1))
		self.assertFalse(self._ignored("Employee", "Visitor Pass", "1", filters={"status": "Active"}))
		# The marked field, named in the filters (what the app's pickers send) or directly.
		self.assertTrue(self._ignored("Employee", "Visitor Pass", 1, filters={"link_fieldname": marked}))
		self.assertTrue(
			self._ignored("Employee", "Visitor Pass", 1, filters=json.dumps({"link_fieldname": marked}))
		)
		self.assertTrue(self._ignored("Employee", "Visitor Pass", 1, link_fieldname=marked))
		# The caller did not ask, or named something that is not such a field.
		self.assertFalse(self._ignored("Employee", "Visitor Pass", 0, filters={"link_fieldname": marked}))
		self.assertFalse(
			self._ignored("Employee", "Visitor Pass", 1, filters={"link_fieldname": "visitor_type"})
		)
		self.assertFalse(self._ignored("Employee", "Visitor Pass", 1, filters={"link_fieldname": ["x"]}))
		self.assertFalse(self._ignored("Employee", "No Such DocType", 1, filters={"link_fieldname": marked}))

	def test_a_field_without_the_mark_cannot_borrow_another_fields(self):
		unmarked = self._link_field("Visitor Pass", "Supplier", False)
		if not unmarked:
			self.skipTest("every Supplier link on Visitor Pass is marked Ignore User Permissions")
		self.assertFalse(self._ignored("Supplier", "Visitor Pass", 1, filters={"link_fieldname": unmarked}))
		# Naming an Employee field while searching Supplier does not help either.
		marked = self._link_field("Visitor Pass", "Employee", True)
		if marked:
			self.assertFalse(self._ignored("Supplier", "Visitor Pass", 1, filters={"link_fieldname": marked}))

	def test_the_fieldname_is_not_treated_as_a_filter(self):
		self.assertEqual(link_details._safe_filters("Employee", {"link_fieldname": "person_to_visit"}), [])


# ─────────────────────────────────────────────────────────
# 5. One-time upgrade steps
# ─────────────────────────────────────────────────────────
class TestPlatformSteps(_FixDTestCase):
	def _settings(self, **values):
		for fieldname, value in values.items():
			frappe.db.set_single_value("VMS Settings", fieldname, value, update_modified=False)
		frappe.clear_document_cache("VMS Settings", "VMS Settings")

	def _switches(self):
		return {
			f: frappe.utils.cint(frappe.db.get_single_value("VMS Settings", f))
			for f in platform_steps.GATE_CHECK_SWITCHES
		}

	def test_is_fresh_install(self):
		marker = upgrades._INSTALLED_BY_THIS_VERSION_MARKER
		_clear_marker(marker)
		# frappe.flags is a dict (frappe._dict), so it is patched as one.
		with patch.dict(frappe.flags, {"in_install": False}):
			self.assertFalse(
				upgrades.is_fresh_install(), "a site 1.0.0 installed has no before_install record"
			)
			frappe.db.set_default(marker, "[]")
			self.assertTrue(upgrades.is_fresh_install())
		_clear_marker(marker)
		with patch.dict(frappe.flags, {"in_install": "visitormanagement"}):
			self.assertTrue(upgrades.is_fresh_install())

	def test_gate_checks_are_switched_on_once_on_an_upgrade(self):
		_clear_marker(platform_steps._GATE_CHECKS_MARKER)
		self._settings(**dict.fromkeys(platform_steps.GATE_CHECK_SWITCHES, 0))

		with patch.object(platform_steps, "is_fresh_install", return_value=False):
			platform_steps._carry_gate_checks_over()
			self.assertEqual(set(self._switches().values()), {1})
			self.assertTrue(frappe.db.get_default(platform_steps._GATE_CHECKS_MARKER))
			# settings.py reads through the document cache.
			self.assertEqual(vms_settings.flag("qr_scan_required_at_gate"), 1)

			# An administrator turns one off afterwards: the next migrate leaves it off.
			self._settings(require_visitor_photo=0)
			platform_steps._carry_gate_checks_over()
			self.assertEqual(self._switches()["require_visitor_photo"], 0)

	def test_gate_checks_keep_their_defaults_on_a_fresh_install(self):
		_clear_marker(platform_steps._GATE_CHECKS_MARKER)
		self._settings(**dict.fromkeys(platform_steps.GATE_CHECK_SWITCHES, 0))

		with patch.object(platform_steps, "is_fresh_install", return_value=True):
			platform_steps._carry_gate_checks_over()
		self.assertEqual(set(self._switches().values()), {0})
		self.assertTrue(frappe.db.get_default(platform_steps._GATE_CHECKS_MARKER))
		# Decided once: a later migrate (no longer "fresh" for any reason) does not revisit it.
		with patch.object(platform_steps, "is_fresh_install", return_value=False):
			platform_steps._carry_gate_checks_over()
		self.assertEqual(set(self._switches().values()), {0})

	def test_walk_ins_stay_open_on_an_upgrade_only(self):
		fieldname = "allow_walk_in_pre_registration"
		for fresh, expected in ((False, 1), (True, 0)):
			with self.subTest(fresh=fresh):
				_clear_marker(platform_steps._WALK_IN_MARKER)
				self._settings(**{fieldname: 0})
				with patch.object(platform_steps, "is_fresh_install", return_value=fresh):
					platform_steps._carry_walk_in_over()
				self.assertEqual(
					frappe.utils.cint(frappe.db.get_single_value("VMS Settings", fieldname)), expected
				)
				self.assertEqual(vms_settings.walk_in_allowed(), bool(expected))

	def test_new_setting_defaults_reach_an_existing_site_once(self):
		frappe.db.delete("Singles", {"doctype": "VMS Settings", "field": "require_portal_consent"})
		frappe.clear_document_cache("VMS Settings", "VMS Settings")
		# Never stored: read as required, not as off.
		self.assertTrue(vms_settings.portal_consent_required())
		platform_steps._seed_new_setting_defaults()
		self.assertEqual(platform_steps._stored_setting("require_portal_consent"), "1")
		# Unticked by an administrator: stored as 0 and left alone.
		self._settings(require_portal_consent=0)
		platform_steps._seed_new_setting_defaults()
		self.assertFalse(vms_settings.portal_consent_required())

	def test_inactive_blacklist_entries_are_reported_not_activated(self):
		self.assertFalse(hasattr(setup, "_activate_blacklist_entries"))
		_clear_marker(platform_steps._BLACKLIST_INACTIVE_MARKER)
		_clear_marker(setup._BLACKLIST_BACKFILL_MARKER)
		entry = _raw_insert("Visitor Blacklist", reason="cleared after review", is_active=0, visitor_name="X")

		platform_steps._report_inactive_blacklist_entries()
		platform_steps.run()
		self.assertEqual(frappe.db.get_value("Visitor Blacklist", entry.name, "is_active"), 0)
		self.assertTrue(frappe.db.get_default(platform_steps._BLACKLIST_INACTIVE_MARKER))

	def test_deleted_copies_of_blacklist_entries_lose_the_full_number_once(self):
		number = f"GONE{_hash(6).upper()}7788"
		copy = _raw_insert(
			"Deleted Document",
			name=_hash(10),
			deleted_doctype="Visitor Blacklist",
			deleted_name=f"VB-FIXD-{_hash(5)}",
			data=frappe.as_json(
				{"doctype": "Visitor Blacklist", "reason": "old", "id_proof_number": number, "is_active": 1}
			),
		)
		_clear_marker(platform_steps._BLACKLIST_VERSION_SCRUB_MARKER)
		platform_steps._mask_blacklist_id_numbers()

		data = json.loads(frappe.db.get_value("Deleted Document", copy.name, "data"))
		self.assertNotIn("id_proof_number", data)
		self.assertTrue(data["id_proof_number_masked"].endswith("7788"))
		self.assertNotIn(number, frappe.as_json(data))
		self.assertEqual(data["reason"], "old")
		self.assertTrue(frappe.db.get_default(platform_steps._BLACKLIST_VERSION_SCRUB_MARKER))

	def test_export_on_visitor_pass_is_left_with_system_manager_once(self):
		setup_custom_perms("Visitor Pass")
		rows = frappe.get_all(
			"Custom DocPerm", filters={"parent": "Visitor Pass", "permlevel": 0}, fields=["name", "role"]
		)
		others = [row for row in rows if row.role != "System Manager"]
		if not others:
			self.skipTest("Visitor Pass has no permission row besides System Manager on this site")
		for row in rows:
			frappe.db.set_value("Custom DocPerm", row.name, "export", 1)

		def exporters():
			return set(
				frappe.get_all(
					"Custom DocPerm", filters={"parent": "Visitor Pass", "export": 1}, pluck="role"
				)
			)

		_clear_marker(platform_steps._PASS_EXPORT_MARKER)
		platform_steps._restrict_visitor_pass_export()
		self.assertLessEqual(exporters(), {"System Manager"})
		self.assertTrue(frappe.db.get_default(platform_steps._PASS_EXPORT_MARKER))

		# An administrator grants it back: the next migrate respects that.
		frappe.db.set_value("Custom DocPerm", others[0].name, "export", 1)
		platform_steps._restrict_visitor_pass_export()
		self.assertIn(others[0].role, exporters())

	def test_hospitality_user_can_run_its_report(self):
		self.assertIn("Hospitality User", setup.HOSPITALITY_REPORT_RUNNERS)
		if not frappe.db.exists("Role", "Hospitality User"):
			self.skipTest("role Hospitality User does not exist on this site")
		_clear_marker(
			setup._GRANT_MARKER.format(doctype="Hospitality Request", role="Hospitality User", ptype="read")
		)
		_clear_marker(
			setup._GRANT_MARKER.format(doctype="Hospitality Request", role="Hospitality User", ptype="report")
		)
		setup._grant("Hospitality Request", "Hospitality User")
		setup._grant("Hospitality Request", "Hospitality User", "report")
		row = frappe.db.get_value(
			"Custom DocPerm",
			{"parent": "Hospitality Request", "role": "Hospitality User", "permlevel": 0},
			["read", "report", "write", "export", "create"],
			as_dict=True,
		)
		self.assertEqual((row.read, row.report), (1, 1))
		user = _make_user("fixd-hu", ["Hospitality User"])
		self.assertTrue(frappe.has_permission("Hospitality Request", "report", user=user))
		# Rows stay scoped: no "see everything" for this role.
		self.assertTrue(permissions.get_hospitality_request_permission_query_conditions(user))


class TestUpgradeStepsCaller(_FixDTestCase):
	def test_setup_runs_the_steps(self):
		with patch.object(upgrades, "run_all", return_value=[]) as run_all:
			setup._run_upgrade_steps()
		run_all.assert_called_once_with()

	def test_every_area_is_called_in_order_and_missing_ones_are_skipped(self):
		called = []

		def find_spec(name):
			return None if name.endswith(".gate_steps") else object()

		def import_module(name):
			called.append(name.rsplit(".", 1)[-1])
			return SimpleNamespace(run=lambda: None)

		with (
			patch("importlib.util.find_spec", side_effect=find_spec),
			patch("importlib.import_module", side_effect=import_module),
		):
			ran = upgrades.run_all()
		self.assertEqual(called, ["pass_steps", "portal_steps", "platform_steps"])
		self.assertEqual(ran, called)

	def test_a_failing_step_is_named_and_stops_the_run(self):
		called = []

		def import_module(name):
			step = name.rsplit(".", 1)[-1]
			called.append(step)
			if step == "portal_steps":
				return SimpleNamespace(run=lambda: 1 / 0)
			return SimpleNamespace(run=lambda: None)

		with (
			patch("importlib.util.find_spec", return_value=object()),
			patch("importlib.import_module", side_effect=import_module),
			patch.object(frappe, "log_error") as log_error,
		):
			with self.assertRaises(upgrades.UpgradeStepError) as ctx:
				upgrades.run_all()
		self.assertIn("portal_steps", str(ctx.exception))
		self.assertIsInstance(ctx.exception.__cause__, ZeroDivisionError)
		self.assertNotIn("platform_steps", called, "nothing runs after a failed step")
		self.assertIn("portal_steps", log_error.call_args.kwargs["title"])

	def test_the_platform_steps_module_is_one_of_them(self):
		self.assertEqual(
			upgrades.STEP_MODULES, ("pass_steps", "gate_steps", "portal_steps", "platform_steps")
		)
		self.assertTrue(callable(platform_steps.run))


# ─────────────────────────────────────────────────────────
# 6. Settings: privacy notice and consent
# ─────────────────────────────────────────────────────────
class TestConsentSettings(_FixDTestCase):
	def test_fields_exist_with_their_defaults(self):
		meta = frappe.get_meta("VMS Settings")
		self.assertEqual(meta.get_field("require_portal_consent").default, "1")
		self.assertEqual(meta.get_field("privacy_notice_version").default, "1")
		self.assertTrue(meta.has_field("privacy_notice_text"))
		self.assertEqual(meta.get_field("allow_walk_in_pre_registration").default, "0")

	def test_accessors_fall_back_to_the_standard_notice(self):
		frappe.db.set_single_value("VMS Settings", {"privacy_notice_text": "", "privacy_notice_version": ""})
		frappe.clear_document_cache("VMS Settings", "VMS Settings")
		self.assertEqual(vms_settings.privacy_notice_text(), vms_settings.DEFAULT_PRIVACY_NOTICE)
		self.assertEqual(vms_settings.privacy_notice_version(), "1")

		frappe.db.set_single_value(
			"VMS Settings",
			{"privacy_notice_text": "  Our own notice.  ", "privacy_notice_version": "2026-10"},
		)
		frappe.clear_document_cache("VMS Settings", "VMS Settings")
		self.assertEqual(vms_settings.privacy_notice_text(), "Our own notice.")
		self.assertEqual(vms_settings.privacy_notice_version(), "2026-10")

	def test_changing_the_notice_raises_a_numeric_version(self):
		settings = frappe.get_single("VMS Settings")
		settings.privacy_notice_text = f"Notice {_hash()}"
		settings.privacy_notice_version = "3"
		settings.flags.ignore_mandatory = True
		settings.save(ignore_permissions=True)
		self.assertEqual(settings.privacy_notice_version, "3", "a version the administrator set is kept")

		settings.privacy_notice_text = f"Notice changed {_hash()}"
		settings.save(ignore_permissions=True)
		self.assertEqual(settings.privacy_notice_version, "4")

		# Unchanged text: unchanged version.
		settings.save(ignore_permissions=True)
		self.assertEqual(settings.privacy_notice_version, "4")


# ─────────────────────────────────────────────────────────
# 7. Retention purge
# ─────────────────────────────────────────────────────────
class TestRetentionPurge(_FixDTestCase):
	RETENTION_DAYS = 365

	def setUp(self):
		self.marker = tasks._PURGE_MARKER_TEXT
		self._stray_paths = []

	def tearDown(self):
		super().tearDown()
		for path in self._stray_paths:
			if os.path.exists(path):
				os.remove(path)

	def _purge(self):
		"""Run the purge on this test's own passes only.

		The purge deletes files from disk, which the rollback at the end of a test
		cannot undo, so it must never reach the site's real visits.
		"""
		eligible = tasks._eligible_pass_sql

		def only_fixtures(alias, cutoff, retention_days):
			sql, params = eligible(alias, cutoff, retention_days)
			return f"({sql} and {alias}.name like %s)", [*params, "ZZFIXD-%"]

		with patch.object(tasks, "_eligible_pass_sql", side_effect=only_fixtures):
			return tasks.purge_expired_visitor_data()

	def _retention(self, enabled=1):
		frappe.db.set_single_value(
			"VMS Settings",
			{"data_retention_enabled": enabled, "data_retention_days": self.RETENTION_DAYS},
			update_modified=False,
		)
		frappe.clear_document_cache("VMS Settings", "VMS Settings")

	def _real_file(self, doctype, name, fieldname):
		"""A private file with bytes on disk, attached to a record."""
		doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"zz_fixd_{fieldname}_{_hash()}.txt",
				"content": f"fix d {_hash()}".encode(),
				"is_private": 1,
				"attached_to_doctype": doctype,
				"attached_to_name": name,
				"attached_to_field": fieldname,
			}
		).insert(ignore_permissions=True)
		self._stray_paths.append(doc.get_full_path())
		return doc

	def _old_visit(self, days_ago=400, **values):
		values.setdefault("status", "Checked-Out")
		values.setdefault("workflow_state", "Approved")
		return _make_pass(
			visit_date=add_days(nowdate(), -days_ago),
			id_proof_type="PAN Card",
			id_proof_number="AABPR2345T",
			vehicle_number="TN01AB1234",
			company__organisation="Fix D Traders",
			visitor_summary="Fix D Visitor | +919876500000",
			mobile_digits="919876500000",
			**values,
		)

	def test_purge_reaches_every_copy_of_the_visitor(self):
		self._retention(1)
		visitor_pass = self._old_visit(job_applicant_link="ZZ-FIXD-APPLICANT")
		name = visitor_pass.name
		meta = frappe.get_meta("Visitor Pass")
		optional = {
			f: v
			for f, v in {
				"id_proof_number_masked": "XXXXXX345T",
				"consent_given": 1,
				"consent_notice_version": "7",
			}.items()
			if meta.has_field(f)
		}
		if optional:
			frappe.db.set_value("Visitor Pass", name, optional, update_modified=False)

		visa = self._real_file("Visitor Pass", name, "custom_visa_copy")
		qr = self._real_file("Visitor Pass", name, "qr_code_image")
		frappe.db.set_value(
			"Visitor Pass",
			name,
			{"custom_visa_copy": visa.file_url, "qr_code_image": qr.file_url},
			update_modified=False,
		)
		visa_path, qr_path = visa.get_full_path(), qr.get_full_path()
		self.assertTrue(os.path.exists(visa_path) and os.path.exists(qr_path))

		member = _raw_insert(
			"Visitor Group Member",
			parent=name,
			parenttype="Visitor Pass",
			parentfield="group_members",
			idx=1,
			visitor_name="Second Visitor",
			mobile_number="+919876500001",
			id_proof_number="ZZZPR9999K",
			remarks="brother of the first visitor",
		)
		log = _raw_insert(
			"Security Log",
			visitor_pass=name,
			event_type="Check-In",
			visitor_name="Fix D Visitor",
			mobile_number="+919876500000",
			id_proof_number="XXXXXX345T",
			vehicle_number="TN01AB1234",
			purpose_of_visit="Interview with Mr Rao",
			verification_notes="Matches the passport photo",
		)
		declared_item = _raw_insert(
			"Visitor Item",
			parent=name,
			parenttype="Visitor Pass",
			parentfield="visitor_items",
			idx=1,
			item_name="Dell laptop",
			item_category="Electronics",
			serial_number="SN-778899",
			quantity=1,
		)
		checked_item = _raw_insert(
			"Security Item Verify",
			parent=log.name,
			parenttype="Security Log",
			parentfield="items_verification",
			idx=1,
			item_name="Dell laptop",
			serial__asset_number="SN-778899",
			security_remarks="scratched lid, owner's sticker",
			verified=1,
		)
		request = _raw_insert(
			"Hospitality Request",
			visitor_pass=name,
			visitor_name_display="Fix D Visitor",
			visitor_mobile_display="+919876500000",
			dietary_allergies="peanuts",
			special_diet="Halal",
			accessibility_requirements="Wheelchair",
			pickup_location="Airport T2",
			driver_name="A Driver",
			driver_phone="+919800000009",
			booking_reference="HTL-7781",
		)
		booking = _raw_insert(
			"Conference Room Booking",
			visitor_pass=name,
			meeting_title="Visitor Meeting — Fix D Visitor",
			special_instructions="Fix D Visitor needs a ramp",
		)
		trace = _raw_insert(
			"Contact Trace Record",
			visitor_pass=name,
			visited_area="Lobby",
			close_contacts="Asha, Ravi",
			notes="n",
		)
		version = _raw_insert(
			"Version",
			name=_hash(10),
			ref_doctype="Visitor Pass",
			docname=name,
			data=frappe.as_json(
				{
					"changed": [
						["visitor_full_name", "Fix D Vistor", "Fix D Visitor"],
						["workflow_state", "Draft", "Approved"],
					],
					"row_changed": [
						["group_members", 0, member.name, [["visitor_name", "S", "Second Visitor"]]]
					],
				},
				indent=None,
			),
		)

		def comment(comment_type, content, subject=None):
			return _raw_insert(
				"Comment",
				name=_hash(10),
				comment_type=comment_type,
				reference_doctype="Visitor Pass",
				reference_name=name,
				content=content,
				subject=subject,
			).name

		portal_note = comment("Info", "Visitor asked to meet: Mr Rao")
		attachment_note = comment("Attachment", "attached passport_fix_d_visitor.png")
		reveal_note = comment("Info", "Full ID number viewed by Someone", id_masking.REVEAL_COMMENT_SUBJECT)
		typed_comment = comment("Comment", "Called the host, all fine.")
		alert = _raw_insert(
			"Communication",
			name=_hash(10),
			communication_type="Automated Message",
			reference_doctype="Visitor Pass",
			reference_name=name,
			subject="Visit approved for Fix D Visitor",
			content="Fix D Visitor, +919876500000",
		)
		blacklist = _raw_insert(
			"Visitor Blacklist",
			reason="keep me",
			is_active=1,
			visitor_name="Fix D Visitor",
			id_proof_number="AABPR2345T",
			mobile_number="+919876500000",
		)

		self.assertGreaterEqual(self._purge(), 8)

		purged = frappe.db.get_value("Visitor Pass", name, "*", as_dict=True)
		self.assertEqual(purged.visitor_full_name, self.marker)
		for fieldname, replacement in tasks._PASS_PURGE_VALUES.items():
			if fieldname not in purged:
				continue  # a column this site does not have (yet)
			self.assertEqual(
				purged[fieldname] or None, replacement, f"Visitor Pass.{fieldname} should have been cleared"
			)
		# The visit itself, and the proof of consent, stay.
		self.assertEqual(purged.status, "Checked-Out")
		self.assertEqual(purged.id_proof_type, "PAN Card")
		if "consent_given" in optional:
			self.assertEqual(purged.consent_given, 1)
			self.assertEqual(purged.consent_notice_version, "7")

		self.assertFalse(frappe.db.exists("File", visa.name))
		self.assertFalse(frappe.db.exists("File", qr.name))
		self.assertFalse(os.path.exists(visa_path), "the visa copy's bytes are gone from disk")
		self.assertFalse(os.path.exists(qr_path), "the QR image's bytes are gone from disk")

		row = frappe.db.get_value("Visitor Group Member", member.name, "*", as_dict=True)
		self.assertEqual(row.visitor_name, self.marker)
		self.assertFalse(row.mobile_number or row.id_proof_number or row.remarks)

		gate = frappe.db.get_value("Security Log", log.name, "*", as_dict=True)
		self.assertEqual(gate.visitor_name, self.marker)
		self.assertFalse(gate.mobile_number or gate.id_proof_number or gate.vehicle_number)
		self.assertFalse(gate.purpose_of_visit or gate.verification_notes)
		self.assertEqual(gate.event_type, "Check-In")

		declared = frappe.db.get_value("Visitor Item", declared_item.name, "*", as_dict=True)
		self.assertEqual(declared.item_name, self.marker)
		self.assertFalse(declared.serial_number)
		self.assertEqual(declared.item_category, "Electronics", "what kind of item it was stays")
		checked = frappe.db.get_value("Security Item Verify", checked_item.name, "*", as_dict=True)
		self.assertEqual(checked.item_name, self.marker)
		self.assertFalse(checked.serial__asset_number or checked.security_remarks)
		self.assertEqual(checked.verified, 1, "that it was verified stays")

		hosp = frappe.db.get_value("Hospitality Request", request.name, "*", as_dict=True)
		self.assertEqual(hosp.visitor_name_display, self.marker)
		self.assertEqual((hosp.special_diet, hosp.accessibility_requirements), ("None", "None"))
		for fieldname in (
			"visitor_mobile_display",
			"dietary_allergies",
			"pickup_location",
			"driver_name",
			"driver_phone",
			"booking_reference",
		):
			self.assertFalse(hosp[fieldname], f"Hospitality Request.{fieldname} should have been cleared")

		room = frappe.db.get_value(
			"Conference Room Booking", booking.name, ["meeting_title", "special_instructions"], as_dict=True
		)
		self.assertEqual(room.meeting_title, f"Visitor Meeting — {self.marker}")
		self.assertFalse(room.special_instructions)
		self.assertFalse(frappe.db.get_value("Contact Trace Record", trace.name, "close_contacts"))
		self.assertEqual(frappe.db.get_value("Contact Trace Record", trace.name, "visited_area"), "Lobby")

		history = frappe.db.get_value("Version", version.name, "data")
		self.assertNotIn("Fix D Visitor", history)
		self.assertNotIn("Second Visitor", history)
		self.assertIn("Approved", history, "the approval history survives")

		self.assertFalse(frappe.db.exists("Comment", portal_note))
		self.assertFalse(frappe.db.exists("Comment", attachment_note))
		self.assertTrue(frappe.db.exists("Comment", reveal_note), "the record of who viewed the ID is kept")
		self.assertTrue(frappe.db.exists("Comment", typed_comment), "a person's own comment is not deleted")
		self.assertFalse(frappe.db.exists("Communication", alert.name))

		self.assertTrue(
			frappe.db.exists(
				"Visitor Event Log", {"visitor_pass": name, "event_type": "Data Retention Purge"}
			)
		)

		kept = frappe.db.get_value(
			"Visitor Blacklist",
			blacklist.name,
			["visitor_name", "id_proof_number", "mobile_number"],
			as_dict=True,
		)
		self.assertEqual(
			(kept.visitor_name, kept.id_proof_number, kept.mobile_number),
			("Fix D Visitor", "AABPR2345T", "+919876500000"),
			"the purge never touches the blacklist",
		)

		# Idempotent: nothing of this visit is left to purge.
		before = frappe.db.count("Visitor Event Log", {"visitor_pass": name})
		self._purge()
		self.assertEqual(frappe.db.count("Visitor Event Log", {"visitor_pass": name}), before)

	def test_purge_is_off_by_default_and_does_nothing_when_off(self):
		self.assertEqual(frappe.get_meta("VMS Settings").get_field("data_retention_enabled").default, "0")
		self._retention(0)
		visitor_pass = self._old_visit()
		self.assertEqual(self._purge(), 0)
		self.assertEqual(
			frappe.db.get_value("Visitor Pass", visitor_pass.name, "visitor_full_name"), "Fix D Visitor"
		)

	def test_only_finished_visits_older_than_the_period_are_purged(self):
		self._retention(1)
		recent = self._old_visit(days_ago=self.RETENTION_DAYS - 5)
		on_site = self._old_visit(status="Checked-In")
		approved = self._old_visit(status="Approved")
		pending_with_stale_flag = self._old_visit(status="Pending Approval", no_show=1)
		no_show = self._old_visit(status="Approved", no_show=1)
		rejected = self._old_visit(status="Rejected")
		# A log of a pass that is not over must not be purged through its pass's stale flag.
		open_log = _raw_insert(
			"Security Log",
			visitor_pass=pending_with_stale_flag.name,
			event_type="Alert",
			visitor_name="Keep Me",
		)

		self._purge()

		def name_of(doc):
			return frappe.db.get_value("Visitor Pass", doc.name, "visitor_full_name")

		for kept in (recent, on_site, approved, pending_with_stale_flag):
			self.assertEqual(name_of(kept), "Fix D Visitor")
		for gone in (no_show, rejected):
			self.assertEqual(name_of(gone), self.marker)
		self.assertEqual(frappe.db.get_value("Security Log", open_log.name, "visitor_name"), "Keep Me")

	def test_the_period_is_counted_from_today(self):
		self._retention(1)
		visitor_pass = self._old_visit(days_ago=100)

		def name_of():
			return frappe.db.get_value("Visitor Pass", visitor_pass.name, "visitor_full_name")

		# The purge takes "today" from tasks.nowdate (see _retention_cutoff and
		# _eligible_pass_sql); moving that is moving the purge's clock. Not
		# FrappeTestCase.freeze_time: it needs freezegun, which a production bench
		# does not have installed.
		def on_day(days_from_today):
			return patch.object(tasks, "nowdate", return_value=add_days(nowdate(), days_from_today))

		with on_day(self.RETENTION_DAYS - 101):
			self._purge()
			self.assertEqual(name_of(), "Fix D Visitor", "one day short of the retention period")
		with on_day(self.RETENTION_DAYS - 100):
			self._purge()
			self.assertEqual(name_of(), self.marker, "the visit date is now exactly the period ago")

	def test_abandoned_portal_drafts_are_purged_after_thirty_days(self):
		self._retention(1)
		draft = {"status": "Draft", "workflow_state": "Draft", "request_channel": "Portal"}
		abandoned = self._old_visit(days_ago=tasks.ABANDONED_PORTAL_DRAFT_DAYS + 1, **draft)
		still_fresh = self._old_visit(days_ago=tasks.ABANDONED_PORTAL_DRAFT_DAYS - 5, **draft)
		desk_draft = self._old_visit(
			days_ago=tasks.ABANDONED_PORTAL_DRAFT_DAYS + 1, **{**draft, "request_channel": "Desk"}
		)
		scan = self._real_file("Visitor Pass", abandoned.name, "id_proof_scan")
		frappe.db.set_value(
			"Visitor Pass", abandoned.name, "id_proof_scan", scan.file_url, update_modified=False
		)
		scan_path = scan.get_full_path()

		self._purge()

		def name_of(doc):
			return frappe.db.get_value("Visitor Pass", doc.name, "visitor_full_name")

		self.assertEqual(name_of(abandoned), self.marker)
		self.assertEqual(name_of(still_fresh), "Fix D Visitor")
		self.assertEqual(
			name_of(desk_draft), "Fix D Visitor", "only public-form drafts are treated as abandoned"
		)
		self.assertFalse(frappe.db.exists("File", scan.name))
		self.assertFalse(os.path.exists(scan_path))

	def test_invitation_purge_clears_the_link_that_holds_the_token(self):
		self._retention(1)
		token = _hash(24)
		invitation = _raw_insert(
			"Visitor Invitation",
			visitor_full_name="Fix D Invitee",
			visitor_email="invitee@example.com",
			visitor_mobile="+919876500002",
			invitation_status="Expired",
			invitation_token=token,
			portal_submission_url=f"https://example.com/visitor-pre-registration-form/new?token={token}",
			purpose_of_visit="Interview with Mr Rao",
			visit_date=add_days(nowdate(), -400),
		)
		live = _raw_insert(
			"Visitor Invitation",
			visitor_full_name="Fix D Live",
			visitor_email="live@example.com",
			invitation_status="Sent",
			invitation_token=_hash(24),
			visit_date=add_days(nowdate(), -400),
		)
		self._purge()

		row = frappe.db.get_value("Visitor Invitation", invitation.name, "*", as_dict=True)
		self.assertEqual(row.visitor_full_name, self.marker)
		for fieldname in (
			"visitor_email",
			"visitor_mobile",
			"invitation_token",
			"portal_submission_url",
		):
			self.assertFalse(row[fieldname], f"Visitor Invitation.{fieldname} should have been cleared")
		self.assertEqual(row.purpose_of_visit, self.marker)
		self.assertEqual(
			frappe.db.get_value("Visitor Invitation", live.name, "visitor_full_name"), "Fix D Live"
		)


# ─────────────────────────────────────────────────────────
# 8. Error logging and hooks
# ─────────────────────────────────────────────────────────
class TestErrorLoggingAndHooks(_FixDTestCase):
	def test_long_failure_messages_are_logged_not_raised(self):
		long_reason = "disk gone " * 40  # far beyond the 140-character Error Log title
		before = frappe.db.count("Error Log", {"method": "VMS Abandoned Uploads"})
		with patch.object(frappe, "delete_doc", side_effect=RuntimeError(long_reason)):
			self.assertEqual(tasks._delete_upload("no-such-file"), 0)
		self.assertEqual(frappe.db.count("Error Log", {"method": "VMS Abandoned Uploads"}), before + 1)

		with (
			patch.object(frappe, "sendmail", side_effect=RuntimeError(long_reason)),
			patch.object(tasks, "_get_recipients", return_value=["someone@example.com"]),
		):
			tasks._notify_overstay(
				[frappe._dict(name="VP-X", visitor_full_name="A", host_name="B", hours_in=13)], 12
			)
		self.assertTrue(frappe.db.exists("Error Log", {"method": "VMS Overstay Alert"}))

		frappe.db.set_single_value(
			"VMS Settings", {"data_retention_enabled": 1, "data_retention_days": 0}, update_modified=False
		)
		frappe.clear_document_cache("VMS Settings", "VMS Settings")
		self.assertEqual(
			tasks.purge_expired_visitor_data(), 0, "a zero period never means 'purge everything'"
		)
		self.assertTrue(frappe.db.exists("Error Log", {"method": "VMS Data Retention"}))

	def test_hooks_point_at_code_that_exists(self):
		hooks = frappe.get_hooks(app_name="visitormanagement")
		paths = list(hooks.get("auth_hooks", []))
		for group in ("has_permission", "permission_query_conditions"):
			for value in (hooks.get(group) or {}).values():
				paths.extend(value if isinstance(value, list) else [value])
		scheduler = hooks.get("scheduler_events") or {}
		for value in scheduler.values():
			if isinstance(value, dict):
				for jobs in value.values():
					paths.extend(jobs)
			else:
				paths.extend(value)
		self.assertIn(
			"visitormanagement.visitor_management.doctype.visitor_invitation.visitor_invitation.expire_due_invitations",
			paths,
		)
		for path in paths:
			with self.subTest(path=path):
				self.assertTrue(callable(frappe.get_attr(path)))

	def test_personal_data_hook_lists_only_this_apps_doctypes_and_real_fields(self):
		entries = frappe.get_hooks("user_data_fields", app_name="visitormanagement")
		self.assertTrue(entries)
		for entry in entries:
			doctype = entry["doctype"]
			self.assertNotEqual(doctype, "Visitor Blacklist")
			self.assertIn(frappe.db.get_value("DocType", doctype, "module"), link_details.APP_MODULES)
			meta = frappe.get_meta(doctype)
			self.assertTrue(meta.has_field(entry["filter_by"]))
			for fieldname in entry["redact_fields"]:
				df = meta.get_field(fieldname)
				if df:
					# Frappe writes one placeholder into every row of a person: never a unique column.
					self.assertFalse(df.unique, f"{doctype}.{fieldname} is unique")

	def test_standard_notice_literals_agree(self):
		self.assertEqual(vms_settings._default_privacy_notice(), vms_settings.DEFAULT_PRIVACY_NOTICE)
