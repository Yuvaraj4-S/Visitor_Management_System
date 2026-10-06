# See license.txt
"""Fix round, portal area: pre-registration form, invitations, rooms, uploads.

Run on its own (never without the two skip flags on a working site):

	bench --site <site> run-tests --module visitormanagement.tests.test_fix_c \\
		--skip-before-tests --skip-test-records

Everything a test needs — a host Employee, a Visitor Type, an ID Proof Type,
rooms, bookings, invitations, passes — is created inside the test transaction
and rolled back with it. Nothing here reads a record the site already had.
"""

import base64
import inspect
import io
import json
import os
import random
import string
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, add_to_date, get_datetime, get_time, now_datetime, nowdate

from visitormanagement.conference_room.doctype.conference_room_booking import (
	conference_room_booking as booking_module,
)
from visitormanagement.visitor_management import lifecycle, portal, portal_upload
from visitormanagement.visitor_management.doctype.visitor_invitation import (
	visitor_invitation as invitation_module,
)
from visitormanagement.visitor_management.upgrades import portal_steps

ID_TYPE = "FixC Site Permit"
ID_REGEX = r"^FC-[0-9]{6}$"
VISITOR_TYPE = "FixC Guest"
HOME_COUNTRY = "India"

# Settings these tests pin, with the value each test class starts from.
SETTINGS = {
	"allow_walk_in_pre_registration": 1,
	"require_portal_consent": 1,
	"privacy_notice_version": "7",
}


def _token(length=6):
	return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _permit():
	return "FC-" + "".join(random.choices(string.digits, k=6))


def _mobile():
	return "+91 98765" + "".join(random.choices(string.digits, k=5))


def _png(size=(6, 6)):
	"""A real PNG with random pixels, so no two test files share a content hash."""
	from PIL import Image

	buffer = io.BytesIO()
	Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3)).save(buffer, "PNG")
	return buffer.getvalue()


def _jpeg(size=(8, 8)):
	from PIL import Image

	buffer = io.BytesIO()
	Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3)).save(buffer, "JPEG")
	return buffer.getvalue()


def _pdf():
	return (
		b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n"
		+ os.urandom(8).hex().encode()
		+ b"\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
	)


def _data_uri(content, name="scan.png", mime="image/png"):
	return f"{name},data:{mime};base64,{base64.b64encode(content).decode()}"


def _make_employee(employee_name=None):
	"""An active Employee, written straight to the table (no Company needed)."""
	employee = frappe.new_doc("Employee")
	employee_name = employee_name or f"FixC Host {_token()}"
	employee.update(
		{
			"first_name": employee_name,
			"employee_name": employee_name,
			"status": "Active",
			"company_email": f"fixc-{_token()}@example.com",
		}
	)
	employee.name = f"FIXC-EMP-{_token().upper()}"
	employee.employee = employee.name
	employee.db_insert()
	return employee.name


def _ensure_masters():
	if not frappe.db.exists("ID Proof Type", ID_TYPE):
		frappe.get_doc(
			{
				"doctype": "ID Proof Type",
				"id_proof_type_name": ID_TYPE,
				"is_active": 1,
				"validation_method": "Regex",
				"validation_regex": ID_REGEX,
				"normalisation": "Uppercase and strip spaces",
				"error_message": "A site permit number looks like FC-123456.",
			}
		).insert(ignore_permissions=True)
	# validators.py caches the master in Redis.
	frappe.cache.delete_value("vms_id_proof_types")
	if not frappe.db.exists("Visitor Type", VISITOR_TYPE):
		frappe.get_doc(
			{
				"doctype": "Visitor Type",
				"visitor_type_name": VISITOR_TYPE,
				"approver_role": "System Manager",
				"badge_prefix": "FXC",
				"badge_colour": "Green",
				"is_active": 1,
			}
		).insert(ignore_permissions=True)


def _make_room(**overrides):
	values = {
		"doctype": "Conference Room",
		"room_name": f"FixC Room {_token()}",
		"location": "FixC Block",
		"capacity": 6,
		"available_from": "08:00:00",
		"available_to": "20:00:00",
		"min_booking_minutes": 30,
		"max_booking_hours": 8,
		"is_active": 1,
	}
	values.update(overrides)
	return frappe.get_doc(values).insert(ignore_permissions=True)


class FixCTestCase(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		# Registered before FrappeTestCase adds its rollback, so these run after
		# it: nothing cached from inside the transaction outlives it.
		cls.addClassCleanup(frappe.cache.delete_value, "vms_id_proof_types")
		cls.addClassCleanup(frappe.clear_document_cache, "VMS Settings", "VMS Settings")
		super().setUpClass()
		frappe.set_user("Administrator")
		_ensure_masters()
		cls.host = _make_employee()
		cls.host_name = frappe.db.get_value("Employee", cls.host, "employee_name")

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self._day = 0
		for fieldname, value in SETTINGS.items():
			self.set_setting(fieldname, value)
		frappe.clear_messages()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.clear_messages()
		super().tearDown()

	# ── helpers ──────────────────────────────────────────────
	def set_setting(self, fieldname, value):
		"""Set a VMS Settings value; skip the test if this site has no such field."""
		if not frappe.get_meta("VMS Settings").has_field(fieldname):
			self.skipTest(f"VMS Settings has no field {fieldname}")
		frappe.db.set_single_value("VMS Settings", fieldname, value)
		frappe.clear_document_cache("VMS Settings", "VMS Settings")

	def next_day(self):
		"""A different visit date for every pass of a test."""
		self._day += 1
		return add_days(nowdate(), self._day)

	def payload(self, **overrides):
		"""What the public form posts for a walk-in (no invitation)."""
		values = {
			"visitor_type": VISITOR_TYPE,
			"visitor_full_name": f"FixC Visitor {_token()}",
			"mobile_number": _mobile(),
			"email_id": f"fixc-{_token()}@example.com",
			"custom_nationality": HOME_COUNTRY,
			"id_proof_type": ID_TYPE,
			"id_proof_number_entry": _permit(),
			"id_proof_scan": _data_uri(_png(), "id.png"),
			"visitor_photo": _data_uri(_png(), "me.png"),
			"purpose_of_visit": "Fix round C test",
			"person_to_visit": self.host,
			"visit_date": str(self.next_day()),
			"expected_checkin": "10:00:00",
			"expected_checkout": "11:00:00",
			"submission_action": "submit",
			"consent_given": 1,
		}
		values.update(overrides)
		return values

	def submit(self, **overrides):
		return portal.submit_pre_registration(payload=json.dumps(self.payload(**overrides)))

	def pass_for(self, email):
		return frappe.db.get_value("Visitor Pass", {"email_id": email}, "name")

	def new_invitation(self, **overrides):
		values = {
			"doctype": "Visitor Invitation",
			"visitor_type": VISITOR_TYPE,
			"visitor_email": f"fixc-inv-{_token()}@example.com",
			"visitor_full_name": "FixC Invitee",
			"host_employee": self.host,
			"visit_date": str(self.next_day()),
			"expected_checkin": "10:00:00",
			"expected_checkout": "11:00:00",
			"purpose_of_visit": "Fix round C invitation",
		}
		values.update(overrides)
		invitation = frappe.get_doc(values).insert(ignore_permissions=True)
		token = f"fixc-{_token(24)}"
		invitation.db_set({"invitation_token": token, "invitation_status": "Sent"}, update_modified=False)
		invitation.reload()
		return invitation

	def message_titles(self):
		titles = []
		for message in frappe.get_message_log():
			if isinstance(message, str):
				message = json.loads(message)
			titles.append(message.get("title"))
		return titles


# ── item 1: QA DEF-8 — a room clash must not fail the pass ────────────────────
class TestRoomClashDoesNotFailThePass(FixCTestCase):
	def test_log_failure_takes_a_long_message_and_never_raises(self):
		long_text = "Conference Room Booking was not created because " + "the room is taken " * 40
		before = frappe.db.count("Error Log", {"method": "FixC long message"})
		lifecycle.log_failure("FixC long message", long_text)
		self.assertEqual(frappe.db.count("Error Log", {"method": "FixC long message"}), before + 1)

		# A title longer than the Error Log column is cut, not refused.
		lifecycle.log_failure("FixC " + "t" * 400, long_text)
		self.assertTrue(frappe.db.exists("Error Log", {"method": ("like", "FixC ttt%")}))

	def test_clashing_room_is_reported_and_the_caller_carries_on(self):
		room = _make_room()
		day = self.next_day()
		frappe.get_doc(
			{
				"doctype": "Conference Room Booking",
				"meeting_title": "FixC existing meeting with a deliberately long title",
				"conference_room": room.name,
				"meeting_type": "Internal",
				"booking_date": day,
				"start_time": "10:00:00",
				"end_time": "11:00:00",
				"booked_by": self.host,
				"expected_attendees": 2,
			}
		).insert(ignore_permissions=True)

		# What ensure_conference_room_booking reads off a Visitor Pass. The pass
		# itself is created through the portal so the booking's Link is real.
		result = self.submit(visit_date=str(day))
		visitor_pass = frappe.get_doc("Visitor Pass", result["name"])
		visitor_pass.conference_room = room.name
		frappe.clear_messages()
		errors_before = frappe.db.count("Error Log", {"method": "VMS CRB Auto-Create"})

		# Used to raise CharacterLengthExceededError from inside its own handler.
		booked = lifecycle.ensure_conference_room_booking(visitor_pass)

		self.assertIsNone(booked)
		self.assertIn("Room Not Reserved", self.message_titles())
		self.assertNotIn("Room Already Booked", self.message_titles())
		self.assertEqual(frappe.db.count("Error Log", {"method": "VMS CRB Auto-Create"}), errors_before + 1)
		self.assertFalse(frappe.db.exists("Conference Room Booking", {"visitor_pass": visitor_pass.name}))

	def test_free_room_is_booked(self):
		room = _make_room()
		result = self.submit()
		visitor_pass = frappe.get_doc("Visitor Pass", result["name"])
		visitor_pass.conference_room = room.name
		booked = lifecycle.ensure_conference_room_booking(visitor_pass)
		self.assertTrue(booked)
		self.assertEqual(frappe.db.get_value("Conference Room Booking", booked, "conference_room"), room.name)


# ── items 3, 4, 6: the public form's endpoint ─────────────────────────────────
class TestPortalConsent(FixCTestCase):
	def test_submission_without_consent_is_refused_and_stores_nothing(self):
		email = f"fixc-noconsent-{_token()}@example.com"
		files_before = frappe.db.count("File")
		with self.assertRaises(frappe.ValidationError):
			self.submit(email_id=email, consent_given=0)
		self.assertFalse(self.pass_for(email))
		self.assertEqual(frappe.db.count("File"), files_before)

	def test_consent_is_stamped_by_the_server(self):
		if not frappe.get_meta("Visitor Pass").has_field("consent_given"):
			self.skipTest("Visitor Pass has no consent fields")
		started = now_datetime()
		result = self.submit(
			# A client cannot choose when it consented or to which notice.
			consent_timestamp="2001-01-01 00:00:00",
			consent_notice_version="forged",
		)
		stored = frappe.db.get_value(
			"Visitor Pass",
			result["name"],
			["consent_given", "consent_timestamp", "consent_notice_version"],
			as_dict=True,
		)
		self.assertEqual(stored.consent_given, 1)
		self.assertEqual(stored.consent_notice_version, "7")
		self.assertGreaterEqual(get_datetime(stored.consent_timestamp), add_to_date(started, seconds=-2))

	def test_consent_not_asked_for_when_switched_off(self):
		self.set_setting("require_portal_consent", 0)
		self.assertFalse(portal.consent_required())
		result = self.submit(consent_given=0)
		self.assertTrue(result["name"])
		if frappe.get_meta("Visitor Pass").has_field("consent_given"):
			self.assertFalse(frappe.db.get_value("Visitor Pass", result["name"], "consent_given"))

	def test_notice_is_escaped_and_links_a_policy_address(self):
		if not frappe.get_meta("VMS Settings").has_field("privacy_notice_text"):
			self.skipTest("VMS Settings has no privacy_notice_text")
		self.set_setting(
			"privacy_notice_text", "Read <script>alert(1)</script> our policy at https://example.com/privacy."
		)
		html = portal.privacy_notice_html()
		self.assertNotIn("<script>", html)
		self.assertIn('href="https://example.com/privacy"', html)


class TestPortalHostIsNotAnOracle(FixCTestCase):
	def test_known_and_unknown_host_get_the_same_answer(self):
		known_email = f"fixc-known-{_token()}@example.com"
		unknown_email = f"fixc-unknown-{_token()}@example.com"

		known = self.submit(email_id=known_email, person_to_visit=self.host_name)
		unknown = self.submit(email_id=unknown_email, person_to_visit=f"Nobody Works Here {_token()}")

		# Same shape, same state, no error either way.
		self.assertEqual(set(known), set(unknown))
		self.assertEqual(
			{k: v for k, v in known.items() if k != "name"},
			{k: v for k, v in unknown.items() if k != "name"},
		)

		self.assertEqual(frappe.db.get_value("Visitor Pass", known["name"], "person_to_visit"), self.host)
		self.assertFalse(frappe.db.get_value("Visitor Pass", unknown["name"], "person_to_visit"))
		# Reception is told what the visitor typed.
		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{
					"reference_doctype": "Visitor Pass",
					"reference_name": unknown["name"],
					"comment_type": "Info",
					"content": ("like", "%Nobody Works Here%"),
				},
			)
		)

	def test_host_resolves_only_to_one_active_employee(self):
		self.assertEqual(portal._resolve_host(self.host), self.host)
		self.assertEqual(portal._resolve_host(self.host_name), self.host)
		self.assertIsNone(portal._resolve_host(""))
		self.assertIsNone(portal._resolve_host(f"No Such Person {_token()}"))

		# Two people with one name: not the form's decision to make.
		shared = f"FixC Twin {_token()}"
		_make_employee(shared)
		_make_employee(shared)
		self.assertIsNone(portal._resolve_host(shared))

		# Somebody who has left is not a host.
		left = _make_employee()
		frappe.db.set_value("Employee", left, "status", "Left", update_modified=False)
		self.assertIsNone(portal._resolve_host(left))

	def test_walk_in_refused_when_the_site_is_invitation_only(self):
		self.set_setting("allow_walk_in_pre_registration", 0)
		email = f"fixc-closed-{_token()}@example.com"
		with self.assertRaises(frappe.ValidationError):
			self.submit(email_id=email)
		self.assertFalse(self.pass_for(email))

	def test_guest_cannot_set_host_or_staff_fields(self):
		email = f"fixc-guest-{_token()}@example.com"
		frappe.set_user("Guest")
		try:
			result = self.submit(
				email_id=email,
				meal_required=1,
				refreshments_required=1,
				# Not existing records: as Links they answered "Could not find ...".
				conference_room=f"No Such Room {_token()}",
				supplier_link=f"No Such Supplier {_token()}",
				job_applicant_link=f"No Such Applicant {_token()}",
				visitor_items=[{"item_name": "<img src=x onerror=alert(1)>Laptop", "quantity": 1}],
			)
		finally:
			frappe.set_user("Administrator")

		visitor_pass = frappe.get_doc("Visitor Pass", result["name"])
		self.assertFalse(visitor_pass.meal_required)
		self.assertFalse(visitor_pass.refreshments_required)
		self.assertFalse(visitor_pass.conference_room)
		self.assertFalse(visitor_pass.supplier_link)
		self.assertFalse(visitor_pass.job_applicant_link)
		self.assertEqual([row.item_name for row in visitor_pass.visitor_items], ["Laptop"])

	def test_refusals_about_other_records_are_neutral_for_a_guest(self):
		frappe.set_user("Guest")
		try:
			for title in portal._NEUTRAL_FOR_GUEST_TITLES:
				frappe.clear_messages()
				frappe.msgprint("something about somebody else", title=title)
				with self.assertRaises(frappe.ValidationError):
					portal._neutral_refusal()
				text = json.dumps(frappe.get_message_log(), default=str)
				self.assertNotIn("something about somebody else", text)

			frappe.clear_messages()
			frappe.msgprint("Mobile Number is not valid", title="Invalid Number")
			self.assertIsNone(portal._neutral_refusal())
		finally:
			frappe.set_user("Administrator")

	def test_host_alert_mails_are_throttled_per_host(self):
		key = f"vms:portal-host-alert:{self.host}"
		frappe.cache().delete_value(key)
		try:
			for _i in range(portal.WALK_IN_HOST_ALERTS_PER_HOUR):
				doc = frappe.new_doc("Visitor Pass")
				portal._throttle_host_alerts(doc, self.host)
				self.assertIsNone(doc.flags.notifications_executed)

			doc = frappe.new_doc("Visitor Pass")
			portal._throttle_host_alerts(doc, self.host)
			# Marked as already run: Document.run_notifications skips them.
			self.assertIsInstance(doc.flags.notifications_executed, list)
		finally:
			frappe.cache().delete_value(key)


class TestPortalIdNumberIsMasked(FixCTestCase):
	def test_a_reopened_draft_shows_only_the_masked_number(self):
		if not frappe.get_meta("Visitor Pass").has_field("id_proof_number_masked"):
			self.skipTest("Visitor Pass has no masked ID field")
		invitation = self.new_invitation()
		number = _permit()

		saved = self.submit(
			invitation_token=invitation.invitation_token,
			submission_action="save",
			id_proof_number_entry=number,
		)
		self.assertEqual(saved["action"], "save")
		self.assertNotIn(number, json.dumps(saved, default=str))

		context = invitation_module.get_web_form_context(invitation.invitation_token)
		self.assertTrue(context["valid"])
		text = json.dumps(context, default=str)
		self.assertNotIn(number, text, "the full ID number went back to the visitor's browser")
		self.assertTrue(context["values"]["id_proof_number_masked"])
		self.assertTrue(context["values"]["id_proof_number_masked"].endswith(number[-4:]))
		# Whether a file is held, never where.
		self.assertIs(context["values"]["id_proof_scan_on_file"], True)
		self.assertNotIn("/private/files/", text)
		# The host is named, not identified.
		self.assertEqual(context["values"]["person_to_visit"], self.host_name)
		self.assertNotIn(self.host, text)

		# Coming back and submitting without typing the number again keeps it.
		submitted = self.submit(
			invitation_token=invitation.invitation_token,
			id_proof_number_entry="",
			id_proof_scan="",
			visitor_photo="",
		)
		self.assertEqual(submitted["name"], saved["name"])
		stored = frappe.db.get_value("Visitor Pass", saved["name"], "id_proof_number")
		self.assertEqual(stored, number)
		self.assertEqual(
			frappe.db.get_value("Visitor Invitation", invitation.name, "invitation_status"), "Submitted"
		)

	def test_owned_templates_never_print_the_full_number(self):
		"""Notification and print templates of this area use masked values only."""
		app_path = frappe.get_app_path("visitormanagement")
		offenders = []
		for folder in (
			("visitor_management", "notification"),
			("conference_room", "notification"),
			("visitor_management", "print_format"),
		):
			for root, _dirs, files in os.walk(os.path.join(app_path, *folder)):
				for filename in files:
					if not filename.endswith((".json", ".html", ".md")):
						continue
					with open(os.path.join(root, filename), encoding="utf-8") as handle:
						source = handle.read()
					if "id_proof_number" in source.replace("id_proof_number_masked", ""):
						offenders.append(filename)
		self.assertEqual(offenders, [])


# ── item 5: VAPT F3 / QA D-6 — uploads ────────────────────────────────────────
class TestPortalUploads(FixCTestCase):
	def test_polyglot_jpeg_is_refused_cleanly_with_nothing_left_behind(self):
		email = f"fixc-polyglot-{_token()}@example.com"
		# Starts like a JPEG, is not one. Used to pass the signature check and
		# then raise PIL.UnidentifiedImageError inside Frappe's File (HTTP 500).
		polyglot = b"\xff\xd8\xff\xe0" + b"<?php echo 'not an image'; ?>" * 40
		files_before = frappe.db.count("File")

		with self.assertRaises(frappe.ValidationError) as raised:
			self.submit(email_id=email, id_proof_scan=_data_uri(polyglot, "id.jpg", "image/jpeg"))

		self.assertNotIn("UnidentifiedImageError", type(raised.exception).__name__)
		self.assertFalse(self.pass_for(email))
		# The valid photo sent with it was not stored either.
		self.assertEqual(frappe.db.count("File"), files_before)

	def test_content_decides_the_file_type(self):
		self.assertEqual(portal_upload.verify_upload("me.png", _png()), "me.png")
		self.assertEqual(portal_upload.verify_upload("me.jpeg", _jpeg()), "me.jpeg")
		# A PNG a phone saved as .jpg, and a PDF named like a picture: Frappe
		# picks its image handling from the NAME, so the name follows the bytes.
		self.assertEqual(portal_upload.verify_upload("me.jpg", _png()), "me.png")
		self.assertEqual(portal_upload.verify_upload("visa.jpg", _pdf()), "visa.pdf")
		self.assertEqual(portal_upload.verify_upload("visa.pdf", _pdf()), "visa.pdf")

	def test_broken_files_are_refused(self):
		good_png = _png((40, 40))
		broken = {
			"truncated.png": good_png[: len(good_png) // 2],
			"polyglot.jpg": b"\xff\xd8\xff\xe0" + b"GIF89a not a jpeg at all " * 30,
			"jpeg-named.png": _jpeg()[:3] + b"\x00" * 300,
			"headeronly.pdf": b"%PDF-1.4\n",
			"noeof.pdf": b"%PDF-1.7\n" + b"x" * 200,
			"script.png": b"<html><script>alert(1)</script></html>",
			"photo.svg": _png(),
			"big.png": b"\x89PNG\r\n\x1a\n" + b"0" * (portal_upload.MAX_BYTES + 1),
		}
		for name, content in broken.items():
			with self.subTest(name=name), self.assertRaises(frappe.ValidationError):
				portal_upload.verify_upload(name, content)

	def test_oversized_dimensions_are_refused(self):
		from PIL import Image

		buffer = io.BytesIO()
		# 1-bit pixels: 64 megapixels in a few kilobytes of PNG.
		Image.new("1", (8000, 8000)).save(buffer, "PNG")
		self.assertLess(len(buffer.getvalue()), portal_upload.MAX_BYTES)
		with self.assertRaises(frappe.ValidationError):
			portal_upload.verify_upload("huge.png", buffer.getvalue())

	def test_files_are_stored_private_and_attached_to_the_pass(self):
		result = self.submit()
		files = frappe.get_all(
			"File",
			filters={"attached_to_doctype": "Visitor Pass", "attached_to_name": result["name"]},
			fields=["is_private", "attached_to_field", "file_url"],
		)
		self.assertEqual({f.attached_to_field for f in files}, {"id_proof_scan", "visitor_photo"})
		self.assertTrue(all(f.is_private and f.file_url.startswith("/private/files/") for f in files))

	def test_a_failure_after_the_files_were_stored_removes_them(self):
		"""QA D-6: no pass, no File row and no bytes on disk from a refused submission."""
		email = f"fixc-rollback-{_token()}@example.com"
		stored = []
		store_file = portal._store_file

		def recording(filename, content):
			file_doc = store_file(filename, content)
			stored.append((file_doc, file_doc.get_full_path()))
			return file_doc

		with (
			patch.object(portal, "_store_file", recording),
			patch.object(portal, "_attach_file_to_pass", side_effect=frappe.ValidationError("late failure")),
			self.assertRaises(frappe.ValidationError),
		):
			self.submit(email_id=email)

		self.assertEqual(len(stored), 2, "both files should have been written before the failure")
		self.assertFalse(self.pass_for(email))
		for file_doc, path in stored:
			self.assertFalse(frappe.db.exists("File", file_doc.name))
			self.assertFalse(os.path.exists(path), f"{path} was left on disk")


# ── item 7: QA DEF-5 — expired invitations ────────────────────────────────────
class TestInvitationExpiry(FixCTestCase):
	def _expire(self, invitation):
		frappe.db.set_value(
			"Visitor Invitation",
			invitation.name,
			"invitation_expires_on",
			add_days(now_datetime(), -1),
			update_modified=False,
		)

	def _status(self, invitation):
		return frappe.db.get_value("Visitor Invitation", invitation.name, "invitation_status")

	def test_opening_an_expired_link_marks_it_and_asks_for_a_commit(self):
		invitation = self.new_invitation()
		self._expire(invitation)
		frappe.local.flags.commit = False

		self.assertIsNone(invitation_module.get_valid_invitation_by_token(invitation.invitation_token))

		self.assertEqual(self._status(invitation), "Expired")
		# The link is opened with a GET, which Frappe rolls back unless told so.
		self.assertTrue(frappe.local.flags.commit)

	def test_first_open_is_recorded_and_kept(self):
		invitation = self.new_invitation()
		frappe.local.flags.commit = False
		context = invitation_module.get_web_form_context(invitation.invitation_token)
		self.assertTrue(context["valid"])
		self.assertEqual(self._status(invitation), "Opened")
		self.assertTrue(frappe.local.flags.commit)

	def test_scheduled_job_expires_invitations_nobody_opened(self):
		due = self.new_invitation()
		live = self.new_invitation()
		used = self.new_invitation()
		self._expire(due)
		self._expire(used)
		frappe.db.set_value(
			"Visitor Invitation", used.name, "invitation_status", "Submitted", update_modified=False
		)

		changed = invitation_module.expire_due_invitations()

		self.assertGreaterEqual(changed, 1)
		self.assertEqual(self._status(due), "Expired")
		self.assertEqual(self._status(live), "Sent")
		self.assertEqual(self._status(used), "Submitted")
		# Nothing left to do on a second run.
		self.assertEqual(invitation_module.expire_due_invitations(), 0)

	def test_form_shows_expired_before_the_job_has_run(self):
		invitation = self.new_invitation()
		self._expire(invitation)
		doc = frappe.get_doc("Visitor Invitation", invitation.name)
		self.assertEqual(doc.invitation_status, "Sent")
		doc.run_method("onload")
		self.assertEqual(doc.invitation_status, "Expired")

	def test_a_later_expiry_reopens_an_expired_invitation(self):
		invitation = self.new_invitation()
		self._expire(invitation)
		invitation_module.expire_due_invitations()

		doc = frappe.get_doc("Visitor Invitation", invitation.name)
		self.assertEqual(doc.invitation_status, "Expired")
		doc.invitation_expires_on = add_days(now_datetime(), 2)
		doc.save(ignore_permissions=True)

		self.assertEqual(doc.invitation_status, "Draft")
		self.assertIsNotNone(invitation_module.get_valid_invitation_by_token(invitation.invitation_token))

	def test_host_name_is_stored_for_the_list(self):
		invitation = self.new_invitation()
		if not invitation.meta.has_field("host_name"):
			self.skipTest("Visitor Invitation has no host_name")
		self.assertEqual(invitation.host_name, self.host_name)

		frappe.db.set_value("Visitor Invitation", invitation.name, "host_name", None, update_modified=False)
		portal_steps._fill_invitation_host_names()
		self.assertEqual(
			frappe.db.get_value("Visitor Invitation", invitation.name, "host_name"), self.host_name
		)

	def test_invitation_link_is_absolute_and_carries_the_token(self):
		link = invitation_module.build_invitation_link("abc123")
		self.assertTrue(link.startswith(("http://", "https://")))
		self.assertTrue(link.endswith("/visitor-pre-registration-form/new?token=abc123"))

	def test_delivery_problems_are_worded_for_the_host(self):
		raw = "Please setup default outgoing Email Account from Settings > Email Account"
		for error in (frappe.OutgoingEmailError(raw), OSError("smtp.internal.example:587 refused")):
			text = invitation_module._delivery_problem(error)
			self.assertNotIn("Email Account from", text)
			self.assertNotIn("smtp.internal.example", text)
			self.assertIn("administrator", text)


# ── later_batch 2 + VAPT F4: who is the caller ────────────────────────────────
class TestPortalRateLimitIdentity(FixCTestCase):
	def _as_request(self, peer, forwarded=None):
		"""Pretend a request arrived from `peer`, as Frappe would have set it up."""
		had_request = hasattr(frappe.local, "request")
		previous = (getattr(frappe.local, "request", None), getattr(frappe.local, "request_ip", None))
		frappe.local.request = frappe._dict(remote_addr=peer, method="POST", headers={})
		# frappe/auth.py set_request_ip: the first X-Forwarded-For entry, else the peer.
		frappe.local.request_ip = forwarded or peer

		def restore():
			frappe.local.request_ip = previous[1]
			if had_request:
				frappe.local.request = previous[0]
			else:
				del frappe.local.request

		self.addCleanup(restore)

	def _identity(self, peer, forwarded=None, trusted="unset", env=None):
		self._as_request(peer, forwarded)
		conf = frappe.local.conf
		had = "trusted_proxy_ips" in conf
		before = conf.get("trusted_proxy_ips")
		if trusted == "unset":
			conf.pop("trusted_proxy_ips", None)
		else:
			conf["trusted_proxy_ips"] = trusted
		try:
			with patch.dict(os.environ, {"FORWARDED_ALLOW_IPS": env} if env else {}, clear=False):
				if not env:
					os.environ.pop("FORWARDED_ALLOW_IPS", None)
				return portal_upload.rate_limit_identity()
		finally:
			if had:
				conf["trusted_proxy_ips"] = before
			else:
				conf.pop("trusted_proxy_ips", None)

	def test_proxied_requests_are_told_apart_by_the_forwarded_address(self):
		# bench nginx on the same host
		self.assertEqual(self._identity("127.0.0.1", "198.51.100.7"), "198.51.100.7")
		# nginx / ingress in another container (Docker, Kubernetes, Frappe Cloud):
		# used to be one bucket for every visitor of the site.
		self.assertEqual(self._identity("172.18.0.5", "198.51.100.7"), "198.51.100.7")
		self.assertEqual(self._identity("172.18.0.5", "198.51.100.8"), "198.51.100.8")
		self.assertEqual(self._identity("::ffff:10.0.0.4", "198.51.100.9"), "198.51.100.9")
		# IPv6 unique local and link-local peers are infrastructure too.
		self.assertEqual(self._identity("fd12:3456:789a::5", "198.51.100.9"), "198.51.100.9")
		self.assertEqual(self._identity("fe80::1", "198.51.100.9"), "198.51.100.9")

	def test_a_direct_caller_cannot_choose_its_identity(self):
		# A public peer is the client itself; the header is whatever it typed.
		self.assertEqual(self._identity("203.0.113.50", "198.51.100.7"), "203.0.113.50")
		self.assertEqual(self._identity("203.0.113.50", "10.9.9.9"), "203.0.113.50")
		self.assertEqual(self._identity("8.8.8.8", "198.51.100.7"), "8.8.8.8")
		# "Not globally reachable" is not "a proxy": only the private and link-local
		# ranges are believed. Teredo and carrier-grade NAT addresses are clients.
		self.assertEqual(self._identity("2001:0:53aa:64c::1", "198.51.100.7"), "2001:0:53aa:64c::1")
		self.assertEqual(self._identity("100.64.1.2", "198.51.100.7"), "100.64.1.2")

	def test_declared_proxies_are_authoritative(self):
		# An explicit list makes the limiter strict: a private peer not on it is a client.
		self.assertEqual(self._identity("172.18.0.5", "198.51.100.7", trusted=[]), "172.18.0.5")
		self.assertEqual(
			self._identity("172.18.0.5", "198.51.100.7", trusted=["172.18.0.0/16"]), "198.51.100.7"
		)
		self.assertEqual(
			self._identity("203.0.113.50", "198.51.100.7", trusted=["203.0.113.50"]), "198.51.100.7"
		)
		# Loopback is always the local proxy.
		self.assertEqual(self._identity("127.0.0.1", "198.51.100.7", trusted=[]), "198.51.100.7")

	def test_gunicorn_forwarded_allow_ips_is_honoured(self):
		self.assertEqual(self._identity("203.0.113.50", "198.51.100.7", env="*"), "198.51.100.7")
		self.assertEqual(
			self._identity("203.0.113.50", "198.51.100.7", env="127.0.0.1,203.0.113.50"), "198.51.100.7"
		)
		self.assertEqual(
			self._identity("203.0.113.51", "198.51.100.7", env="127.0.0.1,203.0.113.50"), "203.0.113.51"
		)

	def test_wrong_tokens_are_counted_and_then_every_token_is_refused(self):
		invitation = self.new_invitation()
		self._as_request("203.0.113.77")
		key = invitation_module._token_miss_key()
		frappe.cache().delete_value(key)
		self.addCleanup(frappe.cache().delete_value, key)

		# A visitor opening their own link is never counted, however often.
		for _i in range(invitation_module.TOKEN_MISSES_PER_HOUR + 5):
			self.assertIsNotNone(invitation_module.lookup_invitation(invitation.invitation_token))
		self.assertEqual(portal_upload.events_counted(key), 0)

		for _i in range(invitation_module.TOKEN_MISSES_PER_HOUR):
			self.assertIsNone(invitation_module.lookup_invitation(f"guess-{_token(12)}"))
		self.assertEqual(portal_upload.events_counted(key), invitation_module.TOKEN_MISSES_PER_HOUR)

		# Past the limit the answer is the same for a right token as for a wrong one.
		self.assertIsNone(invitation_module.lookup_invitation(invitation.invitation_token))
		# The page render calls the function without Frappe's decorators.
		context = inspect.unwrap(invitation_module.get_web_form_context)(invitation.invitation_token)
		self.assertFalse(context["valid"])

	def test_only_stored_submissions_count_against_the_limit(self):
		self._as_request("203.0.113.78")
		key = "vms:portal-submit:203.0.113.78"
		frappe.cache().delete_value(key)
		self.addCleanup(frappe.cache().delete_value, key)

		# Frappe's own decorator is stepped around on purpose: it needs a real
		# request, and this test is about the app's counter inside the function.
		submit = inspect.unwrap(portal.submit_pre_registration)

		for _i in range(5):
			with self.assertRaises(frappe.ValidationError):
				submit(payload=json.dumps(self.payload(mobile_number="")))
		self.assertEqual(portal_upload.events_counted(key), 0, "malformed requests used up the allowance")

		submit(payload=json.dumps(self.payload()))
		self.assertEqual(portal_upload.events_counted(key), 1)


# ── item 10: Conference Room hours ────────────────────────────────────────────
class TestConferenceRoomHours(FixCTestCase):
	def test_room_created_without_hours_gets_the_form_defaults(self):
		# As the REST API, Data Import or a script creates one: Frappe 15 fills
		# both Time fields with the creation time.
		for make in (
			lambda values: frappe.get_doc({"doctype": "Conference Room", **values}),
			lambda values: frappe.new_doc("Conference Room").update(values),
		):
			room = make({"room_name": f"FixC Bare Room {_token()}", "location": "FixC", "capacity": 4})
			room.insert(ignore_permissions=True)
			self.assertEqual(get_time(room.available_from), get_time("08:00:00"))
			self.assertEqual(get_time(room.available_to), get_time("20:00:00"))

	def test_hours_somebody_chose_are_kept(self):
		room = _make_room(available_from="09:30:00", available_to="17:15:00")
		self.assertEqual(get_time(room.available_from), get_time("09:30:00"))
		self.assertEqual(get_time(room.available_to), get_time("17:15:00"))

		# Clearing both on an existing room means "no restriction" and stays.
		room.available_from = None
		room.available_to = None
		room.save(ignore_permissions=True)
		self.assertFalse(room.available_from)
		self.assertFalse(room.available_to)

	def test_window_shorter_than_the_minimum_booking_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			_make_room(available_from="09:00:00", available_to="09:20:00", min_booking_minutes=30)
		# Long enough for exactly one minimum booking is fine.
		_make_room(available_from="09:00:00", available_to="09:30:00", min_booking_minutes=30)

	def test_upgrade_step_repairs_rooms_open_for_an_instant(self):
		room = _make_room()
		frappe.db.set_value(
			"Conference Room",
			room.name,
			{"available_from": "14:23:11.512345", "available_to": "14:23:11.512398"},
			update_modified=False,
		)
		portal_steps._repair_room_opening_hours()
		stored = frappe.db.get_value(
			"Conference Room", room.name, ["available_from", "available_to"], as_dict=True
		)
		self.assertEqual(get_time(stored.available_from), get_time("08:00:00"))
		self.assertEqual(get_time(stored.available_to), get_time("20:00:00"))

		# A real, if short, window is somebody's choice and is left alone.
		kept = _make_room(available_from="09:00:00", available_to="09:45:00")
		portal_steps._repair_room_opening_hours()
		self.assertEqual(
			get_time(frappe.db.get_value("Conference Room", kept.name, "available_to")), get_time("09:45:00")
		)


# ── item 9: QA D-5 — a rejected booking holds no slot, anywhere ───────────────
class TestRejectedBookingFreesTheSlot(FixCTestCase):
	def test_calendar_and_clash_check_agree(self):
		room = _make_room()
		day = self.next_day()
		booking = frappe.get_doc(
			{
				"doctype": "Conference Room Booking",
				"meeting_title": "FixC to be rejected",
				"conference_room": room.name,
				"meeting_type": "Internal",
				"booking_date": day,
				"start_time": "14:00:00",
				"end_time": "15:00:00",
				"booked_by": self.host,
				"expected_attendees": 2,
			}
		).insert(ignore_permissions=True)

		def on_calendar():
			events = booking_module.get_booking_events(
				str(day), str(day), json.dumps({"conference_room": room.name})
			)
			return [event["name"] for event in events]

		def clash():
			return booking_module.find_conflicting_booking(room.name, day, "14:00:00", "15:00:00")

		def free_rooms():
			return [r.name for r in booking_module.get_available_rooms(str(day), "14:00:00", "15:00:00")]

		self.assertIn(booking.name, on_calendar())
		self.assertTrue(clash())
		self.assertNotIn(room.name, free_rooms())

		for status in booking_module.NON_BLOCKING_STATUSES:
			frappe.db.set_value(
				"Conference Room Booking", booking.name, "status", status, update_modified=False
			)
			with self.subTest(status=status):
				self.assertNotIn(booking.name, on_calendar())
				self.assertFalse(clash())
				self.assertIn(room.name, free_rooms())
