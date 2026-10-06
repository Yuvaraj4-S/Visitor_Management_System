# The app must not change Frappe core or any other installed app.
#
# Locks in:
#   - no permission rows of this app's on HRMS / ERPNext DocTypes or on Page;
#   - no site-wide desk JS/CSS include and no site-wide logo from hooks.py;
#   - the app's own pickers for Employee / Supplier / Job Applicant /
#     Maintenance Visit answer only callers allowed to pick, with a name and a
#     label only;
#   - the one-time repair that takes back earlier builds' rows (and un-freezes
#     the DocType only when what is left is exactly its standard set);
#   - the Page repair, which removes only 1.0.0's Employee row;
#   - no Form Tour of this app is a "UI tour", which Frappe would run for every
#     user on every page of every app.
#
# FrappeTestCase rolls the database back when the class finishes. The caches the
# repairs clear are cleared again afterwards, so nothing read during a test
# outlives the rollback. test_setup_leaves_no_rows_on_other_apps_doctypes runs
# setup_visitor_management(), which commits — exactly what every migrate runs.

import glob
import json
import os

import frappe
from frappe.desk.doctype.form_tour.form_tour import get_onboarding_ui_tours

from visitormanagement import setup
from visitormanagement.tests.site_staff import StaffedTestCase
from visitormanagement.visitor_management import link_details
from visitormanagement.visitor_management.upgrades import platform_steps

CORE_DOCTYPES = ("Employee", "Supplier", "Job Applicant", "Maintenance Visit")
TEST_DOMAIN = "vms-core-untouched.example.com"


def _make_user(prefix, roles):
	email = f"{prefix}-{frappe.generate_hash(length=6)}@{TEST_DOMAIN}"
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": prefix,
			"send_welcome_email": 0,
			"user_type": "System User",
			"roles": [{"role": r} for r in roles],
		}
	).insert(ignore_permissions=True)
	# Other apps' User hooks may drop a role on save — ERPNext's
	# validate_employee_role removes "Employee" from any user with no Employee
	# record — so make sure the fixture really holds what the tests rely on.
	frappe.clear_cache(user=user.name)
	missing = set(roles) - set(frappe.get_roles(user.name))
	if missing:
		raise AssertionError(f"test user {user.name} lost roles {sorted(missing)} on save")
	return user.name


def _custom_perm(doctype, role, **rights):
	return frappe.get_doc(
		{
			"doctype": "Custom DocPerm",
			"parent": doctype,
			"parenttype": "DocType",
			"parentfield": "permissions",
			"role": role,
			"permlevel": 0,
			**rights,
		}
	).insert(ignore_permissions=True)


class _RollbackCaches(StaffedTestCase):
	@classmethod
	def setUpClass(cls):
		# Class cleanups run last-in first-out: registered before FrappeTestCase's
		# own, this runs after its rollback, so no cache keeps rolled-back state.
		cls.addClassCleanup(frappe.clear_cache)
		super().setUpClass()


class TestNoCoreChanges(_RollbackCaches):
	def test_setup_leaves_no_rows_on_other_apps_doctypes(self):
		setup.setup_visitor_management()
		for doctype in CORE_DOCTYPES:
			standard_roles = set(frappe.get_all("DocPerm", filters={"parent": doctype}, pluck="role"))
			ours = setup.LEGACY_CORE_GRANTS[doctype] - standard_roles
			rows = frappe.get_all(
				"Custom DocPerm", filters={"parent": doctype, "role": ("in", list(ours))}, pluck="role"
			)
			self.assertEqual(rows, [], f"this app still has permission rows on {doctype}: {rows}")
		page_rows = [
			row
			for row in frappe.get_all("Custom DocPerm", filters={"parent": "Page"}, fields=["*"])
			if setup._is_legacy_page_row(row)
		]
		self.assertEqual(page_rows, [], "1.0.0's Employee row on Page is still there")

	def test_grant_refuses_other_apps_doctypes(self):
		before = frappe.db.count("Custom DocPerm", {"parent": "Supplier"})
		setup._grant("Supplier", "Security", "select")
		self.assertEqual(frappe.db.count("Custom DocPerm", {"parent": "Supplier"}), before)
		self.assertFalse(frappe.db.get_default("vms_grant_once:Supplier:Security:select"))

	def test_no_site_wide_desk_includes_or_logo(self):
		site_wide = ("app_include_js", "app_include_css", "app_logo_url", "web_include_js", "web_include_css")
		for hook in site_wide:
			self.assertFalse(
				frappe.get_hooks(hook, app_name="visitormanagement"),
				f"hooks.py declares {hook}, which applies to the whole site",
			)
		public = frappe.get_app_path("visitormanagement", "public")
		self.assertFalse(os.path.exists(os.path.join(public, "js", "core_fixes.js")))
		self.assertFalse(os.path.exists(os.path.join(public, "css", "visitormanagement.css")))

	def test_no_global_link_queries(self):
		self.assertFalse(frappe.get_hooks("standard_queries", app_name="visitormanagement"))


class TestLinkQuery(_RollbackCaches):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		# Sales Manager may create a Visitor Pass (shipped DocPerm), so it may pick a
		# host or supplier there — but not a Job Applicant (HR data). Adding Front
		# Office Executive allows that too. Not the "Employee" role: ERPNext's User
		# hook strips it from a user with no Employee record, which a test user has not.
		cls.picker = _make_user("vms-picker", ["Sales Manager"])
		cls.reception = _make_user("vms-reception", ["Sales Manager", "Front Office Executive"])
		cls.outsider = _make_user("vms-outsider", ["Blogger"])
		cls.employee = frappe.db.get_value("Employee", {"status": "Active"}, "name")
		cls.supplier = frappe.db.get_value("Supplier", {"disabled": 0}, "name")
		cls.applicant = frappe.db.get_value("Job Applicant", {}, "name")

	def tearDown(self):
		frappe.set_user("Administrator")

	def _query(self, doctype, txt="", filters=None, reference_doctype="Visitor Pass", ignore=True):
		return link_details.link_query(doctype, txt, "name", 0, 50, filters or {}, reference_doctype, ignore)

	def test_app_user_gets_name_and_label_only(self):
		if not self.employee:
			self.skipTest("no active Employee on this site")
		frappe.set_user(self.picker)
		rows = self._query("Employee", filters={"status": "Active"})
		names = [r[0] for r in rows]
		self.assertIn(self.employee, names)
		for row in rows:
			expected = frappe.db.get_value("Employee", row[0], ["name", "employee_name", "department"])
			self.assertEqual(tuple(row), tuple(expected), "picker returned more than name/label")

	def test_details_are_narrow(self):
		if not self.employee:
			self.skipTest("no active Employee on this site")
		frappe.set_user(self.picker)
		details = link_details.get_link_details(
			"Employee", self.employee, reference_doctype="Visitor Pass", fieldname="person_to_visit"
		)
		self.assertTrue(set(details) <= {"employee_name", "department"}, details)
		if self.supplier:
			details = link_details.get_link_details(
				"Supplier", self.supplier, reference_doctype="Visitor Pass", fieldname="supplier_link"
			)
			self.assertEqual(set(details) - {"supplier_name"}, set(), details)

	def test_outsider_gets_nothing(self):
		frappe.set_user(self.outsider)
		for doctype in CORE_DOCTYPES:
			self.assertEqual(list(self._query(doctype)), [], f"outsider listed {doctype}")
		if self.employee:
			self.assertRaises(
				frappe.PermissionError, link_details.get_link_details, "Employee", self.employee
			)

	def test_guest_gets_nothing(self):
		frappe.set_user("Guest")
		self.assertEqual(list(self._query("Employee")), [])

	def test_job_applicants_only_for_candidate_handlers(self):
		frappe.set_user(self.picker)
		self.assertEqual(
			list(self._query("Job Applicant")), [], "a pass editor outside HR/reception listed candidates"
		)
		if self.applicant:
			self.assertRaises(
				frappe.PermissionError, link_details.get_link_details, "Job Applicant", self.applicant
			)
		frappe.set_user(self.reception)
		rows = self._query("Job Applicant")
		if self.applicant:
			self.assertIn(self.applicant, [r[0] for r in rows])
			details = link_details.get_link_details("Job Applicant", self.applicant)
			self.assertTrue(set(details) <= {"applicant_name", "job_title"}, details)

	def test_unexpected_filters_are_dropped(self):
		frappe.set_user(self.picker)
		safe = link_details._safe_filters(
			"Employee",
			{
				"status": "Active",
				"user_id": "someone-else@example.com",
				"ctc": [">", 0],
				"department": ["like", "%"],
			},
		)
		self.assertEqual(safe, [("status", "=", "Active")])

	def test_user_permissions_apply_unless_the_field_ignores_them(self):
		employees = frappe.get_all("Employee", filters={"status": "Active"}, pluck="name", limit=2)
		if len(employees) < 2:
			self.skipTest("needs two active Employees")
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": self.picker,
				"allow": "Employee",
				"for_value": employees[0],
				"apply_to_all_doctypes": 1,
			}
		).insert(ignore_permissions=True)
		frappe.set_user(self.picker)
		restricted = [r[0] for r in self._query("Employee", reference_doctype="Visitor Pass", ignore=False)]
		# Frappe also applies the permission to Employee's own Employee links
		# (reports_to), so the allowed record itself may drop out; others never show.
		self.assertTrue(set(restricted) <= {employees[0]}, restricted)
		# person_to_visit is marked Ignore User Permissions on Visitor Pass. The mark is
		# honoured only for the field the picker names (VAPT F2): the request flag on
		# its own, with no field named, leaves the User Permissions in force.
		unnamed = [r[0] for r in self._query("Employee", reference_doctype="Visitor Pass", ignore=True)]
		self.assertTrue(set(unnamed) <= {employees[0]}, unnamed)
		unrestricted = [
			r[0]
			for r in self._query(
				"Employee",
				filters={"link_fieldname": "person_to_visit"},
				reference_doctype="Visitor Pass",
				ignore=True,
			)
		]
		self.assertIn(employees[1], unrestricted)


class TestReleaseRepair(_RollbackCaches):
	DOCTYPE = "Maintenance Visit"

	def setUp(self):
		frappe.db.delete("Custom DocPerm", {"parent": self.DOCTYPE})
		frappe.db.delete("Custom DocPerm", {"parent": "Page"})

	def _freeze_with_app_row(self):
		from frappe.permissions import setup_custom_perms

		setup_custom_perms(self.DOCTYPE)
		_custom_perm(self.DOCTYPE, "Host Employee", select=1, read=0, export=0)

	def test_frozen_standard_set_is_released(self):
		self._freeze_with_app_row()
		released = setup.release_core_doctype_permissions(
			self.DOCTYPE, setup.LEGACY_CORE_GRANTS[self.DOCTYPE]
		)
		self.assertTrue(released)
		self.assertFalse(frappe.db.exists("Custom DocPerm", {"parent": self.DOCTYPE}))

	def test_customised_set_is_left_alone(self):
		self._freeze_with_app_row()
		# The site changed one of the copied standard rows: theirs to keep.
		row = frappe.db.get_value(
			"Custom DocPerm",
			{"parent": self.DOCTYPE, "role": ("!=", "Host Employee")},
			["name", "export"],
			as_dict=True,
		)
		frappe.db.set_value("Custom DocPerm", row.name, "export", 0 if row.export else 1)
		count_before = frappe.db.count("Custom DocPerm", {"parent": self.DOCTYPE})
		released = setup.release_core_doctype_permissions(
			self.DOCTYPE, setup.LEGACY_CORE_GRANTS[self.DOCTYPE]
		)
		self.assertFalse(released)
		self.assertFalse(
			frappe.db.exists("Custom DocPerm", {"parent": self.DOCTYPE, "role": "Host Employee"})
		)
		self.assertEqual(frappe.db.count("Custom DocPerm", {"parent": self.DOCTYPE}), count_before - 1)

	def test_stale_grant_markers_are_forgotten(self):
		marker = f"vms_grant_once:{self.DOCTYPE}:Host Employee:select"
		own_marker = "vms_grant_once:Visitor Pass:System Manager:cancel"
		frappe.db.set_default(marker, "1")
		frappe.db.set_default(own_marker, "1")
		setup._forget_grant_markers(self.DOCTYPE)
		self.assertFalse(frappe.db.get_default(marker))
		self.assertTrue(frappe.db.get_default(own_marker), "a marker of the app's own DocType was dropped")

	def test_page_repair_removes_only_the_legacy_employee_row(self):
		# 1.0.0's patch: Employee, read, and the export Custom DocPerm defaults to.
		_custom_perm("Page", "Employee", read=1, export=1)
		admin_row = _custom_perm("Page", "Website Manager", read=1, export=0)
		setup.release_core_doctype_permissions("Page", {"Employee"}, is_ours=setup._is_legacy_page_row)
		self.assertFalse(frappe.db.exists("Custom DocPerm", {"parent": "Page", "role": "Employee"}))
		self.assertTrue(frappe.db.exists("Custom DocPerm", admin_row.name), "an admin's Page row was removed")

	def test_page_repair_keeps_an_admin_modified_employee_row(self):
		row = _custom_perm("Page", "Employee", read=1, write=1, export=1)
		setup.release_core_doctype_permissions("Page", {"Employee"}, is_ours=setup._is_legacy_page_row)
		self.assertTrue(frappe.db.exists("Custom DocPerm", row.name))

	def test_page_repair_alone_unfreezes_page(self):
		_custom_perm("Page", "Employee", read=1, export=1)
		released = setup.release_core_doctype_permissions(
			"Page", {"Employee"}, is_ours=setup._is_legacy_page_row
		)
		self.assertTrue(released)
		self.assertFalse(frappe.db.exists("Custom DocPerm", {"parent": "Page"}))


# ─── No site-wide "UI tours" ──────────────────────────────────────────────
# Frappe 15 puts every Form Tour with ui_tour = 1 into the boot data of every
# desk user (form_tour.get_onboarding_ui_tours has no role or module filter) and
# then loads and runs its onboarding-tour script on every page of every app for
# them. That script throws on a slow page load. An earlier build of this app
# shipped three such tours — the only ones on a site with Frappe, ERPNext and
# HRMS — so it was the reason that code ran on other apps' pages at all.
class TestNoSiteWideTours(_RollbackCaches):
	WORKSPACE_TOUR = "VMS Setup Tour"

	def tearDown(self):
		frappe.set_user("Administrator")
		# Each test builds its own "site as an earlier build left it" under the
		# same record names; nothing in this class commits.
		frappe.db.rollback()

	def _shipped_tour_files(self):
		root = frappe.get_app_path("visitormanagement")
		return sorted(glob.glob(os.path.join(root, "*", "form_tour", "*", "*.json")))

	def test_no_shipped_tour_is_a_ui_tour(self):
		files = self._shipped_tour_files()
		self.assertEqual(
			sorted(os.path.basename(os.path.dirname(path)) for path in files),
			sorted(frappe.scrub(name) for name in platform_steps.SHIPPED_FORM_TOURS),
		)
		for path in files:
			with open(path) as handle:
				tour = json.load(handle)
			with self.subTest(tour=tour["name"]):
				self.assertFalse(tour.get("ui_tour"), "a UI tour is booted for every user of the site")
				self.assertFalse(tour.get("page_route"))
				self.assertIn(tour.get("module"), link_details.APP_MODULES)
				# An ordinary form tour: tied to one of this app's DocTypes, field by field.
				meta = frappe.get_meta(tour["reference_doctype"])
				self.assertIn(meta.module, link_details.APP_MODULES)
				for step in tour["steps"]:
					self.assertFalse(step.get("ui_tour"))
					self.assertFalse(step.get("element_selector"))
					df = meta.get_field(step["fieldname"])
					self.assertIsNotNone(df, f"{tour['name']}: no field {step['fieldname']}")
					self.assertEqual((step["label"], step["fieldtype"]), (df.label, df.fieldtype))
		# A tour of the workspace can only be a UI tour, so the app has none.
		self.assertNotIn(
			frappe.scrub(self.WORKSPACE_TOUR), [os.path.basename(os.path.dirname(p)) for p in files]
		)

	def test_site_has_no_ui_tour_of_this_app(self):
		ours = frappe.get_all(
			"Form Tour", filters={"ui_tour": 1, "module": ("in", link_details.APP_MODULES)}, pluck="name"
		)
		self.assertEqual(ours, [])
		self.assertFalse(frappe.db.exists("Form Tour", self.WORKSPACE_TOUR))

	def test_boot_carries_no_tour_of_this_app(self):
		"""What decides whether core loads its tour script for a user who never opens this app."""
		frappe.db.set_single_value("System Settings", "enable_onboarding", 1)
		app_tours = set(
			frappe.get_all("Form Tour", filters={"module": ("in", link_details.APP_MODULES)}, pluck="name")
		)
		self.assertTrue(app_tours, "the app's form tours were not imported")
		others = {
			tour.name
			for tour in frappe.get_all("Form Tour", filters={"ui_tour": 1}, fields=["name", "module"])
			if tour.module not in link_details.APP_MODULES
		}
		frappe.set_user(_make_user("vms-tour-outsider", ["Blogger"]))
		booted = {name for name, _route in get_onboarding_ui_tours()}
		self.assertFalse(booted & (app_tours | {self.WORKSPACE_TOUR}))
		# Nothing but what other apps (or the site) defined; with none, an empty list.
		self.assertEqual(booted, others)

	def test_the_tours_are_started_from_the_onboarding_block(self):
		block = frappe.get_doc("Module Onboarding", "Visitor Management Onboarding")
		steps = [frappe.get_doc("Onboarding Step", row.step) for row in block.steps]
		with_tour = [step for step in steps if step.form_tour]
		self.assertEqual(
			sorted(step.form_tour for step in with_tour), sorted(platform_steps.SHIPPED_FORM_TOURS)
		)
		for step in with_tour:
			with self.subTest(step=step.name):
				tour = frappe.get_doc("Form Tour", step.form_tour)
				self.assertFalse(tour.ui_tour)
				# The widget opens a form of the step's DocType and starts the named tour on it.
				self.assertEqual(tour.reference_doctype, step.reference_document)
				self.assertIn(step.action, ("Create Entry", "Show Form Tour"))
				if step.action == "Create Entry":
					# Only the full form runs a tour (onboarding_widget.js create_entry).
					self.assertTrue(step.show_full_form and step.show_form_tour)
				else:
					# on_finish fires on the last step; a "save" step appended after it would swallow it.
					self.assertFalse(tour.save_on_complete)

	def _as_an_earlier_build_left_it(self):
		"""The three UI tours, a UI tour that is not this app's, and one user's progress on all."""
		for name, values in (
			(
				self.WORKSPACE_TOUR,
				{
					"module": "Visitor Management",
					"view_name": "Workspaces",
					"page_route": '["Workspaces", "Visitor Management"]',
				},
			),
			(
				"ZZ Somebody Elses UI Tour",
				{"module": "Desk", "view_name": "List", "page_route": '["List", "ToDo"]'},
			),
		):
			tour = frappe.get_doc(
				{"doctype": "Form Tour", "title": name, "ui_tour": 1, "is_standard": 1, **values}
			)
			tour.name = name
			tour.db_insert()
		shipped = platform_steps.SHIPPED_FORM_TOURS[0]
		frappe.db.set_value(
			"Form Tour",
			shipped,
			{"ui_tour": 1, "view_name": "Form", "page_route": '["Form", "Visitor Type", "new-*"]'},
			update_modified=False,
		)
		user = _make_user("vms-tour-progress", ["Blogger"])
		status = {
			self.WORKSPACE_TOUR: {"steps_complete": 2},
			shipped: {"is_complete": True},
			"ZZ Somebody Elses UI Tour": {"is_complete": True},
		}
		frappe.db.set_value("User", user, "onboarding_status", json.dumps(status), update_modified=False)
		frappe.defaults.clear_default(key=platform_steps._UI_TOURS_MARKER, parent="__default")
		return shipped, user

	def test_upgrade_retires_the_ui_tours_and_nothing_else(self):
		shipped, user = self._as_an_earlier_build_left_it()

		platform_steps._retire_ui_tours()

		self.assertFalse(frappe.db.exists("Form Tour", self.WORKSPACE_TOUR))
		tour = frappe.db.get_value("Form Tour", shipped, ["ui_tour", "page_route"], as_dict=True)
		self.assertEqual((tour.ui_tour, tour.page_route), (0, None))
		self.assertFalse(
			frappe.get_all("Form Tour", filters={"ui_tour": 1, "module": ("in", link_details.APP_MODULES)})
		)
		# Not this app's: still a UI tour, and the user's progress on it is kept.
		self.assertEqual(frappe.db.get_value("Form Tour", "ZZ Somebody Elses UI Tour", "ui_tour"), 1)
		status = json.loads(frappe.db.get_value("User", user, "onboarding_status"))
		self.assertEqual(status, {"ZZ Somebody Elses UI Tour": {"is_complete": True}})
		self.assertTrue(frappe.db.get_default(platform_steps._UI_TOURS_MARKER))

	def test_the_removal_is_one_time_but_shipped_tours_never_stay_ui_tours(self):
		shipped, _user = self._as_an_earlier_build_left_it()
		platform_steps._retire_ui_tours()

		# An administrator's own tour under the old name, made afterwards, is theirs.
		own = frappe.get_doc(
			{
				"doctype": "Form Tour",
				"title": self.WORKSPACE_TOUR,
				"ui_tour": 1,
				"module": "Visitor Management",
				"view_name": "Workspaces",
				"page_route": '["Workspaces", "Visitor Management"]',
			}
		)
		own.name = self.WORKSPACE_TOUR
		own.db_insert()
		# The app's shipped record switched back on (a restored backup, an old fixture).
		frappe.db.set_value("Form Tour", shipped, "ui_tour", 1, update_modified=False)

		platform_steps._retire_ui_tours()
		self.assertTrue(frappe.db.exists("Form Tour", self.WORKSPACE_TOUR))
		self.assertEqual(frappe.db.get_value("Form Tour", shipped, "ui_tour"), 0)
