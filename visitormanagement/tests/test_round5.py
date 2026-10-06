# See license.txt
"""Round 5 of the 1.1.0 re-test.

Locks in:
  - the Visitor Pass form gets what it needs from VMS Settings and the ID Proof
    Type master through one narrow method, for anyone who can read passes — an
    approver whose login has no Employee role got "No permission for VMS
    Settings" on every pass, because the form read those records itself;
  - that method gives nothing to somebody who cannot read passes, nothing to a
    guest, and nothing but its five values to anybody;
  - VMS Settings itself stays closed to those roles;
  - every form of the app, opened by each role held on its own, makes no
    request that is refused.

Run (never without the two skip flags on a working site):

	bench --site <site> run-tests --module visitormanagement.tests.test_round5 \\
		--skip-before-tests --skip-test-records
"""

import os
import re

import frappe
from frappe.desk.form.load import getdoc
from frappe.model.workflow import get_transitions
from frappe.tests.utils import FrappeTestCase

from visitormanagement.tests.test_round4 import _roles_the_app_names, _single_role_user
from visitormanagement.visitor_management import settings as vms_settings
from visitormanagement.visitor_management import workflow_builder
from visitormanagement.visitor_management.doctype.visitor_pass import visitor_pass as vp_module
from visitormanagement.visitor_management.link_details import APP_MODULES

# The runner walks Link dependencies before setUpClass; nothing here needs them.
test_ignore = ["Employee", "Visitor Type", "Visitor Pass", "User"]

SETTINGS_METHOD = (
	"visitormanagement.visitor_management.doctype.visitor_pass.visitor_pass.get_pass_form_settings"
)


class _Round5Case(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		# Registered before FrappeTestCase adds its rollback, so it runs after it.
		cls.addClassCleanup(frappe.clear_cache)
		super().setUpClass()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.clear_messages()


class TestPassFormSettings(_Round5Case):
	def test_returns_only_the_five_values(self):
		values = vp_module.get_pass_form_settings()
		self.assertEqual(sorted(values), sorted(vp_module.PASS_FORM_SETTING_KEYS))
		self.assertEqual(
			sorted(vp_module.PASS_FORM_SETTING_KEYS),
			[
				"badge_required_for",
				"default_country_code",
				"enable_badge",
				"foreign_national_id_types",
				"home_country",
			],
			"a value added here goes to every role that can open a pass: decide that on purpose",
		)
		self.assertEqual(values["home_country"], vms_settings.home_country())
		self.assertEqual(values["default_country_code"], vms_settings.country_code())
		self.assertIn(values["enable_badge"], (0, 1))
		self.assertIsInstance(values["badge_required_for"], str)
		self.assertEqual(
			sorted(values["foreign_national_id_types"]),
			sorted(frappe.get_all("ID Proof Type", filters={"valid_for_foreign_nationals": 1}, pluck="name")),
		)

	def test_follows_the_settings(self):
		frappe.db.set_single_value("VMS Settings", {"enable_badge": 0, "default_country_code": "44"})
		frappe.clear_document_cache("VMS Settings", "VMS Settings")
		values = vp_module.get_pass_form_settings()
		self.assertEqual((values["enable_badge"], values["default_country_code"]), (0, "44"))

	def test_is_for_signed_in_users_who_can_read_passes(self):
		self.assertIn(vp_module.get_pass_form_settings, frappe.whitelisted)
		self.assertNotIn(vp_module.get_pass_form_settings, frappe.guest_methods)
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			vp_module.get_pass_form_settings()
		frappe.set_user(_single_role_user("Blogger"))
		self.assertFalse(frappe.has_permission("Visitor Pass", "read"))
		with self.assertRaises(frappe.PermissionError):
			vp_module.get_pass_form_settings()

	def test_each_approver_role_held_alone_gets_them_and_settings_stay_closed(self):
		roles = sorted(
			{*workflow_builder.approver_roles(), "Hospitality Manager", "Facility Manager", "Host Employee"}
		)
		self.assertTrue(roles)
		# Who may read the settings record is the DocType's own decision; these roles are not among them.
		readers = set(frappe.get_all("DocPerm", filters={"parent": "VMS Settings", "read": 1}, pluck="role"))
		readers |= set(
			frappe.get_all("Custom DocPerm", filters={"parent": "VMS Settings", "read": 1}, pluck="role")
		)
		for role in roles:
			with self.subTest(role=role):
				frappe.set_user(_single_role_user(role))
				if not frappe.has_permission("Visitor Pass", "read"):
					frappe.set_user("Administrator")
					continue  # this site does not let the role open passes at all
				values = frappe.call(SETTINGS_METHOD)
				self.assertEqual(sorted(values), sorted(vp_module.PASS_FORM_SETTING_KEYS))
				if role not in readers:
					self.assertFalse(
						frappe.has_permission("VMS Settings", "read"),
						f"{role} can now read all of VMS Settings",
					)
					with self.assertRaises(frappe.PermissionError):
						frappe.call(
							"frappe.client.get_single_value", doctype="VMS Settings", field="admin_email"
						)
				frappe.set_user("Administrator")

	def test_form_script_reads_no_record_through_the_generic_client_api(self):
		"""What the pass form needs from other records comes from methods that decide what to give."""
		path = os.path.join(
			frappe.get_app_path("visitormanagement"),
			"visitor_management",
			"doctype",
			"visitor_pass",
			"visitor_pass.js",
		)
		with open(path) as handle:
			source = handle.read()
		code = "\n".join(line for line in source.splitlines() if not line.strip().startswith("//"))
		for pattern in (
			r"\.get_single_value\(",
			r"frappe\.db\s*\.\s*get_value\(",
			r"frappe\.db\s*\.\s*get_doc\(",
		):
			self.assertIsNone(re.search(pattern, code), f"visitor_pass.js: generic read {pattern}")
		self.assertIn("get_pass_form_settings", code)


# What each form asks the server for when a document is opened (its onload /
# refresh path), besides loading the document and its workflow actions.
_FORM_LOAD_CALLS = {
	"Visitor Pass": (
		SETTINGS_METHOD,
		"visitormanagement.visitor_management.workflow_builder.get_visitor_type_approvers",
	),
	"Security Log": (
		"visitormanagement.visitor_management.doctype.security_log.security_log.get_gate_policy",
		"visitormanagement.visitor_management.link_details.get_own_employee",
	),
	"Visitor Invitation": ("visitormanagement.visitor_management.link_details.get_own_employee",),
}


class TestEveryRoleCanOpenItsForms(_Round5Case):
	def test_each_role_on_its_own_opens_every_form_it_can_read(self):
		doctypes = frappe.get_all(
			"DocType", filters={"module": ("in", APP_MODULES), "istable": 0}, pluck="name", order_by="name"
		)
		with_workflow = set(frappe.get_all("Workflow", filters={"is_active": 1}, pluck="document_type"))
		checked = 0
		for role in _roles_the_app_names():
			user = _single_role_user(role)
			for doctype in doctypes:
				frappe.set_user(user)
				if not frappe.has_permission(doctype, "read"):
					continue
				with self.subTest(role=role, form=doctype):
					try:
						if frappe.get_meta(doctype).issingle:
							name = doctype
						else:
							rows = frappe.get_list(doctype, limit_page_length=1, order_by="creation desc")
							name = rows[0].name if rows else None
						if name:
							getdoc(doctype, name)
							if doctype in with_workflow:
								get_transitions(frappe.get_doc(doctype, name))
						for method in _FORM_LOAD_CALLS.get(doctype, ()):
							frappe.call(method)
						checked += 1
					except frappe.PermissionError as exc:
						self.fail(f"{role} can open {doctype} but a request its form makes is refused: {exc}")
					finally:
						frappe.clear_messages()
						frappe.local.response = frappe._dict({"docs": []})
			frappe.set_user("Administrator")
		self.assertGreater(checked, 30, "almost no form was opened: the harness is not looking")
