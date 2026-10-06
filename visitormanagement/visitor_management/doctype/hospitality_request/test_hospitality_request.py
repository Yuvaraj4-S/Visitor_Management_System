# See license.txt

"""Hospitality Request — meals, hotel, cab, tour and room prep for a visitor.

This DocType shipped with zero test coverage. The other Blocker bug found and
fixed this session lived here: `populate_hospitality_request_from_pass`
(visitormanagement/visitor_management/lifecycle.py) used to overwrite a
Hospitality Manager's own Special Diet pick on every save, and a related fix
in the same area stopped a Desk-typed Meal Type on the Visitor Pass from
being silently re-derived away. Empty coverage is exactly how both shipped
unnoticed.

Helpers are imported from test_regression rather than re-written, so a change
to how a Visitor Pass is built or approved only has to be made in one place.
"""

from __future__ import annotations

import frappe
from frappe.model.workflow import apply_workflow

from visitormanagement.tests.site_staff import StaffedTestCase
from visitormanagement.tests.test_regression import (
	_approve_pass,
	_employee_for,
	_fresh_pan,
	_make_pass,
	_SkipApproval,
	_user_with_role,
)

# Visit window that overlaps exactly one of the DEFAULT_MEAL_WINDOWS slots
# (Lunch, 13:00-14:00) so a pass with Meal Required ticked but no explicit
# meal_type still derives a deterministic, non-None one. If a site has customised its meal windows,
# `derive_hospitality_meal_plan` may compute something else for the same
# window — the "still derives *something*" assertions below hold regardless;
# only the exact label is compared where the site could not plausibly differ.
_MEAL_OVERLAPPING_CHECKIN = "10:00:00"
_MEAL_OVERLAPPING_CHECKOUT = "17:00:00"

ARRANGEMENT_FLAGS = (
	"cab_required",
	"hotel_required",
	"factory_tour_required",
	"buggy_required",
	"greeting_required",
)


# Frappe 15's test runner builds test records for this doctype's whole Link
# dependency chain when it loads this module, before setUpClass runs. Through
# Employee that walk reaches Company -> Fiscal Year, and ERPNext's Fiscal Year
# test records collide with a real Fiscal Year already on a working site. Every
# fixture this file needs is built by hand against real site data, so the walk
# is skipped for this doctype only (the Frappe 16 branch does the same thing by
# pre-marking `frappe.local.test_objects` in setUpClass).
test_ignore = ["Conference Room", "Employee", "Supplier", "Visitor Pass", "Workflow State"]


class TestHospitalityRequest(StaffedTestCase):
	def setUp(self):
		self.host = frappe.db.get_value("Employee", {"status": "Active"}, "name")
		if not self.host:
			self.skipTest("no Active Employee on this site")

	def _pass(self, extra=None):
		return _make_pass("Customer", "HR Tester", _fresh_pan(), self.host, extra=extra)

	def _hr_for(self, vp_name, extra=None):
		hr = frappe.new_doc("Hospitality Request")
		hr.visitor_pass = vp_name
		if extra:
			hr.update(extra)
		return hr

	# ---------------- approval workflow states ----------------

	def test_approval_workflow_states(self):
		employee_user = _user_with_role("Employee")
		hospmgr_user = _user_with_role("Hospitality Manager")
		if not employee_user or not hospmgr_user:
			self.skipTest("Employee or Hospitality Manager user missing on this site")

		host = _employee_for(employee_user) or self.host
		frappe.set_user(employee_user)
		try:
			draft_vp = self._pass()
			approved_vp = _make_pass("Customer", "HR Workflow Tester", _fresh_pan(), host)
		finally:
			frappe.set_user("Administrator")
		try:
			_approve_pass(approved_vp.name, "Customer")
		except _SkipApproval as exc:
			self.skipTest(f"no user holding {exc} on this site")

		frappe.set_user(employee_user)
		try:
			# Draft can be created against an unapproved pass.
			hr = self._hr_for(draft_vp.name)
			hr.insert()
			self.assertEqual(hr.workflow_state or "Draft", "Draft")

			# But it cannot leave Draft until the linked pass is Approved+.
			with self.assertRaises(frappe.ValidationError) as ctx:
				apply_workflow(hr, "Submit")
			self.assertIn("Visitor Pass", str(ctx.exception))

			# Against an Approved pass, Submit is allowed.
			hr2 = self._hr_for(approved_vp.name)
			hr2.insert()
			apply_workflow(hr2, "Submit")
			hr2.reload()
			self.assertEqual(hr2.workflow_state, "Pending Approval")
		finally:
			frappe.set_user("Administrator")

		frappe.set_user(hospmgr_user)
		try:
			apply_workflow(frappe.get_doc("Hospitality Request", hr2.name), "Approve")
		finally:
			frappe.set_user("Administrator")
		hr2.reload()
		self.assertEqual(hr2.workflow_state, "Approved")

	# ---------------- Meal Type: explicit choice vs. derivation ----------------

	def test_explicit_meal_type_on_desk_pass_survives_the_creating_save(self):
		"""The Visitor Pass half of this session's fix: `normalize_visitor_pass`
		must not let the derived meal plan clobber a Meal Type a receptionist
		just typed, on the very save that carries it in. (A later resave that
		touches an unrelated field is a separate question this test does not
		make a claim about — see FINDINGS.)"""
		vp = self._pass(
			extra={
				"expected_checkin": _MEAL_OVERLAPPING_CHECKIN,
				"expected_checkout": _MEAL_OVERLAPPING_CHECKOUT,
				# Meal Required is the host's decision and is no longer ticked from
				# the visit window on the server (lifecycle.apply_hospitality_meal_plan);
				# without it the meal details, Meal Type included, are cleared.
				"meal_required": 1,
				"meal_type": "Dinner",
			}
		)
		self.assertEqual(
			vp.meal_type,
			"Dinner",
			"an explicit Meal Type on a Desk-created pass was overwritten by the derived value",
		)

		# And it should reach a Hospitality Request created against that pass.
		hr = self._hr_for(vp.name)
		hr.insert()
		self.assertEqual(
			hr.meal_type,
			"Dinner",
			"Hospitality Request did not mirror the Visitor Pass's explicit Meal Type",
		)

	def test_meal_plan_still_auto_derives_when_nothing_was_set(self):
		"""The counter-test the brief asks for: a fix that stops overwriting an
		explicit choice must not also stop deriving a plan when there was no
		explicit choice to protect. Meal type/slots derive from the visit window
		when Meal Required is ticked but no type was chosen."""
		vp = self._pass(
			extra={
				"expected_checkin": _MEAL_OVERLAPPING_CHECKIN,
				"expected_checkout": _MEAL_OVERLAPPING_CHECKOUT,
				"meal_required": 1,
			}
		)
		self.assertTrue(vp.meal_required, "an explicitly ticked Meal Required must survive the save")
		self.assertTrue(
			vp.meal_type,
			"meal_type should have been auto-derived from the visit window when nothing was chosen",
		)

		hr = self._hr_for(vp.name)
		hr.insert()
		self.assertEqual(
			hr.meal_type,
			vp.meal_type,
			"Hospitality Request should mirror the pass's derived Meal Type when neither was set by hand",
		)

	def test_visit_window_alone_does_not_order_a_meal(self):
		"""Meal Required is the host's decision: a visit window that overlaps a
		meal slot only suggests it (the desk form ticks it live, where the host can
		untick it). The server must not force it on, and with no meal asked for
		there are no meal details either."""
		vp = self._pass(
			extra={
				"expected_checkin": _MEAL_OVERLAPPING_CHECKIN,
				"expected_checkout": _MEAL_OVERLAPPING_CHECKOUT,
			}
		)
		self.assertFalse(vp.meal_required, "the server ticked Meal Required on its own from the visit window")
		self.assertIsNone(vp.meal_type, "a pass with no meal requested must carry no Meal Type")

	# ---------------- Special Diet: explicit choice vs. blank fallback ----------------

	def test_explicit_special_diet_on_the_request_survives_save(self):
		"""The Blocker itself: `populate_hospitality_request_from_pass` used to
		overwrite whatever the Hospitality Manager just picked here with the
		Visitor Pass's (usually blank/"None") value on every single save."""
		vp = self._pass()
		hr = self._hr_for(vp.name, extra={"special_diet": "Vegetarian"})
		hr.insert()
		self.assertEqual(hr.special_diet, "Vegetarian")

		# Re-save with the choice left untouched — must still hold.
		hr.save()
		hr.reload()
		self.assertEqual(
			hr.special_diet,
			"Vegetarian",
			"an explicit Special Diet did not survive a save of the Hospitality Request",
		)

	def test_special_diet_flows_through_from_the_pass_when_left_blank(self):
		"""The complementary case a prior pass at this fix could not cleanly
		prove: when NOBODY has chosen a Special Diet on the Hospitality
		Request, the Visitor Pass's own value must still flow in — a fix that
		stops the overwrite must not also stop the ordinary fetch. Sets
		special_diet directly on the Visitor Pass via db_set (bypassing its
		own validate/normalize path, which has no dependency on this field)
		so the assertion isolates populate_hospitality_request_from_pass's
		own fallback branch rather than any Visitor Pass-side behaviour."""
		vp = self._pass()
		frappe.db.set_value("Visitor Pass", vp.name, "special_diet", "Jain", update_modified=False)

		hr = self._hr_for(vp.name)  # special_diet left untouched: blank on a fresh doc
		hr.insert()
		self.assertEqual(
			hr.special_diet,
			"Jain",
			"a blank Special Diet on a new Hospitality Request should have been filled in from the Visitor Pass",
		)

	# ---------------- arrangement-flag sections can render ----------------

	def test_arrangement_flags_are_visible_and_read_only(self):
		"""cab/hotel/tour/buggy/greeting *_required are mirrored read-only from
		the Visitor Pass (see ARRANGEMENT_REQUIRED_FIELDS in lifecycle.py) so
		the hospitality team can see which sections apply without being able
		to invent a request the host never made. `hidden` must stay falsy —
		a hidden field hides its whole section, and the section can't render
		what the field never shows."""
		meta = frappe.get_meta("Hospitality Request")
		for fieldname in ARRANGEMENT_FLAGS:
			with self.subTest(field=fieldname):
				df = meta.get_field(fieldname)
				self.assertIsNotNone(df, f"{fieldname} is missing from Hospitality Request")
				self.assertFalse(df.hidden, f"{fieldname} is hidden — its section cannot render")
				self.assertTrue(df.read_only, f"{fieldname} should be read-only (mirrored, not editable)")


def _user_holding(*roles):
	"""A new System User holding exactly `roles`, rolled back with the test class.

	The roles are written directly: other apps' User hooks drop some on save
	(ERPNext removes "Employee" from a user with no Employee record).
	"""
	email = f"hosp-link-{frappe.generate_hash(length=8)}@example.com"
	frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": "Hospitality Link",
			"send_welcome_email": 0,
			"user_type": "System User",
		}
	).insert(ignore_permissions=True)
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
		row.name = frappe.generate_hash(length=10)
		row.db_insert()
	frappe.clear_cache(user=email)
	return email


class TestVisitorPassLinkAccess(StaffedTestCase):
	"""A Hospitality Request copies from the pass it names and writes back to it,
	so only someone with business on that pass may link it."""

	def setUp(self):
		self.host = frappe.db.get_value("Employee", {"status": "Active"}, "name")
		if not self.host:
			self.skipTest("no Active Employee on this site")
		if not all(frappe.db.exists("Role", role) for role in ("Employee", "Hospitality Manager")):
			self.skipTest("needs the Employee and Hospitality Manager roles")
		frappe.set_user("Administrator")
		# Somebody else's pass, asking for no hospitality service at all.
		self.other_pass = _make_pass("Customer", "Link Access Visitor", _fresh_pan(), self.host)
		self.staff = _user_holding("Employee")
		if not frappe.has_permission("Hospitality Request", "create", user=self.staff):
			self.skipTest("the Employee role cannot create a Hospitality Request on this site")

	def tearDown(self):
		frappe.set_user("Administrator")

	def _request_for(self, pass_name):
		request = frappe.new_doc("Hospitality Request")
		request.visitor_pass = pass_name
		return request

	def test_staff_cannot_link_a_pass_they_cannot_open(self):
		frappe.set_user(self.staff)
		self.assertFalse(frappe.has_permission("Visitor Pass", "read", doc=self.other_pass.name))
		with self.assertRaises(frappe.PermissionError):
			self._request_for(self.other_pass.name).insert()
		frappe.set_user("Administrator")
		# Nothing was read from the pass, and nothing written back to it.
		self.assertFalse(frappe.db.exists("Hospitality Request", {"visitor_pass": self.other_pass.name}))
		self.assertFalse(frappe.db.get_value("Visitor Pass", self.other_pass.name, "hospitality_request"))

	def test_staff_can_link_their_own_pass_and_keep_saving_it(self):
		frappe.set_user(self.staff)
		own_pass = _make_pass("Customer", "Link Access Own Visitor", _fresh_pan(), self.host)
		request = self._request_for(own_pass.name)
		request.insert()
		self.assertEqual(request.visitor_name_display, "Link Access Own Visitor")
		# An unchanged link is not checked again.
		request.notes = "window seat"
		request.save()
		# Repointing it at somebody else's pass is.
		request.visitor_pass = self.other_pass.name
		with self.assertRaises(frappe.PermissionError):
			request.save()

	def test_hospitality_manager_can_raise_a_request_for_any_pass(self):
		manager = _user_holding("Hospitality Manager")
		frappe.set_user(manager)
		# Not theirs to open yet: it asks for no service and has no request.
		self.assertFalse(frappe.has_permission("Visitor Pass", "read", doc=self.other_pass.name))
		request = self._request_for(self.other_pass.name)
		request.insert()
		self.assertEqual(request.visitor_name_display, "Link Access Visitor")
		# The pass now has a request, which is what opens it to the hospitality desk.
		self.assertTrue(frappe.has_permission("Visitor Pass", "read", doc=self.other_pass.name))

	def test_the_apps_own_request_for_a_pass_is_not_refused(self):
		"""lifecycle.ensure_hospitality_request runs as whoever saves the pass."""
		frappe.set_user(self.staff)
		request = self._request_for(self.other_pass.name)
		request.insert(ignore_permissions=True)
		self.assertTrue(request.name)
