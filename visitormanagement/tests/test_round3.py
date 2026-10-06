# See license.txt
"""Round 3 of the 1.1.0 re-test.

Locks in:
  - a visit is approved by somebody else: not by the person who created the
    pass, not by the person who sent it for approval, not by the host — on a
    pass a visitor pre-registered (owned by "Guest") as much as on one typed in
    the desk, at every step of a two-step approval, and whichever way the
    approval arrives (the Actions menu, apply_workflow, a bare submit);
  - the public meal-plan endpoint keeps its limit for anonymous callers and
    does not count signed-in staff; the desk form has its own method;
  - every block of the app's workspaces resolves to one of its rows.

Run (never without the two skip flags on a working site):

	bench --site <site> run-tests --module visitormanagement.tests.test_round3 \\
		--skip-before-tests --skip-test-records

Users, Employees and passes are created inside the test transaction and rolled
back with it. The Visitor Types are the ones the app ships (Customer, VIP,
Contractor); a class is skipped where a site has removed or re-routed them.
"""

import glob
import json
import os
from unittest.mock import patch

import frappe
from frappe.model.workflow import apply_workflow, get_transitions
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, nowdate

from visitormanagement.tests.test_fix_a import _make_employee, _make_user
from visitormanagement.tests.test_regression import _file, _fresh_pan
from visitormanagement.visitor_management import lifecycle
from visitormanagement.visitor_management.link_details import APP_MODULES

# The runner walks Link dependencies before setUpClass; through Employee that
# reaches ERPNext's Company -> Fiscal Year test records, which collide with a
# real Fiscal Year on a working site. Every fixture here is built by hand.
test_ignore = ["Employee", "ID Proof Type", "Visitor Type", "Visitor Pass", "User"]


def _approver_roles(visitor_type):
	return frappe.db.get_value(
		"Visitor Type", visitor_type, ["approver_role", "secondary_approver_role"], as_dict=True
	)


class _ApprovalCase(FrappeTestCase):
	"""A host who is also an approver, another approver, and a clerk who is neither."""

	VISITOR_TYPE = "Customer"
	ROLE = "Sales Manager"
	SECOND_ROLE = None

	@classmethod
	def setUpClass(cls):
		# Registered before FrappeTestCase adds its rollback, so it runs after it.
		cls.addClassCleanup(frappe.clear_cache)
		super().setUpClass()
		frappe.set_user("Administrator")
		routing = _approver_roles(cls.VISITOR_TYPE)
		cls.routed_as_shipped = bool(
			routing
			and routing.approver_role == cls.ROLE
			and (routing.secondary_approver_role or None) == cls.SECOND_ROLE
		)
		if not cls.routed_as_shipped:
			return
		roles = [r for r in (cls.ROLE, cls.SECOND_ROLE) if r]
		# Holds every approver role of this type, and is the host.
		cls.host_approver = _make_user("r3-hostappr", "Employee", *roles)
		cls.host_employee = _make_employee(cls.host_approver)
		# Other holders of each approver role: nothing to do with the visit.
		cls.other_approver = _make_user("r3-approver", "Employee", cls.ROLE)
		cls.other_second = _make_user("r3-second", "Employee", cls.SECOND_ROLE) if cls.SECOND_ROLE else None
		# Raises passes for other people; holds the first approver role too.
		cls.clerk_approver = _make_user("r3-clerkappr", "Employee", cls.ROLE)
		# Plain staff.
		cls.clerk = _make_user("r3-clerk", "Employee")
		cls.plain_host_user = _make_user("r3-host", "Employee")
		cls.plain_host = _make_employee(cls.plain_host_user)

	def setUp(self):
		frappe.set_user("Administrator")
		if not self.routed_as_shipped:
			self.skipTest(f"{self.VISITOR_TYPE} is not routed to {self.ROLE} on this site")
		self._day = 0

	def tearDown(self):
		frappe.set_user("Administrator")

	# ── helpers ──────────────────────────────────────────────
	def new_pass(self, created_by, host):
		"""A Draft pass; created_by "Guest" is what the public form produces."""
		self._day += 1
		label = frappe.generate_hash(length=8)
		scan, photo = _file(f"r3_id_{label}"), _file(f"r3_photo_{label}")
		vp = frappe.new_doc("Visitor Pass")
		vp.update(
			{
				"visitor_type": self.VISITOR_TYPE,
				"visitor_full_name": f"Round Three {label}",
				"mobile_number": "+91 9876543210",
				"email_id": f"r3-{label}@example.com",
				"id_proof_type": "PAN Card",
				"id_proof_number": _fresh_pan(),
				"id_proof_scan": scan,
				"visitor_photo": photo,
				"person_to_visit": host,
				"visit_date": add_days(nowdate(), self._day),
				"expected_checkin": "10:00:00",
				"expected_checkout": "11:00:00",
				"purpose_of_visit": "Round 3 approval test",
				"request_channel": "Portal" if created_by == "Guest" else "Desk",
			}
		)
		vp.flags.ignore_mandatory = True
		frappe.set_user(created_by)
		try:
			# portal.submit_pre_registration inserts exactly like this for a visitor.
			vp.insert(ignore_permissions=(created_by == "Guest"))
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Visitor Pass", vp.name, "owner"), created_by)
		return vp.name

	def act(self, user, name, action):
		frappe.set_user(user)
		try:
			return apply_workflow(frappe.get_doc("Visitor Pass", name), action)
		finally:
			frappe.set_user("Administrator")

	def offered(self, user, name):
		"""The actions the form's Actions menu (and the list's bulk actions) offer `user`."""
		frappe.set_user(user)
		try:
			return sorted({t["action"] for t in get_transitions(frappe.get_doc("Visitor Pass", name))})
		finally:
			frappe.set_user("Administrator")

	def state(self, name):
		return frappe.db.get_value("Visitor Pass", name, "workflow_state")

	def submitter(self, name):
		return frappe.db.get_value("Visitor Pass", name, "submitted_for_approval_by")

	def assertCannotApprove(self, user, name, refused_by=frappe.PermissionError):
		"""Not offered, refused by the workflow, and refused by the pass itself.

		`refused_by` is what refuses once the workflow's conditions are out of the
		way: the pass (PermissionError), or — for the creator, whom Frappe's own
		"no self approval" rule stops before the pass is asked — a ValidationError.
		"""
		before = self.state(name)
		self.assertNotIn("Approve", self.offered(user, name))
		with self.assertRaises(frappe.ValidationError):
			self.act(user, name, "Approve")
		# The rule does not live in the workflow: with every transition condition
		# passing (a hand-edited workflow, an old one), the document still refuses.
		with patch("frappe.model.workflow.is_transition_condition_satisfied", return_value=True):
			with self.assertRaises(refused_by):
				self.act(user, name, "Approve")
		# A bare submit through the API (frappe.client.submit) is an approval too.
		frappe.set_user(user)
		try:
			with self.assertRaises((frappe.PermissionError, frappe.ValidationError)):
				frappe.get_doc("Visitor Pass", name).submit()
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(self.state(name), before)
		self.assertEqual(frappe.db.get_value("Visitor Pass", name, "docstatus"), 0)


# ─── R3-1: one approval step ─────────────────────────────────────────────
class TestApprovalIsBySomebodyElse(_ApprovalCase):
	def test_host_cannot_approve_the_visit_they_invited_and_submitted(self):
		"""The reported case: pre-registered by the visitor, so the pass is owned by Guest."""
		name = self.new_pass("Guest", self.host_employee)

		self.act(self.host_approver, name, "Submit")
		lane = f"Pending {self.ROLE}"
		self.assertEqual(self.state(name), lane)
		self.assertEqual(self.submitter(name), self.host_approver)

		self.assertCannotApprove(self.host_approver, name)

		# Somebody else who holds the role approves it.
		self.assertIn("Approve", self.offered(self.other_approver, name))
		self.act(self.other_approver, name, "Approve")
		row = frappe.db.get_value(
			"Visitor Pass", name, ["workflow_state", "docstatus", "approved_by"], as_dict=True
		)
		self.assertEqual(
			(row.workflow_state, row.docstatus, row.approved_by), ("Approved", 1, self.other_approver)
		)

	def test_host_who_did_not_submit_cannot_approve_either(self):
		"""Reception types the pass and sends it; the host holds the approver role."""
		name = self.new_pass(self.clerk, self.host_employee)
		self.act(self.clerk, name, "Submit")
		self.assertEqual(self.submitter(name), self.clerk)

		self.assertCannotApprove(self.host_approver, name)
		self.act(self.other_approver, name, "Approve")
		self.assertEqual(self.state(name), "Approved")

	def test_submitter_who_is_neither_creator_nor_host_cannot_approve(self):
		name = self.new_pass("Guest", self.plain_host)
		self.act(self.clerk_approver, name, "Submit")
		self.assertEqual(self.submitter(name), self.clerk_approver)

		self.assertCannotApprove(self.clerk_approver, name)
		self.act(self.other_approver, name, "Approve")
		self.assertEqual(self.state(name), "Approved")

	def test_creator_cannot_approve(self):
		"""Frappe's own rule (the owner), kept: somebody else sent it, the creator holds the role."""
		name = self.new_pass(self.clerk_approver, self.plain_host)
		self.act(self.plain_host_user, name, "Submit")
		self.assertCannotApprove(self.clerk_approver, name, refused_by=frappe.ValidationError)
		# Asked directly, the pass gives the same answer as Frappe.
		frappe.set_user(self.clerk_approver)
		try:
			self.assertEqual(frappe.get_doc("Visitor Pass", name)._approval_conflict(), "owner")
		finally:
			frappe.set_user("Administrator")

	def test_approver_with_no_part_in_the_visit_approves_normally(self):
		name = self.new_pass(self.clerk, self.plain_host)
		self.act(self.clerk, name, "Submit")
		self.assertIn("Approve", self.offered(self.other_approver, name))
		self.act(self.other_approver, name, "Approve")
		self.assertEqual(self.state(name), "Approved")

	def test_host_may_still_reject(self):
		"""Declining one's own request is not approving it."""
		name = self.new_pass("Guest", self.host_employee)
		self.act(self.host_approver, name, "Submit")
		self.assertIn("Reject", self.offered(self.host_approver, name))
		self.act(self.host_approver, name, "Reject")
		self.assertEqual(self.state(name), "Rejected")

	def test_legacy_pass_without_a_recorded_submitter(self):
		"""Sent for approval before the field existed: the creator and the host are still known."""
		name = self.new_pass(self.clerk, self.host_employee)
		self.act(self.clerk, name, "Submit")
		# As an earlier version left it: nobody recorded, no copy of the host's login.
		frappe.db.set_value(
			"Visitor Pass",
			name,
			{"submitted_for_approval_by": None, "host_email": None},
			update_modified=False,
		)
		frappe.set_user(self.host_approver)
		try:
			with self.assertRaises(frappe.PermissionError):
				apply_workflow(frappe.get_doc("Visitor Pass", name), "Approve")
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(self.state(name), f"Pending {self.ROLE}")

		self.act(self.other_approver, name, "Approve")
		self.assertEqual(self.state(name), "Approved")

	def test_who_sent_it_cannot_be_written_by_a_request(self):
		name = self.new_pass(self.clerk, self.plain_host)
		# On a draft, by its creator.
		frappe.set_user(self.clerk)
		doc = frappe.get_doc("Visitor Pass", name)
		doc.submitted_for_approval_by = self.other_approver
		doc.save()
		frappe.set_user("Administrator")
		self.assertIsNone(self.submitter(name))

		self.act(self.clerk_approver, name, "Submit")
		self.assertEqual(self.submitter(name), self.clerk_approver)
		# In the lane, by the submitter, who holds the role and so may edit the pass.
		frappe.set_user(self.clerk_approver)
		doc = frappe.get_doc("Visitor Pass", name)
		doc.submitted_for_approval_by = self.other_approver
		doc.vehicle_number = "TN01AB1234"
		doc.save()
		frappe.set_user("Administrator")
		self.assertEqual(self.submitter(name), self.clerk_approver)
		self.assertCannotApprove(self.clerk_approver, name)

	def test_host_cannot_hand_the_visit_to_somebody_else_and_then_approve(self):
		name = self.new_pass(self.clerk, self.host_employee)
		self.act(self.clerk, name, "Submit")

		frappe.set_user(self.host_approver)
		doc = frappe.get_doc("Visitor Pass", name)
		doc.person_to_visit = self.plain_host
		with self.assertRaises(frappe.PermissionError):
			doc.save()
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Visitor Pass", name, "person_to_visit"), self.host_employee)

		# An approver with no part in the visit may correct the host.
		frappe.set_user(self.other_approver)
		doc = frappe.get_doc("Visitor Pass", name)
		doc.person_to_visit = self.plain_host
		doc.save()
		frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Visitor Pass", name, "person_to_visit"), self.plain_host)

	def test_form_tells_the_host_why_there_is_no_approve(self):
		name = self.new_pass("Guest", self.host_employee)
		self.act(self.host_approver, name, "Submit")

		def notice(user):
			frappe.set_user(user)
			try:
				return frappe.get_doc("Visitor Pass", name)._approval_blocked_notice()
			finally:
				frappe.set_user("Administrator")

		self.assertIn("another approver", notice(self.host_approver))
		self.assertIsNone(notice(self.other_approver), "an approver who may approve needs no notice")
		self.assertIsNone(notice(self.clerk), "somebody without the role is not offered Approve anyway")

	def test_a_pass_waits_when_the_only_approver_is_its_host(self):
		"""It is never approved automatically; the message says who has to act."""
		role = f"R3 Lonely Approver {frappe.generate_hash(length=5)}"
		frappe.get_doc({"doctype": "Role", "role_name": role}).insert(ignore_permissions=True)

		def give(user):
			row = frappe.get_doc(
				{
					"doctype": "Has Role",
					"parent": user,
					"parenttype": "User",
					"parentfield": "roles",
					"role": role,
				}
			)
			row.name = frappe.generate_hash(length=10)
			row.db_insert()
			frappe.clear_cache(user=user)

		name = self.new_pass("Guest", self.host_employee)
		self.act(self.host_approver, name, "Submit")
		doc = frappe.get_doc("Visitor Pass", name)

		give(self.host_approver)
		self.assertFalse(doc._has_another_approver(role), "the host is the only holder of the role")
		frappe.set_user(self.host_approver)
		try:
			with patch.object(type(doc), "_lane_role", return_value=role):
				notice = frappe.get_doc("Visitor Pass", name)._approval_blocked_notice()
		finally:
			frappe.set_user("Administrator")
		self.assertIn("not approved automatically", notice)
		self.assertEqual(self.state(name), f"Pending {self.ROLE}")

		give(self.other_approver)
		self.assertTrue(doc._has_another_approver(role))
		frappe.db.set_value("User", self.other_approver, "enabled", 0)
		self.assertFalse(doc._has_another_approver(role), "a disabled user cannot approve")
		frappe.db.set_value("User", self.other_approver, "enabled", 1)


# ─── R3-1: the System Manager lane ───────────────────────────────────────
class TestSystemManagerLane(_ApprovalCase):
	VISITOR_TYPE = "Contractor"
	ROLE = "System Manager"

	def test_system_manager_who_hosts_cannot_approve(self):
		name = self.new_pass("Guest", self.host_employee)
		self.act(self.host_approver, name, "Submit")
		self.assertEqual(self.state(name), "Pending System Manager")
		self.assertCannotApprove(self.host_approver, name)

		self.act(self.other_approver, name, "Approve")
		self.assertEqual(self.state(name), "Approved")


# ─── R3-1: two approvals (VIP) ───────────────────────────────────────────
class TestTwoStepApproval(_ApprovalCase):
	VISITOR_TYPE = "VIP"
	ROLE = "HOD"
	SECOND_ROLE = "CEO"

	def test_host_holding_both_roles_can_take_neither_step(self):
		name = self.new_pass("Guest", self.host_employee)
		self.act(self.host_approver, name, "Submit")
		self.assertEqual(self.state(name), "Pending HOD")

		# First approval.
		self.assertCannotApprove(self.host_approver, name)
		self.act(self.other_approver, name, "Approve")
		self.assertEqual(self.state(name), "Pending CEO")

		# Second approval: the host holds this role as well, and still may not.
		frappe.db.set_value("Visitor Pass", name, "mdceo_notified", 1, update_modified=False)
		self.assertCannotApprove(self.host_approver, name)
		self.act(self.other_second, name, "Approve")
		row = frappe.db.get_value("Visitor Pass", name, ["workflow_state", "docstatus"], as_dict=True)
		self.assertEqual((row.workflow_state, row.docstatus), ("Approved", 1))


# ─── R3-4: the meal-plan look-up ─────────────────────────────────────────
class TestMealPlanLookup(FrappeTestCase):
	CMD = "visitormanagement.visitor_management.lifecycle.get_hospitality_meal_plan"
	IP = "198.51.100.77"  # a documentation address: nobody's real counter

	def setUp(self):
		frappe.set_user("Administrator")
		self._had_request = hasattr(frappe.local, "request")
		self._saved = (
			getattr(frappe.local, "request", None),
			getattr(frappe.local, "request_ip", None),
			frappe.local.form_dict,
		)
		# As a web request to the public endpoint from one network address.
		frappe.local.request = frappe._dict(method="POST", headers={}, path=f"/api/method/{self.CMD}")
		frappe.local.request_ip = self.IP
		frappe.local.form_dict = frappe._dict(cmd=self.CMD)
		self.ARGS = {"visit_date": nowdate(), "expected_checkin": "12:30:00", "expected_checkout": "14:30:00"}
		self.counter = frappe.cache.make_key(f"rl:{self.CMD}:{self.IP}")
		# The app's own counter for the same caller (round 6): it holds the budget
		# whichever API route is used, and would otherwise outlive this test.
		self.own_counter = frappe.cache.make_key(f"vms:portal-meal-plan:{self.IP}")
		frappe.cache.delete(self.counter, self.own_counter)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.cache.delete(self.counter, self.own_counter)
		request, request_ip, form_dict = self._saved
		if self._had_request:
			frappe.local.request = request
		else:
			del frappe.local.request
		frappe.local.request_ip = request_ip
		frappe.local.form_dict = form_dict

	def _count(self):
		return int(frappe.cache.get(self.counter) or 0)

	def _as(self, user):
		"""Switch user inside the same request (frappe.set_user empties form_dict)."""
		frappe.set_user(user)
		frappe.local.form_dict = frappe._dict(cmd=self.CMD)

	def test_guests_keep_their_limit_and_staff_are_not_counted(self):
		staff = _make_user("r3-meal", "Employee")

		# Staff, on the address the visitors will come from: never counted.
		self._as(staff)
		for _i in range(65):
			lifecycle.get_hospitality_meal_plan(**self.ARGS)
		self.assertEqual(self._count(), 0, "a signed-in call used up the anonymous budget")

		# Anonymous callers: 60 an hour for the address, as before.
		self._as("Guest")
		for _i in range(60):
			lifecycle.get_hospitality_meal_plan(**self.ARGS)
		self.assertEqual(self._count(), 60)
		with self.assertRaises(frappe.RateLimitExceededError):
			lifecycle.get_hospitality_meal_plan(**self.ARGS)

		# With the anonymous budget spent, staff on that address still get an answer,
		# from the public endpoint and from the desk form's own method.
		self._as(staff)
		plan = lifecycle.get_hospitality_meal_plan(**self.ARGS)
		self.assertEqual(plan, lifecycle.get_meal_plan_for_pass(**self.ARGS))
		self.assertIn("meal_required", plan)

	def test_desk_method_is_for_people_who_work_with_passes(self):
		self.assertNotIn(lifecycle.get_meal_plan_for_pass, frappe.guest_methods)
		self.assertIn(lifecycle.get_meal_plan_for_pass, frappe.whitelisted)
		self._as("Guest")
		with self.assertRaises(frappe.PermissionError):
			lifecycle.get_meal_plan_for_pass(**self.ARGS)
		self._as(_make_user("r3-outsider", "Blogger"))
		with self.assertRaises(frappe.PermissionError):
			lifecycle.get_meal_plan_for_pass(**self.ARGS)

	def test_desk_form_does_not_call_a_guest_endpoint(self):
		"""Whatever the desk form of an app DocType calls must not be open to (or limited like) guests."""
		import re

		root = frappe.get_app_path("visitormanagement")
		guest_paths = {f"{fn.__module__}.{fn.__name__}" for fn in frappe.guest_methods}
		offenders = []
		for path in glob.glob(os.path.join(root, "*", "doctype", "*", "*.js")) + glob.glob(
			os.path.join(root, "public", "js", "*.js")
		):
			with open(path) as handle:
				source = handle.read()
			for dotted in set(re.findall(r"visitormanagement(?:\.[A-Za-z_][A-Za-z0-9_]*)+", source)):
				if dotted in guest_paths:
					offenders.append(f"{os.path.relpath(path, root)} -> {dotted}")
		self.assertEqual(offenders, [])


# ─── R3-2 / R3-3: the workspaces ─────────────────────────────────────────
class TestWorkspaceBlocks(FrappeTestCase):
	"""Frappe draws a workspace block only when its name equals the LABEL of a row
	of that kind (frappe/public/js/frappe/views/workspace/blocks/block.js make());
	a block that matches nothing is skipped without an error.
	"""

	def _workspaces(self):
		root = frappe.get_app_path("visitormanagement")
		paths = sorted(glob.glob(os.path.join(root, "*", "workspace", "*", "*.json")))
		self.assertGreaterEqual(len(paths), 2)
		for path in paths:
			with open(path) as handle:
				yield json.load(handle)

	@staticmethod
	def _labels(ws):
		# As frappe/desk/desktop.py builds them: the row's label, else the document's name.
		return {
			"chart": {r.get("label") or r.get("chart_name") for r in ws.get("charts") or []},
			"number_card": {
				r.get("label") or r.get("number_card_name") for r in ws.get("number_cards") or []
			},
			"shortcut": {r.get("label") or r.get("link_to") for r in ws.get("shortcuts") or []},
			"card": {r.get("label") for r in ws.get("links") or [] if r.get("type") == "Card Break"},
			"quick_list": {r.get("label") or r.get("document_type") for r in ws.get("quick_lists") or []},
			"custom_block": {
				r.get("label") or r.get("custom_block_name") for r in ws.get("custom_blocks") or []
			},
		}

	def test_every_block_resolves_to_a_row_and_every_row_has_a_block(self):
		for ws in self._workspaces():
			labels = self._labels(ws)
			used = {kind: set() for kind in labels}
			for block in json.loads(ws["content"]):
				kind, data = block["type"], block.get("data") or {}
				if kind in labels:
					name = data.get(f"{kind}_name")
					used[kind].add(name)
					self.assertIn(name, labels[kind], f"{ws['name']}: {kind} block {name!r} matches no row")
				elif kind == "onboarding":
					self.assertTrue(
						frappe.db.exists("Module Onboarding", data.get("onboarding_name")),
						f"{ws['name']}: no Module Onboarding {data.get('onboarding_name')!r}",
					)
			for kind, rows in labels.items():
				self.assertEqual(rows - used[kind], set(), f"{ws['name']}: {kind} rows no block shows")

	def test_every_row_points_at_something_that_exists(self):
		for ws in self._workspaces():
			with self.subTest(workspace=ws["name"]):
				self.assertIn(ws.get("module"), APP_MODULES)
				for row in ws.get("number_cards") or []:
					self.assertTrue(frappe.db.exists("Number Card", row["number_card_name"]), row)
				for row in ws.get("charts") or []:
					self.assertTrue(frappe.db.exists("Dashboard Chart", row["chart_name"]), row)
				for row in ws.get("shortcuts") or []:
					if row.get("type") in ("DocType", "Report", "Page", "Dashboard"):
						self.assertTrue(frappe.db.exists(row["type"], row["link_to"]), row)
				for row in ws.get("links") or []:
					if row.get("type") == "Link" and row.get("link_type") in ("DocType", "Report", "Page"):
						self.assertTrue(frappe.db.exists(row["link_type"], row["link_to"]), row)

	def test_no_donut_or_pie_chart_on_the_workspaces(self):
		"""frappe-charts' redraw of these two raises a console error while a busy workspace lays out."""
		for ws in self._workspaces():
			for row in ws.get("charts") or []:
				chart_type = frappe.db.get_value("Dashboard Chart", row["chart_name"], "type")
				self.assertNotIn(chart_type, ("Donut", "Pie"), row["chart_name"])
