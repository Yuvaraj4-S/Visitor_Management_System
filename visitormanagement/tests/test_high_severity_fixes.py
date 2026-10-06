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
# FrappeTestCase rolls the database back when the class finishes, so the
# test Visitor Types / Passes created here never persist.

import base64
import json

import frappe
import frappe.defaults  # clear_default, used by the back-port review tests below
from frappe.model.workflow import apply_workflow, get_transitions
from frappe.utils import nowdate

from visitormanagement.tests.site_staff import StaffedTestCase
from visitormanagement.tests.test_regression import (
	_employee_for,
	_fresh_pan,
	_make_pass,
	_user_with_role,
)

PNG_DATA_URI = (
	"data:image/png;base64,"
	"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="
)


def _home_country():
	return frappe.db.get_single_value("VMS Settings", "home_country") or "India"


def _active_host():
	return frappe.db.get_value("Employee", {"status": "Active"}, "name")


class TestNationalityDefault(StaffedTestCase):
	"""#1 + #6 — custom_nationality is mandatory; it must default to the home
	country so any creation path saves instead of failing mandatory validation."""

	def test_pass_without_nationality_defaults_to_home_country(self):
		host = _active_host()
		if not host:
			self.skipTest("no active Employee")
		vp = frappe.new_doc("Visitor Pass")
		vp.update(
			{
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
			}
		)
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
			# The visitor ticked the privacy notice (required by default from 1.1.0).
			"consent_given": 1,
			"visitor_items": [
				{"item_name": "Laptop", "quantity": 1},
				{"item_name": "Cable", "quantity": 2},
			],
		}
		# A submission without an invitation, which a site accepts only with "Allow
		# Pre-Registration Without an Invitation" on. Set for this test rather than
		# relying on what the site happens to have, and put back afterwards.
		switch = "allow_walk_in_pre_registration"
		has_switch = frappe.get_meta("VMS Settings").has_field(switch)
		previous = frappe.db.get_single_value("VMS Settings", switch) if has_switch else None
		if has_switch:
			frappe.db.set_single_value("VMS Settings", switch, 1)
			frappe.clear_document_cache("VMS Settings", "VMS Settings")
		try:
			result = portal.submit_pre_registration(payload=json.dumps(payload))
		finally:
			if has_switch:
				frappe.db.set_single_value("VMS Settings", switch, previous)
				frappe.clear_document_cache("VMS Settings", "VMS Settings")
		name = result["name"] if isinstance(result, dict) else result
		self.assertTrue(name, "portal submission returned no Visitor Pass")

		vp = frappe.get_doc("Visitor Pass", name)
		self.assertEqual(vp.custom_nationality, _home_country())
		self.assertEqual(len(vp.visitor_items), 2)
		self.assertIn("Laptop", vp.items_carried or "")


class TestBadgeColourPalette(StaffedTestCase):
	"""#2 — a Visitor Type using one of the new colours must not crash the pass."""

	def test_pass_of_blue_badge_type_saves(self):
		host = _active_host()
		if not host:
			self.skipTest("no active Employee")
		# field-level: the widen-options patch must have applied
		options = (frappe.get_meta("Visitor Pass").get_field("badge_colour").options or "").split("\n")
		self.assertIn("Blue", options)

		vt = frappe.get_doc(
			{
				"doctype": "Visitor Type",
				"visitor_type_name": "HSF Blue Type",
				"approver_role": "System Manager",
				"badge_prefix": "HBL",
				"badge_colour": "Blue",
				"is_active": 1,
			}
		)
		vt.insert(ignore_permissions=True)

		vp = _make_pass("HSF Blue Type", "HSF Blue Visitor", _fresh_pan(), host, visit_date=nowdate())
		vp.reload()
		# before_save copies the type's colour onto the pass — this used to raise
		# a ValidationError because Blue was not an allowed pass option.
		self.assertEqual(vp.badge_colour, "Blue")


class TestCustomTypeApproval(StaffedTestCase):
	"""#3 — approval must route by the Visitor Type's approver_role so a custom
	type is submittable and approvable end-to-end."""

	def test_custom_visitor_type_routes_and_approves(self):
		employee = _user_with_role("Employee")
		approver = _user_with_role("System Manager")
		if not employee or not approver:
			self.skipTest("Employee/System Manager users missing")
		host = _employee_for(employee) or _active_host()

		frappe.get_doc(
			{
				"doctype": "Visitor Type",
				"visitor_type_name": "HSF Delegate",
				"approver_role": "System Manager",
				"badge_prefix": "HDG",
				"badge_colour": "Green",
				"is_active": 1,
			}
		).insert(ignore_permissions=True)

		frappe.set_user(employee)
		try:
			vp = _make_pass("HSF Delegate", "HSF Custom Visitor", _fresh_pan(), host, visit_date=nowdate())
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


class TestDuplicateNotificationsDisabled(StaffedTestCase):
	"""#4 + #5 — the notifications that duplicate the app's code emails must stay
	disabled so the host / kitchen each get exactly one email."""

	def test_host_and_food_notifications_disabled(self):
		for name in ("VMS Host Alert", "VMS Food Dept Alert"):
			if not frappe.db.exists("Notification", name):
				self.skipTest(f"{name} not present on this site")
			self.assertEqual(
				frappe.db.get_value("Notification", name, "enabled"),
				0,
				f"{name} must be disabled (app code sends this email)",
			)


# ─── Frappe 15 back-port review fixes ────────────────────────────────────


class TestVisitorTypeChartSources(StaffedTestCase):
	"""The by-type chart sources must run on Frappe 15. They passed a v16-only dict
	field ({"COUNT": "name", "as": "count"}), which v15's DatabaseQuery rejects."""

	def test_counts_by_type_query_runs(self):
		from visitormanagement.visitor_management.dashboard_chart_source.visitor_passes_by_type.visitor_passes_by_type import (
			get_visitor_pass_counts_by_type,
		)

		labels, values = get_visitor_pass_counts_by_type()
		self.assertEqual(len(labels), len(values))
		self.assertTrue(all(isinstance(v, int) for v in values))
		pending_labels, pending_values = get_visitor_pass_counts_by_type(workflow_state_like="Pending%")
		self.assertEqual(len(pending_labels), len(pending_values))

	def test_chart_get_data_runs(self):
		from visitormanagement.visitor_management.dashboard_chart_source.pending_visitor_passes_by_type import (
			pending_visitor_passes_by_type,
		)
		from visitormanagement.visitor_management.dashboard_chart_source.visitor_passes_by_type import (
			visitor_passes_by_type,
		)

		data = visitor_passes_by_type.get_data(no_cache=1)
		self.assertIn("labels", data)
		pending = pending_visitor_passes_by_type.get_data(no_cache=1)
		self.assertTrue(pending == {} or "labels" in pending)


class TestGuestUploadsStayOff(StaffedTestCase):
	"""Frappe 15 build: the portal sends files inside the submission, so setup must
	never switch on `allow_guests_to_upload_files`, and the File guard only keeps
	anonymous uploads off this app's records."""

	def test_no_app_code_enables_guest_uploads(self):
		import ast
		import pathlib

		root = pathlib.Path(frappe.get_app_path("visitormanagement"))
		offenders = []
		for path in root.rglob("*.py"):
			if path.name == "uninstall.py" or path.name.startswith("test_") or "tests" in path.parts:
				continue  # uninstall may only switch it OFF (see _revert_portal_uploads_setting)
			tree = ast.parse(path.read_text())
			for node in ast.walk(tree):
				if (
					isinstance(node, ast.Call)
					and isinstance(node.func, ast.Attribute)
					and node.func.attr in ("set_single_value", "set_value")
					and any(
						isinstance(arg, ast.Constant) and arg.value == "allow_guests_to_upload_files"
						for arg in node.args
					)
				):
					offenders.append(f"{path.relative_to(root)}:{node.lineno}")
		self.assertEqual(offenders, [], "only uninstall may write allow_guests_to_upload_files")

	def test_setup_note_leaves_setting_unchanged(self):
		from visitormanagement import setup

		for marker in (None, "1"):
			for value in (0, 1):
				if marker:
					frappe.db.set_default(setup._PORTAL_UPLOADS_SELF_ENABLED_MARKER, marker)
				else:
					frappe.defaults.clear_default(key=setup._PORTAL_UPLOADS_SELF_ENABLED_MARKER)
				frappe.db.set_single_value("System Settings", "allow_guests_to_upload_files", value)
				setup._note_portal_uploads_setting()
				self.assertEqual(
					frappe.db.get_single_value("System Settings", "allow_guests_to_upload_files"), value
				)

	def _as_guest_request(self, doc):
		from visitormanagement.visitor_management.portal_upload import guard_guest_upload

		had_request = hasattr(frappe.local, "request")
		previous = getattr(frappe.local, "request", None)
		frappe.local.request = frappe._dict(method="POST", headers={}, files={})
		frappe.set_user("Guest")
		try:
			guard_guest_upload(doc)
		finally:
			frappe.set_user("Administrator")
			if had_request:
				frappe.local.request = previous
			else:
				del frappe.local.request

	def test_guard_refuses_guest_upload_onto_app_record(self):
		doc = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "x.png",
				"attached_to_doctype": "Visitor Pass",
				"attached_to_name": "X",
			}
		)
		with self.assertRaises(frappe.PermissionError):
			self._as_guest_request(doc)

	def test_guard_leaves_other_guest_uploads_alone(self):
		# Unattached (another app's public form) and attached to a non-VMS DocType.
		self._as_guest_request(frappe.get_doc({"doctype": "File", "file_name": "cv.pdf"}))
		self._as_guest_request(
			frappe.get_doc(
				{
					"doctype": "File",
					"file_name": "cv.pdf",
					"attached_to_doctype": "ToDo",
					"attached_to_name": "X",
				}
			)
		)

	def test_portal_refuses_a_file_url_payload(self):
		from visitormanagement.visitor_management import portal

		with self.assertRaises(frappe.PermissionError):
			portal._read_upload("/private/files/someone-elses-id.png", "visitor-id-proof.png")

	def test_portal_stores_named_data_uri_privately(self):
		from visitormanagement.visitor_management import portal

		file_url, file_name = portal._read_upload("my id.png," + PNG_DATA_URI, "visitor-id-proof.png")
		self.assertTrue(file_url.startswith("/private/files/"))
		self.assertEqual(frappe.db.get_value("File", file_name, "is_private"), 1)

	def test_portal_rejects_non_image_bytes(self):
		from visitormanagement.visitor_management import portal

		payload = "evil.png,data:image/png;base64," + base64.b64encode(b"<html>not an image</html>").decode()
		with self.assertRaises(frappe.ValidationError):
			portal._read_upload(payload, "visitor-id-proof.png")


class TestSeedSelfApprovalRepair(StaffedTestCase):
	"""Upgraded sites keep the seeded workflows; the one-time repair must switch
	self-approval off on their Approve transitions, and only once."""

	def test_repair_turns_self_approval_off_once(self):
		from visitormanagement import setup

		workflow = "Hospitality Request Approval"
		if not frappe.db.exists("Workflow", workflow):
			self.skipTest(f"{workflow} not present on this site")
		filters = {
			"parent": workflow,
			"state": "Pending Approval",
			"action": "Approve",
			"next_state": "Approved",
		}
		rows = frappe.get_all("Workflow Transition", filters=filters, pluck="name")
		if not rows:
			self.skipTest("Approve transition not present")

		for row in rows:
			frappe.db.set_value("Workflow Transition", row, "allow_self_approval", 1)
		frappe.defaults.clear_default(key=setup._SEED_SELF_APPROVAL_MARKER)

		setup._repair_seed_self_approval()
		for row in rows:
			self.assertEqual(frappe.db.get_value("Workflow Transition", row, "allow_self_approval"), 0)
		self.assertTrue(frappe.db.get_default(setup._SEED_SELF_APPROVAL_MARKER))

		# An administrator turning it back on afterwards keeps that choice.
		for row in rows:
			frappe.db.set_value("Workflow Transition", row, "allow_self_approval", 1)
		setup._repair_seed_self_approval()
		for row in rows:
			self.assertEqual(frappe.db.get_value("Workflow Transition", row, "allow_self_approval"), 1)


class TestInvitationTokenSchema(StaffedTestCase):
	"""invitation_token is unique from 1.1.0, and its collation fix must not fight
	schema sync over the column type."""

	def test_collation_alter_length_matches_schema_sync(self):
		if frappe.db.db_type != "mariadb":
			self.skipTest("MariaDB only")
		field = frappe.get_meta("Visitor Invitation").get_field("invitation_token")
		self.assertTrue(field.unique)
		expected = max(int(field.length), 64) if field.length else frappe.db.VARCHAR_LEN
		column_type = frappe.db.get_column_type("Visitor Invitation", "invitation_token")
		self.assertEqual(column_type, f"varchar({expected})")

	def test_dedupe_clears_empty_token_and_is_idempotent(self):
		from visitormanagement import setup

		name = "VMS-TEST-EMPTY-TOKEN-" + frappe.generate_hash(length=6)
		frappe.db.sql(
			"""insert into `tabVisitor Invitation` (name, creation, modified, invitation_token)
			values (%s, now(), now(), %s)""",
			(name, ""),
		)
		setup._dedupe_invitation_tokens()
		self.assertIsNone(frappe.db.get_value("Visitor Invitation", name, "invitation_token"))
		# Nothing left to fix: a second run changes nothing and does not fail.
		setup._dedupe_invitation_tokens()
		duplicated = frappe.db.sql(
			"""select invitation_token from `tabVisitor Invitation`
			where invitation_token is not null group by invitation_token having count(*) > 1"""
		)
		self.assertFalse(duplicated)
