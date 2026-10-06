# See license.txt
"""Fix round, Visitor Pass area: ID number masking, consent, failure logging.

Run on its own (never without the two skip flags on a working site):

	bench --site <site> run-tests --module visitormanagement.tests.test_fix_a \\
		--skip-before-tests --skip-test-records

Everything a test needs — users, a host Employee, a Visitor Type, an ID Proof
Type, passes — is created inside the test transaction and rolled back with it.
Nothing here reads a record the site already had.
"""

import json
import random
import string
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, nowdate

from visitormanagement.visitor_management import id_masking
from visitormanagement.visitor_management.doctype.visitor_pass import visitor_pass as vp_module
from visitormanagement.visitor_management.upgrades import pass_steps

# An ID type of our own, so the format rule and the normalisation are known:
# "FX-" and six digits, stored upper-case with spaces removed.
ID_TYPE = "FixA Site Permit"
ID_REGEX = r"^FX-[0-9]{6}$"
VISITOR_TYPE = "FixA Delegate"
APPROVER_ROLE = "System Manager"


def _token(length=6):
	return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def _permit():
	"""A fresh, valid number of the test ID type."""
	return "FX-" + "".join(random.choices(string.digits, k=6))


def _ensure_role(role):
	if not frappe.db.exists("Role", role):
		frappe.get_doc({"doctype": "Role", "role_name": role}).insert(ignore_permissions=True)


def _make_user(tag, *roles):
	email = f"fixa-{tag}-{_token()}@example.com"
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": f"FixA {tag}",
			"send_welcome_email": 0,
			"enabled": 1,
		}
	)
	user.insert(ignore_permissions=True)
	for role in roles:
		_ensure_role(role)
		# Written directly, not with `user.add_roles`: other apps' User hooks drop
		# roles on save. ERPNext's validate_employee_role removes "Employee" from a
		# user who has no Employee record yet — and the host's Employee row is
		# created after the user, the stranger never gets one.
		row = frappe.get_doc(
			{
				"doctype": "Has Role",
				"parent": email,
				"parenttype": "User",
				"parentfield": "roles",
				"role": role,
			}
		)
		row.name = frappe.generate_hash(length=10)
		row.db_insert()
	frappe.clear_cache(user=email)
	missing = set(roles) - set(frappe.get_roles(email))
	if missing:
		raise AssertionError(f"test user {email} does not hold {sorted(missing)}")
	return email


def _make_employee(user):
	"""An active Employee linked to `user`, written straight to the table.

	The pass only needs the row to exist, be Active and name its user. Going
	through Employee's own validation would need a Company, a holiday list and
	more of ERPNext than these tests have any business creating.
	"""
	employee = frappe.new_doc("Employee")
	employee.update(
		{
			"first_name": "FixA",
			"last_name": "Host",
			"employee_name": "FixA Host",
			"status": "Active",
			"user_id": user,
		}
	)
	employee.name = f"FIXA-EMP-{_token().upper()}"
	employee.employee = employee.name
	employee.db_insert()
	return employee.name


def _ensure_id_type():
	if not frappe.db.exists("ID Proof Type", ID_TYPE):
		frappe.get_doc(
			{
				"doctype": "ID Proof Type",
				"id_proof_type_name": ID_TYPE,
				"is_active": 1,
				"validation_method": "Regex",
				"validation_regex": ID_REGEX,
				"normalisation": "Uppercase and strip spaces",
				"error_message": "A site permit number looks like FX-123456.",
			}
		).insert(ignore_permissions=True)
	# validators.py caches the master in Redis.
	frappe.cache.delete_value("vms_id_proof_types")
	return ID_TYPE


def _ensure_visitor_type():
	if not frappe.db.exists("Visitor Type", VISITOR_TYPE):
		frappe.get_doc(
			{
				"doctype": "Visitor Type",
				"visitor_type_name": VISITOR_TYPE,
				"approver_role": APPROVER_ROLE,
				"badge_prefix": "FXA",
				"badge_colour": "Green",
				"is_active": 1,
			}
		).insert(ignore_permissions=True)
	return VISITOR_TYPE


class FixATestCase(FrappeTestCase):
	"""Shared fixtures: a host (an ordinary employee), a stranger, a System Manager."""

	@classmethod
	def setUpClass(cls):
		# Registered before FrappeTestCase adds its rollback, so it runs after it:
		# the ID type created below is gone from the database by then, and must
		# not linger in the cached copy of the master.
		cls.addClassCleanup(frappe.cache.delete_value, "vms_id_proof_types")
		super().setUpClass()
		frappe.set_user("Administrator")
		_ensure_id_type()
		_ensure_visitor_type()
		cls.host_user = _make_user("host", "Employee")
		cls.host = _make_employee(cls.host_user)
		cls.stranger = _make_user("stranger", "Employee")
		cls.system_manager = _make_user("sysmgr", "System Manager")

	def setUp(self):
		super().setUp()
		frappe.set_user("Administrator")
		self._day = 0

	def tearDown(self):
		frappe.set_user("Administrator")
		super().tearDown()

	# ── helpers ──────────────────────────────────────────────
	def new_pass(self, **overrides):
		"""An unsaved pass with everything but the ID number filled in."""
		self._day += 1
		values = {
			"doctype": "Visitor Pass",
			"visitor_type": VISITOR_TYPE,
			"visitor_full_name": f"FixA Visitor {_token()}",
			"mobile_number": "+91-98765" + "".join(random.choices(string.digits, k=5)),
			"email_id": f"fixa-{_token()}@example.com",
			"id_proof_type": ID_TYPE,
			"person_to_visit": self.host,
			# A different day for every pass of a test, so only a test that means
			# to can trip the duplicate same-day rule.
			"visit_date": add_days(nowdate(), self._day),
			"expected_checkin": "10:00:00",
			"expected_checkout": "11:00:00",
			"purpose_of_visit": "Fix round A test",
		}
		values.update(overrides)
		return frappe.get_doc(values)

	def insert_as(self, user, **overrides):
		frappe.set_user(user)
		try:
			return self.new_pass(**overrides).insert()
		finally:
			frappe.set_user("Administrator")

	def stored(self, name):
		"""The three ID fields as they are in the database."""
		return frappe.db.get_value(
			"Visitor Pass",
			name,
			["id_proof_number", "id_proof_number_masked", "id_proof_number_entry"],
			as_dict=True,
		)

	def stored_members(self, name):
		return frappe.get_all(
			"Visitor Group Member",
			filters={"parent": name, "parenttype": "Visitor Pass"},
			fields=[
				"name",
				"visitor_name",
				"id_proof_number",
				"id_proof_number_masked",
				"id_proof_number_entry",
			],
			order_by="idx asc",
		)

	def assertMasks(self, full, masked):
		self.assertTrue(masked, "no masked number")
		self.assertEqual(masked, id_masking.mask_id(ID_TYPE, full))
		self.assertNotEqual(masked, full)
		self.assertNotIn(full, masked)

	def assertNoFullNumber(self, full, payload, where):
		self.assertNotIn(full, frappe.as_json(payload), f"full ID number leaked through {where}")


# ─── 1. Typing a number, and what is kept ────────────────────────────────
class TestIdNumberIsStoredFullAndShownMasked(FixATestCase):
	def test_host_types_a_number_and_saves(self):
		"""A host types the number: the database keeps it in full, the form gets the mask."""
		full = _permit()
		# Typed the way people type: lower case, a stray space.
		vp = self.insert_as(self.host_user, id_proof_number_entry=f" {full.lower()} ")

		row = self.stored(vp.name)
		self.assertEqual(row.id_proof_number, full, "the number is stored in full, normalised")
		self.assertMasks(full, row.id_proof_number_masked)
		self.assertFalse(row.id_proof_number_entry, "the typed number must not stay in the entry field")

		# What goes back to the browser after the save.
		sent = vp.as_dict()
		self.assertNotIn("id_proof_number", sent)
		self.assertNotIn("id_proof_number_entry", sent)
		self.assertEqual(sent.get("id_proof_number_masked"), row.id_proof_number_masked)
		self.assertNoFullNumber(full, sent, "the saved document")
		# Server code still has it.
		self.assertEqual(vp.id_proof_number, full)

	def test_number_sent_as_id_proof_number_on_create_is_taken_as_typed(self):
		"""An integration that still posts `id_proof_number` when creating a pass keeps working."""
		full = _permit()
		vp = self.insert_as(self.host_user, id_proof_number=full.lower())
		row = self.stored(vp.name)
		self.assertEqual(row.id_proof_number, full)
		self.assertMasks(full, row.id_proof_number_masked)

	def test_invalid_number_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			self.insert_as(self.host_user, id_proof_number_entry="NOT-A-PERMIT")

	def test_a_pass_needs_a_number(self):
		with self.assertRaises(frappe.MandatoryError):
			self.insert_as(self.host_user)

	def test_draft_saved_by_the_portal_may_wait_for_the_number(self):
		"""The portal's Save Draft sets ignore_mandatory; it skipped `reqd` and skips this."""
		vp = self.new_pass()
		vp.flags.ignore_mandatory = True
		vp.insert(ignore_permissions=True)
		row = self.stored(vp.name)
		self.assertFalse(row.id_proof_number)
		self.assertFalse(row.id_proof_number_masked)

	def test_editing_other_fields_later_leaves_the_number_alone(self):
		"""Open the pass, change something else, save — as the browser does it.

		The document the browser sends back has no `id_proof_number` at all. For
		an ordinary user Frappe restores the stored value; for Administrator it
		does not, so the controller has to. Both must end with the same number.
		"""
		for user in (self.host_user, "Administrator"):
			with self.subTest(user=user):
				full = _permit()
				vp = self.insert_as(self.host_user, id_proof_number_entry=full)

				frappe.set_user(user)
				loaded = frappe.client.get("Visitor Pass", vp.name)
				self.assertNotIn("id_proof_number", loaded)
				loaded["vehicle_number"] = "TN01AB1234"
				answer = frappe.client.save(loaded)
				frappe.set_user("Administrator")

				self.assertNoFullNumber(full, answer, "frappe.client.save")
				row = self.stored(vp.name)
				self.assertEqual(row.id_proof_number, full, "the stored number must be untouched")
				self.assertMasks(full, row.id_proof_number_masked)
				self.assertEqual(frappe.db.get_value("Visitor Pass", vp.name, "vehicle_number"), "TN01AB1234")

	def test_typing_a_new_number_replaces_the_old_one(self):
		first, second = _permit(), _permit()
		vp = self.insert_as(self.host_user, id_proof_number_entry=first)

		frappe.set_user(self.host_user)
		loaded = frappe.client.get("Visitor Pass", vp.name)
		loaded["id_proof_number_entry"] = second
		answer = frappe.client.save(loaded)
		frappe.set_user("Administrator")

		self.assertNoFullNumber(second, answer, "frappe.client.save")
		row = self.stored(vp.name)
		self.assertEqual(row.id_proof_number, second)
		self.assertMasks(second, row.id_proof_number_masked)
		self.assertFalse(row.id_proof_number_entry)

	def test_masked_value_sent_back_is_not_taken_for_a_number(self):
		"""A page that echoes the masked number into the input must not overwrite the real one."""
		full = _permit()
		vp = self.insert_as(self.host_user, id_proof_number_entry=full)
		masked = self.stored(vp.name).id_proof_number_masked

		doc = frappe.get_doc("Visitor Pass", vp.name)
		doc.id_proof_number_entry = masked
		doc.save(ignore_permissions=True)
		self.assertEqual(self.stored(vp.name).id_proof_number, full)

	def test_number_cannot_be_changed_once_the_pass_is_approved(self):
		full = _permit()
		vp = self.insert_as(self.host_user, id_proof_number_entry=full)
		# Approved, as the workflow leaves it.
		frappe.db.set_value(
			"Visitor Pass",
			vp.name,
			{"docstatus": 1, "workflow_state": "Approved", "status": "Approved"},
			update_modified=False,
		)

		doc = frappe.get_doc("Visitor Pass", vp.name)
		doc.id_proof_number_entry = _permit()
		with self.assertRaises(frappe.ValidationError):
			doc.save(ignore_permissions=True)
		self.assertEqual(self.stored(vp.name).id_proof_number, full)

		# Frappe saves an approved pass without validate(), and does not compare the
		# two private fields itself. Neither a caller permlevel does not apply to
		# (Administrator) nor the same number typed again may leave a trace.
		doc = frappe.get_doc("Visitor Pass", vp.name)
		doc.id_proof_number = _permit()
		doc.id_proof_number_entry = full
		doc.id_proof_number_masked = "XXXX-0000"  # read-only on the form, not on the server
		doc.save(ignore_permissions=True)
		row = self.stored(vp.name)
		self.assertEqual(row.id_proof_number, full, "the stored number changed on an approved pass")
		self.assertFalse(row.id_proof_number_entry, "the typed number stayed in the entry field")
		self.assertMasks(full, row.id_proof_number_masked)


# ─── 2. Nobody without the right reads the full number back ──────────────
class TestFullNumberNeverReachesAReader(FixATestCase):
	def setUp(self):
		super().setUp()
		self.full = _permit()
		self.vp = self.insert_as(self.host_user, id_proof_number_entry=self.full)
		self.masked = self.stored(self.vp.name).id_proof_number_masked

	def test_no_role_holds_the_permlevel_of_the_stored_field(self):
		meta = frappe.get_meta("Visitor Pass")
		self.assertEqual(meta.get_field("id_proof_number").permlevel, 1)
		self.assertEqual(meta.get_field("id_proof_number_masked").permlevel, 0)
		self.assertEqual(meta.get_field("id_proof_number_entry").permlevel, 0)
		row_meta = frappe.get_meta("Visitor Group Member")
		self.assertEqual(row_meta.get_field("id_proof_number").permlevel, 1)

		for user in (self.host_user, self.system_manager):
			frappe.set_user(user)
			doc = frappe.get_doc("Visitor Pass", self.vp.name)
			self.assertFalse(
				doc.has_permlevel_access_to("id_proof_number"),
				f"{user} must not hold read access to the stored ID number",
			)
		frappe.set_user("Administrator")

	def test_client_get(self):
		frappe.set_user(self.host_user)
		loaded = frappe.client.get("Visitor Pass", self.vp.name)
		self.assertNotIn("id_proof_number", loaded)
		self.assertEqual(loaded.get("id_proof_number_masked"), self.masked)
		self.assertNoFullNumber(self.full, loaded, "frappe.client.get")

	def test_client_get_list(self):
		frappe.set_user(self.host_user)
		rows = frappe.client.get_list(
			"Visitor Pass",
			fields=["name", "id_proof_number", "id_proof_number_masked", "id_proof_number_entry"],
			filters={"name": self.vp.name},
		)
		self.assertEqual(len(rows), 1)
		self.assertNotIn("id_proof_number", rows[0])
		self.assertEqual(rows[0].get("id_proof_number_masked"), self.masked)
		self.assertNoFullNumber(self.full, rows, "frappe.client.get_list")

		everything = frappe.client.get_list("Visitor Pass", fields=["*"], filters={"name": self.vp.name})
		self.assertNoFullNumber(self.full, everything, "frappe.client.get_list with fields=*")

	def test_client_get_value(self):
		frappe.set_user(self.host_user)
		both = frappe.client.get_value(
			"Visitor Pass", ["id_proof_number", "id_proof_number_masked"], self.vp.name
		)
		self.assertNoFullNumber(self.full, both, "frappe.client.get_value")
		self.assertEqual(both.get("id_proof_number_masked"), self.masked)

		# Asking for the stored field alone may be refused outright; it must
		# never answer with the number.
		try:
			alone = frappe.client.get_value("Visitor Pass", "id_proof_number", self.vp.name)
		except Exception:
			alone = None
		self.assertNoFullNumber(self.full, alone, "frappe.client.get_value(id_proof_number)")

	def test_report_view(self):
		from frappe.desk import reportview

		frappe.set_user(self.host_user)
		before = frappe.local.form_dict
		frappe.local.form_dict = frappe._dict(
			{
				"doctype": "Visitor Pass",
				"fields": json.dumps(
					[
						"`tabVisitor Pass`.`name`",
						"`tabVisitor Pass`.`id_proof_number`",
						"`tabVisitor Pass`.`id_proof_number_masked`",
					]
				),
				"filters": json.dumps([["Visitor Pass", "name", "=", self.vp.name]]),
				"start": 0,
				"page_length": 20,
				"view": "Report",
			}
		)
		try:
			result = reportview.get()
		finally:
			frappe.local.form_dict = before
		self.assertNoFullNumber(self.full, result, "frappe.desk.reportview.get")
		self.assertIn(self.masked, frappe.as_json(result))

	def test_form_load(self):
		from frappe.desk.form import load

		frappe.set_user(self.host_user)
		before = frappe.local.response
		frappe.local.response = frappe._dict({"docs": []})
		try:
			load.getdoc("Visitor Pass", self.vp.name)
			sent = frappe.as_json(frappe.local.response)
		finally:
			frappe.local.response = before
		self.assertNotIn(self.full, sent, "full ID number leaked through the form load")
		self.assertIn(self.masked, sent)

	def test_answers_to_a_write(self):
		"""frappe.client.set_value answers with the whole document, unfiltered by Frappe."""
		frappe.set_user(self.host_user)
		answer = frappe.client.set_value("Visitor Pass", self.vp.name, "vehicle_number", "KA05MN4321")
		self.assertNotIn("id_proof_number", answer)
		self.assertNoFullNumber(self.full, answer, "frappe.client.set_value")
		frappe.set_user("Administrator")
		self.assertEqual(self.stored(self.vp.name).id_proof_number, self.full)

	def test_administrator_is_sent_the_mask_too(self):
		"""Even Administrator reads the number through the recorded reveal, not the form."""
		loaded = frappe.client.get("Visitor Pass", self.vp.name)
		self.assertNotIn("id_proof_number", loaded)
		self.assertNoFullNumber(self.full, loaded, "frappe.client.get as Administrator")

	def test_a_stranger_cannot_open_the_pass_at_all(self):
		frappe.set_user(self.stranger)
		with self.assertRaises(frappe.PermissionError):
			frappe.client.get("Visitor Pass", self.vp.name)

	def test_system_manager_reveal_returns_the_number_and_is_recorded(self):
		frappe.set_user(self.system_manager)
		before = frappe.db.count(
			"Activity Log",
			{
				"reference_doctype": "Visitor Pass",
				"reference_name": self.vp.name,
				"user": self.system_manager,
			},
		)
		self.assertEqual(id_masking.reveal_id("Visitor Pass", self.vp.name), self.full)
		frappe.set_user("Administrator")
		after = frappe.db.count(
			"Activity Log",
			{
				"reference_doctype": "Visitor Pass",
				"reference_name": self.vp.name,
				"user": self.system_manager,
			},
		)
		self.assertEqual(after, before + 1, "every reveal must leave a record")

	def test_reveal_is_refused_for_anyone_else(self):
		frappe.set_user(self.host_user)
		with self.assertRaises(frappe.PermissionError):
			id_masking.reveal_id("Visitor Pass", self.vp.name)

	def test_change_history_holds_only_masked_numbers(self):
		"""The form sends Version rows to the browser as they are."""
		second = _permit()
		doc = frappe.get_doc("Visitor Pass", self.vp.name)
		doc.id_proof_number_entry = second
		# Tests switch change tracking off by default; this one is about it.
		doc.save(ignore_permissions=True, ignore_version=False)

		versions = frappe.get_all(
			"Version",
			filters={"ref_doctype": "Visitor Pass", "docname": self.vp.name},
			pluck="data",
		)
		self.assertTrue(versions, "the change was not recorded at all")
		history = "\n".join(versions)
		# The change itself is on record (who changed the number, and when) ...
		self.assertIn('"id_proof_number"', history)
		# ... but neither number is.
		self.assertNotIn(self.full, history)
		self.assertNotIn(second, history)


# ─── 3. Group members ────────────────────────────────────────────────────
class TestGroupMembers(FixATestCase):
	def test_member_numbers_are_stored_full_and_shown_masked(self):
		lead, member = _permit(), _permit()
		vp = self.insert_as(
			self.host_user,
			id_proof_number_entry=lead,
			is_group_visit=1,
			group_members=[
				{
					"visitor_name": "Member One",
					"id_proof_type": ID_TYPE,
					"id_proof_number_entry": member.lower(),
				},
				{"visitor_name": "Member Two"},
			],
		)
		rows = self.stored_members(vp.name)
		self.assertEqual(len(rows), 2)
		self.assertEqual(rows[0].id_proof_number, member)
		self.assertMasks(member, rows[0].id_proof_number_masked)
		self.assertFalse(rows[0].id_proof_number_entry)
		self.assertFalse(rows[1].id_proof_number)
		self.assertEqual(frappe.db.get_value("Visitor Pass", vp.name, "group_size"), 3)

		sent = vp.as_dict()
		self.assertNotIn("id_proof_number", sent["group_members"][0])
		self.assertNoFullNumber(member, sent, "the saved document")

	def test_member_numbers_survive_a_round_trip_through_the_browser(self):
		lead, member, late = _permit(), _permit(), _permit()
		vp = self.insert_as(
			self.host_user,
			id_proof_number_entry=lead,
			is_group_visit=1,
			group_members=[
				{"visitor_name": "Member One", "id_proof_type": ID_TYPE, "id_proof_number_entry": member},
			],
		)

		for user in (self.host_user, "Administrator"):
			with self.subTest(user=user):
				frappe.set_user(user)
				loaded = frappe.client.get("Visitor Pass", vp.name)
				self.assertNotIn("id_proof_number", loaded["group_members"][0])
				self.assertNoFullNumber(member, loaded, "frappe.client.get")
				loaded["group_members"][0]["remarks"] = f"edited by {user}"
				if user == self.host_user:
					loaded["group_members"].append(
						{
							"doctype": "Visitor Group Member",
							"visitor_name": "Late Joiner",
							"id_proof_type": ID_TYPE,
							"id_proof_number_entry": late,
						}
					)
				answer = frappe.client.save(loaded)
				frappe.set_user("Administrator")

				self.assertNoFullNumber(member, answer, "frappe.client.save")
				self.assertNoFullNumber(late, answer, "frappe.client.save")
				rows = self.stored_members(vp.name)
				self.assertEqual(rows[0].id_proof_number, member, "an untouched member keeps their number")
				self.assertMasks(member, rows[0].id_proof_number_masked)

		rows = self.stored_members(vp.name)
		self.assertEqual(len(rows), 2)
		self.assertEqual(rows[1].id_proof_number, late)
		self.assertMasks(late, rows[1].id_proof_number_masked)
		self.assertEqual(self.stored(vp.name).id_proof_number, lead)

	def test_invalid_member_number_is_refused_with_its_row(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			self.insert_as(
				self.host_user,
				id_proof_number_entry=_permit(),
				is_group_visit=1,
				group_members=[
					{
						"visitor_name": "Member One",
						"id_proof_type": ID_TYPE,
						"id_proof_number_entry": "12345",
					},
				],
			)
		self.assertIn("Row 1", str(caught.exception))

	def test_member_reveal_is_for_a_system_manager_and_recorded(self):
		member = _permit()
		vp = self.insert_as(
			self.host_user,
			id_proof_number_entry=_permit(),
			is_group_visit=1,
			group_members=[
				{"visitor_name": "Member One", "id_proof_type": ID_TYPE, "id_proof_number_entry": member},
			],
		)
		row_name = self.stored_members(vp.name)[0].name

		frappe.set_user(self.host_user)
		with self.assertRaises(frappe.PermissionError):
			id_masking.reveal_id("Visitor Pass", vp.name, row_name=row_name)

		frappe.set_user(self.system_manager)
		self.assertEqual(id_masking.reveal_id("Visitor Pass", vp.name, row_name=row_name), member)
		frappe.set_user("Administrator")
		self.assertTrue(
			frappe.db.exists(
				"Activity Log",
				{"reference_doctype": "Visitor Pass", "reference_name": vp.name, "user": self.system_manager},
			)
		)

	def test_member_number_change_is_masked_in_the_change_history(self):
		first, second = _permit(), _permit()
		vp = self.insert_as(
			self.host_user,
			id_proof_number_entry=_permit(),
			is_group_visit=1,
			group_members=[
				{"visitor_name": "Member One", "id_proof_type": ID_TYPE, "id_proof_number_entry": first},
			],
		)
		doc = frappe.get_doc("Visitor Pass", vp.name)
		doc.group_members[0].id_proof_number_entry = second
		doc.append(
			"group_members",
			{"visitor_name": "Member Two", "id_proof_type": ID_TYPE, "id_proof_number_entry": _permit()},
		)
		added = doc.group_members[1].id_proof_number_entry
		doc.save(ignore_permissions=True, ignore_version=False)

		history = "\n".join(
			frappe.get_all(
				"Version", filters={"ref_doctype": "Visitor Pass", "docname": vp.name}, pluck="data"
			)
		)
		self.assertTrue(history, "the change was not recorded at all")
		for number in (first, second, added):
			self.assertNotIn(number, history)


# ─── 4. The checks that need the full number still work ──────────────────
class TestMatchingStillUsesTheFullNumber(FixATestCase):
	def test_same_id_on_the_same_day_is_still_refused(self):
		full = _permit()
		day = add_days(nowdate(), 3)
		first = self.insert_as(self.host_user, id_proof_number_entry=full, visit_date=day)

		with self.assertRaises(frappe.ValidationError) as caught:
			# Typed differently; it is the stored, normalised number that is compared.
			self.insert_as(self.host_user, id_proof_number_entry=f" {full.lower()}", visit_date=day)
		self.assertIn(first.name, str(caught.exception))

		# Another day is another visit.
		other_day = self.insert_as(self.host_user, id_proof_number_entry=full, visit_date=add_days(day, 1))
		self.assertEqual(self.stored(other_day.name).id_proof_number, full)

	def test_blacklisted_id_is_still_refused_and_the_alert_is_masked(self):
		from visitormanagement.visitor_management.workflow_builder import lane_for_role

		full = _permit()
		entry = frappe.get_doc(
			{
				"doctype": "Visitor Blacklist",
				"visitor_name": "FixA Barred Person",
				"id_proof_type": ID_TYPE,
				"id_proof_number": full,
				"is_active": 1,
				"reason": "Fix round A test",
			}
		).insert(ignore_permissions=True)
		# Whatever the blacklist form does with a typed number, this is what the
		# gate matches against.
		frappe.db.set_value(
			"Visitor Blacklist", entry.name, {"id_proof_number": full, "is_active": 1}, update_modified=False
		)

		# A draft may still be saved (reception is warned, not stopped) ...
		draft = self.insert_as(self.host_user, id_proof_number_entry=full)
		self.assertEqual(self.stored(draft.name).id_proof_number, full)

		# ... but the pass cannot be sent for approval, and security is told.
		doc = frappe.get_doc("Visitor Pass", draft.name)
		doc.workflow_state = lane_for_role(APPROVER_ROLE)
		doc.visitor_photo = "/private/files/fixa-photo.png"
		doc.id_proof_scan = "/private/files/fixa-id.png"
		with (
			patch.object(vp_module, "send_in_background") as send,
			patch.object(
				vp_module.VisitorPass, "_security_alert_recipients", return_value=["security@example.com"]
			),
		):
			with self.assertRaises(frappe.ValidationError):
				doc.save(ignore_permissions=True)

		self.assertTrue(send.called, "the security alert was not sent")
		message = send.call_args.kwargs.get("message") or ""
		self.assertNotIn(full, message, "the alert email carries the full ID number")
		self.assertIn(id_masking.mask_id(ID_TYPE, full), message)

	def test_returning_visitor_is_found_by_the_typed_number_and_shown_masked(self):
		full = _permit()
		earlier = self.insert_as(self.host_user, id_proof_number_entry=full)

		frappe.set_user(self.host_user)
		found = vp_module.get_existing_visitor_matches(
			visitor_type=VISITOR_TYPE, id_proof_number=full.lower(), id_proof_type=ID_TYPE
		)
		self.assertEqual(found["best_match"]["name"], earlier.name)
		self.assertNotIn("id_proof_number", found["best_match"])
		self.assertMasks(full, found["best_match"]["id_proof_number_masked"])
		self.assertNoFullNumber(full, found, "get_existing_visitor_matches")

		details = vp_module.get_existing_visitor_pass_details(earlier.name, VISITOR_TYPE)
		self.assertNotIn("id_proof_number", details)
		self.assertMasks(full, details["id_proof_number_masked"])
		self.assertNoFullNumber(full, details, "get_existing_visitor_pass_details")

	def test_loading_an_existing_visitor_copies_the_number_on_the_server(self):
		full = _permit()
		earlier = self.insert_as(self.host_user, id_proof_number_entry=full)
		masked = self.stored(earlier.name).id_proof_number_masked

		# What the form holds after "load existing": the masked number, no entry.
		returning = self.insert_as(
			self.host_user,
			entry_type="Existing",
			existing_visitor_pass=earlier.name,
			id_proof_number_masked=masked,
		)
		row = self.stored(returning.name)
		self.assertEqual(row.id_proof_number, full)
		self.assertMasks(full, row.id_proof_number_masked)
		self.assertNoFullNumber(full, returning.as_dict(), "the saved document")

	def test_a_pass_the_user_cannot_read_gives_up_nothing(self):
		full = _permit()
		earlier = self.insert_as(self.host_user, id_proof_number_entry=full)

		frappe.set_user(self.stranger)
		with self.assertRaises(frappe.PermissionError):
			vp_module.get_existing_visitor_pass_details(earlier.name, VISITOR_TYPE)
		found = vp_module.get_existing_visitor_matches(
			visitor_type=VISITOR_TYPE, id_proof_number=full, id_proof_type=ID_TYPE
		)
		self.assertIsNone(found["best_match"], "a pass outside the user's scope was found")
		frappe.set_user("Administrator")

		# Naming that pass as the "existing visitor" does not copy its number either.
		with self.assertRaises(frappe.MandatoryError):
			self.insert_as(
				self.stranger,
				entry_type="Existing",
				existing_visitor_pass=earlier.name,
				id_proof_number_masked=self.stored(earlier.name).id_proof_number_masked,
			)

	def test_amended_pass_keeps_the_number_of_the_pass_it_replaces(self):
		full = _permit()
		cancelled = self.insert_as(self.host_user, id_proof_number_entry=full)
		masked = self.stored(cancelled.name).id_proof_number_masked
		frappe.db.set_value(
			"Visitor Pass", cancelled.name, {"docstatus": 2, "status": "Cancelled"}, update_modified=False
		)

		# The copy the form makes on Amend: the masked number comes along, the full one cannot.
		amended = self.new_pass(amended_from=cancelled.name, id_proof_number_masked=masked)
		amended.insert(ignore_permissions=True)
		self.assertEqual(self.stored(amended.name).id_proof_number, full)


# ─── 5. The portal's way in (contract with portal.py) ────────────────────
class TestPortalEntryPath(FixATestCase):
	def test_portal_sets_the_entry_field_and_stamps_consent(self):
		full = _permit()
		vp = self.new_pass()
		vp.id_proof_number_entry = full
		vp.record_portal_consent("7")
		vp.insert(ignore_permissions=True)

		row = self.stored(vp.name)
		self.assertEqual(row.id_proof_number, full)
		self.assertMasks(full, row.id_proof_number_masked)
		self.assertFalse(row.id_proof_number_entry)

		consent = frappe.db.get_value(
			"Visitor Pass",
			vp.name,
			["consent_given", "consent_timestamp", "consent_notice_version"],
			as_dict=True,
		)
		self.assertEqual(consent.consent_given, 1)
		self.assertTrue(consent.consent_timestamp)
		self.assertEqual(consent.consent_notice_version, "7")

	def test_visitor_reopens_the_draft_without_retyping_the_number(self):
		full = _permit()
		vp = self.new_pass()
		vp.id_proof_number_entry = full
		vp.insert(ignore_permissions=True)

		# portal.py: update() without the number, entry left empty.
		draft = frappe.get_doc("Visitor Pass", vp.name)
		draft.update({"vehicle_number": "MH12XY0001"})
		draft.id_proof_number_entry = None
		draft.save(ignore_permissions=True)
		self.assertEqual(self.stored(vp.name).id_proof_number, full)

	def test_desk_pass_has_no_consent_and_it_cannot_be_forged(self):
		vp = self.insert_as(
			self.host_user, id_proof_number_entry=_permit(), consent_given=1, consent_notice_version="9"
		)
		fields = ["consent_given", "consent_timestamp", "consent_notice_version"]
		consent = frappe.db.get_value("Visitor Pass", vp.name, fields, as_dict=True)
		self.assertEqual(consent.consent_given, 0)
		self.assertFalse(consent.consent_timestamp)
		self.assertFalse(consent.consent_notice_version)

		# read_only is a form setting; set_value does not honour it.
		frappe.set_user(self.host_user)
		frappe.client.set_value("Visitor Pass", vp.name, {"consent_given": 1, "consent_notice_version": "9"})
		frappe.set_user("Administrator")
		consent = frappe.db.get_value("Visitor Pass", vp.name, fields, as_dict=True)
		self.assertEqual(consent.consent_given, 0)
		self.assertFalse(consent.consent_notice_version)

	def test_recorded_consent_survives_a_later_edit(self):
		vp = self.new_pass()
		vp.id_proof_number_entry = _permit()
		vp.record_portal_consent("2")
		vp.insert(ignore_permissions=True)
		stamped = frappe.db.get_value("Visitor Pass", vp.name, "consent_timestamp")

		later = frappe.get_doc("Visitor Pass", vp.name)
		later.update({"vehicle_number": "DL01AA0001", "consent_given": 0, "consent_notice_version": None})
		later.save(ignore_permissions=True)

		consent = frappe.db.get_value(
			"Visitor Pass",
			vp.name,
			["consent_given", "consent_timestamp", "consent_notice_version"],
			as_dict=True,
		)
		self.assertEqual(consent.consent_given, 1)
		self.assertEqual(consent.consent_timestamp, stamped)
		self.assertEqual(consent.consent_notice_version, "2")


# ─── 6. Text a visitor types is stored without markup (VAPT F5) ──────────
class TestGuestTextInRows(FixATestCase):
	def test_item_and_member_text_is_stripped(self):
		vp = self.insert_as(
			self.host_user,
			id_proof_number_entry=_permit(),
			is_group_visit=1,
			group_members=[
				{"visitor_name": "<img src=x onerror=alert(1)>Ravi", "remarks": "<b>walks with a stick</b>"}
			],
			visitor_items=[
				{"item_name": "<b>Laptop</b>", "quantity": 1, "description": "<script>x</script>grey"}
			],
		)
		member = frappe.get_all(
			"Visitor Group Member", filters={"parent": vp.name}, fields=["visitor_name", "remarks"]
		)[0]
		self.assertEqual(member.visitor_name, "Ravi")
		self.assertEqual(member.remarks, "walks with a stick")
		item = frappe.get_all(
			"Visitor Item", filters={"parent": vp.name}, fields=["item_name", "description"]
		)[0]
		self.assertEqual(item.item_name, "Laptop")
		self.assertNotIn("<", item.description or "")


# ─── 6b. Text that can still be edited once the pass is approved ──────────
class TestTextEditableAfterApproval(FixATestCase):
	"""Frappe saves an approved pass without validate(); the stripping must still run."""

	PLAIN = (
		("current_location", "<img src=x onerror=alert(1)>Lobby", "Lobby"),
		(
			"assigned_meal_slots",
			'<a href="https://evil.example">Lunch (13:00 - 14:00)</a>',
			"Lunch (13:00 - 14:00)",
		),
	)

	def _approved(self):
		vp = self.insert_as(self.host_user, id_proof_number_entry=_permit())
		# Approved, as the workflow leaves it.
		frappe.db.set_value(
			"Visitor Pass",
			vp.name,
			{"docstatus": 1, "workflow_state": "Approved", "status": "Approved"},
			update_modified=False,
		)
		return vp.name

	def test_every_text_field_editable_after_approval_is_accounted_for(self):
		"""A free-text field made "Allow on Submit" later must be given a rule too."""
		meta = frappe.get_meta("Visitor Pass")
		text_types = (
			"Data",
			"Small Text",
			"Text",
			"Long Text",
			"Text Editor",
			"HTML Editor",
			"Markdown Editor",
		)
		editable = {df.fieldname for df in meta.fields if df.allow_on_submit and df.fieldtype in text_types}
		accounted_for = {
			*vp_module.VisitorPass.APPROVED_PASS_TEXT_FIELDS,  # stripped to plain text
			"id_proof_number_masked",  # derived from the stored number on every save
			"hospitality_notes",  # formatted text: Frappe's sanitiser (asserted below)
		}
		self.assertEqual(editable - accounted_for, set(), "text editable after approval with no rule")
		for fieldname in vp_module.VisitorPass.APPROVED_PASS_TEXT_FIELDS:
			self.assertTrue(meta.get_field(fieldname).allow_on_submit, fieldname)

	def test_plain_text_is_stripped_on_an_approved_pass(self):
		name = self._approved()
		doc = frappe.get_doc("Visitor Pass", name)
		for fieldname, sent, _kept in self.PLAIN:
			doc.set(fieldname, sent)
		doc.hospitality_notes = (
			'<p>Window <b>seat</b></p><script>alert(1)</script><p onclick="x()">Jain meal</p>'
		)
		doc.save(ignore_permissions=True)

		stored = frappe.db.get_value(
			"Visitor Pass", name, [f for f, _s, _k in self.PLAIN] + ["hospitality_notes"], as_dict=True
		)
		for fieldname, _sent, kept in self.PLAIN:
			self.assertEqual(stored[fieldname], kept, fieldname)
		# The notes are formatted text: the formatting stays, what can run does not.
		self.assertIn("<b>seat</b>", stored.hospitality_notes)
		self.assertIn("Jain meal", stored.hospitality_notes)
		# (Frappe escapes a <script> element to inert text rather than dropping it.)
		for dangerous in ("<script", "onclick"):
			self.assertNotIn(dangerous, stored.hospitality_notes)

	def test_the_same_fields_hold_no_markup_before_approval(self):
		"""On a draft both are worked out by the server; whatever a request sends is not kept."""
		vp = self.insert_as(
			self.host_user,
			id_proof_number_entry=_permit(),
			**{fieldname: sent for fieldname, sent, _kept in self.PLAIN},
		)
		stored = frappe.db.get_value("Visitor Pass", vp.name, [f for f, _s, _k in self.PLAIN], as_dict=True)
		for fieldname, _sent, _kept in self.PLAIN:
			self.assertNotIn("<", stored[fieldname] or "", fieldname)

	def test_an_approved_pass_holding_old_markup_can_still_be_saved(self):
		"""Fields frozen by approval are not "cleaned": that would be a change after submit."""
		name = self._approved()
		legacy = "<b>Audit</b> of line 3"
		frappe.db.set_value("Visitor Pass", name, "purpose_of_visit", legacy, update_modified=False)

		doc = frappe.get_doc("Visitor Pass", name)
		doc.current_location = "Gate 2"
		doc.save(ignore_permissions=True)  # must not raise UpdateAfterSubmitError
		row = frappe.db.get_value(
			"Visitor Pass", name, ["purpose_of_visit", "current_location"], as_dict=True
		)
		self.assertEqual(row.purpose_of_visit, legacy)
		self.assertEqual(row.current_location, "Gate 2")

		# And nobody can put markup into a frozen field afterwards: Frappe refuses the change.
		doc = frappe.get_doc("Visitor Pass", name)
		doc.purpose_of_visit = "<img src=x>new purpose"
		with self.assertRaises(frappe.UpdateAfterSubmitError):
			doc.save(ignore_permissions=True)


# ─── 7. A failed alert or mail is logged and never breaks the save (DEF-8) ─
class TestFailureLogging(FixATestCase):
	LONG = "smtp said no " * 40  # far longer than the 140 characters an Error Log title holds

	def test_failed_blacklist_alert_does_not_raise(self):
		doc = self.new_pass(id_proof_number_entry=_permit())
		barred = frappe._dict(name="VB-FIXA", reason="Fix round A test")
		with (
			patch.object(vp_module, "send_in_background", side_effect=Exception(self.LONG)),
			patch.object(
				vp_module.VisitorPass, "_security_alert_recipients", return_value=["security@example.com"]
			),
		):
			doc._alert_blacklist_match(barred)  # must not raise

		self.assertTrue(frappe.db.exists("Error Log", {"method": "VMS Blacklist Alert"}))

	def test_failed_approval_mail_does_not_raise(self):
		doc = self.new_pass(id_proof_number_entry=_permit())
		doc.name = "VP-FIXA-" + "X" * 150  # a title built from it must still fit
		with patch.object(vp_module.VisitorPass, "_deliver_approval_mail", side_effect=Exception(self.LONG)):
			doc._send_approval_mail("", "", "", [])  # must not raise

		logged = frappe.get_all(
			"Error Log", filters={"method": ["like", "VMS Approval Email%"]}, pluck="method", limit=1
		)
		self.assertTrue(logged)
		self.assertLessEqual(len(logged[0]), 140)


# ─── 8. Shipped defaults and the upgrade step ────────────────────────────
class TestShippedDefaults(FixATestCase):
	def test_only_system_manager_keeps_export_in_the_doctype_file(self):
		path = frappe.get_app_path(
			"visitormanagement", "visitor_management", "doctype", "visitor_pass", "visitor_pass.json"
		)
		doctype = frappe.get_file_json(path)
		exporters = sorted(p["role"] for p in doctype["permissions"] if p.get("export"))
		self.assertEqual(exporters, ["System Manager"])
		self.assertFalse(
			[p for p in doctype["permissions"] if p.get("permlevel")],
			"no role may be granted the permlevel of the stored ID number",
		)

	def test_private_fields_are_not_printed_or_reported(self):
		for doctype in ("Visitor Pass", "Visitor Group Member"):
			meta = frappe.get_meta(doctype)
			for fieldname in ("id_proof_number", "id_proof_number_entry"):
				df = meta.get_field(fieldname)
				self.assertTrue(df.print_hide, f"{doctype}.{fieldname} must be print_hide")
				self.assertTrue(df.report_hide, f"{doctype}.{fieldname} must be report_hide")
				self.assertTrue(df.no_copy, f"{doctype}.{fieldname} must be no_copy")
			self.assertTrue(meta.get_field("id_proof_number").hidden)

		# An image is inlined into a PDF without any File permission check, so the
		# two restricted documents must never be part of a print.
		meta = frappe.get_meta("Visitor Pass")
		for fieldname in ("id_proof_scan", "custom_visa_copy"):
			self.assertTrue(meta.get_field(fieldname).print_hide, f"{fieldname} must be print_hide")


class TestUpgradeStep(FixATestCase):
	def test_backfill_fills_missing_masked_numbers_and_is_idempotent(self):
		lead, member = _permit(), _permit()
		vp = self.insert_as(
			self.host_user,
			id_proof_number_entry=lead,
			is_group_visit=1,
			group_members=[
				{"visitor_name": "Member One", "id_proof_type": ID_TYPE, "id_proof_number_entry": member},
			],
		)
		row_name = self.stored_members(vp.name)[0].name
		# A site as it was before this version: numbers, no masked values.
		frappe.db.set_value("Visitor Pass", vp.name, "id_proof_number_masked", None, update_modified=False)
		frappe.db.set_value(
			"Visitor Group Member", row_name, "id_proof_number_masked", None, update_modified=False
		)
		modified = frappe.db.get_value("Visitor Pass", vp.name, "modified")

		# A small batch, so the walk through the table takes more than one query.
		self.assertGreaterEqual(pass_steps.backfill_masked_numbers("Visitor Pass", batch_size=5), 1)
		self.assertGreaterEqual(pass_steps.backfill_masked_numbers("Visitor Group Member", batch_size=5), 1)

		self.assertMasks(lead, self.stored(vp.name).id_proof_number_masked)
		self.assertMasks(member, self.stored_members(vp.name)[0].id_proof_number_masked)
		self.assertEqual(self.stored(vp.name).id_proof_number, lead, "the backfill must not touch the number")
		self.assertEqual(frappe.db.get_value("Visitor Pass", vp.name, "modified"), modified)

		# Nothing left to do the second time.
		self.assertEqual(pass_steps.backfill_masked_numbers("Visitor Pass"), 0)
		self.assertEqual(pass_steps.backfill_masked_numbers("Visitor Group Member"), 0)

	def test_old_change_history_is_masked(self):
		vp = self.insert_as(self.host_user, id_proof_number_entry=_permit())
		numbers = [_permit() for _ in range(5)]
		frappe.get_doc(
			{
				"doctype": "Version",
				"ref_doctype": "Visitor Pass",
				"docname": vp.name,
				"data": json.dumps(
					{
						"changed": [
							["id_proof_number", numbers[0], numbers[1]],
							["vehicle_number", "A", "B"],
						],
						"added": [["group_members", {"visitor_name": "M", "id_proof_number": numbers[2]}]],
						"removed": [],
						"row_changed": [
							["group_members", 0, "row-1", [["id_proof_number", numbers[3], numbers[4]]]]
						],
					}
				),
			}
		).insert(ignore_permissions=True)

		self.assertGreaterEqual(pass_steps.scrub_pass_versions(batch_size=50), 1)
		history = "\n".join(
			frappe.get_all(
				"Version", filters={"ref_doctype": "Visitor Pass", "docname": vp.name}, pluck="data"
			)
		)
		for number in numbers:
			self.assertNotIn(number, history)
		self.assertIn("vehicle_number", history, "the rest of the history must be left as it was")

	def test_deleted_pass_copies_lose_the_number(self):
		lead, member = _permit(), _permit()
		deleted = frappe.get_doc(
			{
				"doctype": "Deleted Document",
				"deleted_doctype": "Visitor Pass",
				"deleted_name": "VP-FIXA-DELETED",
				"data": json.dumps(
					{
						"doctype": "Visitor Pass",
						"name": "VP-FIXA-DELETED",
						"visitor_full_name": "FixA Deleted",
						"id_proof_number": lead,
						"group_members": [{"visitor_name": "M", "id_proof_number": member}],
					}
				),
			}
		).insert(ignore_permissions=True)

		self.assertGreaterEqual(pass_steps.scrub_deleted_passes(), 1)
		data = frappe.db.get_value("Deleted Document", deleted.name, "data")
		self.assertNotIn(lead, data)
		self.assertNotIn(member, data)
		self.assertIn("FixA Deleted", data)
