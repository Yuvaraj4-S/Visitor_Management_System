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
from frappe.tests import IntegrationTestCase

from visitormanagement.tests.test_regression import (
	_SkipApproval,
	_approve_pass,
	_employee_for,
	_fresh_pan,
	_make_pass,
	_user_with_role,
)

# Visit window that overlaps exactly one of the DEFAULT_MEAL_WINDOWS slots
# (Lunch, 13:00-14:00) so a pass with no explicit meal_type still derives a
# deterministic, non-None one. If a site has customised its meal windows,
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


class TestHospitalityRequest(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		# Same pre-existing, non-app issue as test_security_log.py: Hospitality
		# Request links Employee directly (assigned_staff / tour_guide /
		# buggy_driver / greeting_assigned_to), so IntegrationTestCase's
		# auto-generated test-record walk pulls in Company -> Fiscal Year and
		# collides with the real 2026-2027 Fiscal Year on this shared dev
		# site (see CLAUDE.md's note on test_visitor_pass.py). Every fixture
		# this file needs is built by hand against real site data, so skip
		# that walk for this doctype only.
		frappe.local.test_objects.setdefault("Hospitality Request", [])
		super().setUpClass()

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
				"meal_type": "Dinner",
			}
		)
		self.assertEqual(
			vp.meal_type, "Dinner",
			"an explicit Meal Type on a Desk-created pass was overwritten by the derived value",
		)

		# And it should reach a Hospitality Request created against that pass.
		hr = self._hr_for(vp.name)
		hr.insert()
		self.assertEqual(
			hr.meal_type, "Dinner",
			"Hospitality Request did not mirror the Visitor Pass's explicit Meal Type",
		)

	def test_meal_plan_still_auto_derives_when_nothing_was_set(self):
		"""The counter-test the brief asks for: a fix that stops overwriting an
		explicit choice must not also stop deriving a plan when there was no
		explicit choice to protect in the first place."""
		vp = self._pass(
			extra={
				"expected_checkin": _MEAL_OVERLAPPING_CHECKIN,
				"expected_checkout": _MEAL_OVERLAPPING_CHECKOUT,
			}
		)
		self.assertTrue(vp.meal_required, "a visit overlapping a meal window should derive meal_required=1")
		self.assertTrue(
			vp.meal_type,
			"meal_type should have been auto-derived from the visit window when nothing was chosen",
		)

		hr = self._hr_for(vp.name)
		hr.insert()
		self.assertEqual(
			hr.meal_type, vp.meal_type,
			"Hospitality Request should mirror the pass's derived Meal Type when neither was set by hand",
		)

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
			hr.special_diet, "Vegetarian",
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
			hr.special_diet, "Jain",
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
