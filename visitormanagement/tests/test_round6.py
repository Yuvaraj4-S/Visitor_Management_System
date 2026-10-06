# See license.txt
"""Round 6 of the 1.1.0 re-test (VAPT RL-1).

Locks in:
  - an anonymous caller of the meal preview gets 60 answers an hour however the
    call is addressed. Frappe's `@rate_limit` keys on `frappe.form_dict.cmd`
    (frappe/rate_limiter.py), which is the method on `/api/method/<method>` and
    absent on `/api/v2/method/<method>` (frappe/api/v2.py), so the same caller
    had a second budget on the second route;
  - a signed-in caller is still never counted;
  - the counter is this site's own (prefixed through `make_key`), expires within
    the hour, and identifies the caller the way the portal's other limits do;
  - a guest endpoint has one public name: a module that imports one by name
    makes it callable under that module's path too, which is one more budget
    in Frappe's limiter.

Run (never without the two skip flags on a working site):

	bench --site <site> run-tests --module visitormanagement.tests.test_round6 \\
		--skip-before-tests --skip-test-records
"""

import ast
import glob
import os
import sys
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate

from visitormanagement.tests import site_staff
from visitormanagement.tests.test_fix_a import _make_user
from visitormanagement.visitor_management import lifecycle, portal, portal_upload
from visitormanagement.visitor_management.doctype.visitor_invitation import (
	visitor_invitation as invitation_module,
)
from visitormanagement.visitor_management.web_form.visitor_pre_registration_form import (
	visitor_pre_registration_form as web_form_module,
)

# The runner walks Link dependencies before setUpClass; nothing here needs them.
test_ignore = ["Employee", "Visitor Type", "Visitor Pass", "User"]

CMD = "visitormanagement.visitor_management.lifecycle.get_hospitality_meal_plan"
LIMIT = lifecycle.GUEST_MEAL_PREVIEWS_PER_HOUR


class TestGuestMealBudget(FrappeTestCase):
	IP = "198.51.100.86"  # a documentation address: nobody's real counter

	def setUp(self):
		frappe.set_user("Administrator")
		self._had_request = hasattr(frappe.local, "request")
		self._saved = (
			getattr(frappe.local, "request", None),
			getattr(frappe.local, "request_ip", None),
			frappe.local.form_dict,
		)
		# As a web request from one network address (no socket peer: a local proxy).
		frappe.local.request = frappe._dict(method="GET", headers={}, path="/api/v2/method/" + CMD)
		frappe.local.request_ip = self.IP
		self.ARGS = {"visit_date": nowdate(), "expected_checkin": "12:30:00", "expected_checkout": "14:30:00"}
		self.own_key = f"vms:portal-meal-plan:{self.IP}"
		self._forget()

	def tearDown(self):
		frappe.set_user("Administrator")
		self._forget()
		request, request_ip, form_dict = self._saved
		if self._had_request:
			frappe.local.request = request
		else:
			del frappe.local.request
		frappe.local.request_ip = request_ip
		frappe.local.form_dict = form_dict

	def _forget(self):
		"""Drop every counter this test's address can have, the app's and Frappe's."""
		keys = [frappe.cache.make_key(self.own_key), frappe.cache.make_key("vms:portal-meal-plan:peer-x")]
		keys += list(frappe.cache.scan_iter(frappe.cache.make_key(f"rl:*:{self.IP}")))
		frappe.cache.delete(*keys)

	def _frappe_count(self, cmd):
		"""Frappe's own bucket for calls addressed as `cmd` (frappe/rate_limiter.py)."""
		return int(frappe.cache.get(frappe.cache.make_key(f"rl:{cmd}:{self.IP}")) or 0)

	def _own_count(self):
		return portal_upload.events_counted(self.own_key)

	def _call(self, user, **form_dict):
		"""One call as `user`, addressed the way `form_dict` says (frappe.set_user empties it)."""
		if frappe.session.user != user:
			frappe.set_user(user)
		frappe.local.form_dict = frappe._dict(form_dict)
		return lifecycle.get_hospitality_meal_plan(**self.ARGS)

	def test_budget_does_not_depend_on_how_the_call_is_addressed(self):
		third = LIMIT // 3
		for _i in range(third):
			self._call("Guest", cmd=CMD)  # /api/method/<method>
		for _i in range(third):
			self._call("Guest")  # /api/v2/method/<method>: no cmd at all
		for i in range(LIMIT - 2 * third):
			self._call("Guest", cmd=f"some.other.name{i}")  # any other spelling
		self.assertEqual(self._own_count(), LIMIT)

		# Frappe's limiter saw three callers, none of them near the ceiling: on
		# its own it would go on answering.
		self.assertEqual(self._frappe_count(CMD), third)
		self.assertEqual(self._frappe_count(None), third)
		self.assertLess(max(self._frappe_count(CMD), self._frappe_count(None)), LIMIT)

		for form_dict in ({}, {"cmd": CMD}, {"cmd": "yet.another.name"}):
			with self.assertRaises(frappe.RateLimitExceededError):
				self._call("Guest", **form_dict)
		self.assertEqual(frappe.RateLimitExceededError.http_status_code, 429)
		# A refused call did not reach Frappe's limiter or the preview.
		self.assertEqual(self._frappe_count(CMD), third)
		self.assertEqual(self._frappe_count(None), third)

	def test_signed_in_callers_are_not_counted(self):
		staff = _make_user("r6-meal", "Employee")
		for _i in range(LIMIT + 5):
			self._call(staff)
		self.assertEqual(self._own_count(), 0, "a signed-in call used up the anonymous budget")
		self.assertEqual(self._frappe_count(None), 0)

		# The anonymous budget of that address, spent...
		for _i in range(LIMIT):
			self._call("Guest")
		with self.assertRaises(frappe.RateLimitExceededError):
			self._call("Guest")
		# ...does not touch staff on it.
		self.assertIn("meal_required", self._call(staff))
		self.assertEqual(self._own_count(), LIMIT + 1)

	def test_counter_belongs_to_this_site_and_expires(self):
		self._call("Guest")
		raw = frappe.cache.make_key(self.own_key)
		raw_text = raw.decode() if isinstance(raw, bytes) else raw
		self.assertEqual(raw_text, f"{frappe.conf.db_name}|{self.own_key}")
		# `get` and `ttl` are raw redis calls: they see exactly the key given.
		self.assertEqual(int(frappe.cache.get(raw)), 1)
		self.assertIsNone(frappe.cache.get(self.own_key), "the counter is stored without the site prefix")
		self.assertTrue(0 < frappe.cache.ttl(raw) <= 60 * 60)

		# The window starts with the first call; later calls do not push it out.
		frappe.cache.expire(raw, 100)
		self._call("Guest")
		self.assertTrue(0 < frappe.cache.ttl(raw) <= 100)

	def test_caller_is_identified_as_for_the_other_portal_limits(self):
		"""The portal's identity helper decides who is calling, not the forwarded header alone."""
		with patch.object(lifecycle, "rate_limit_identity", return_value="peer-x") as identity:
			self._call("Guest")
			self._call("Guest")
		self.assertEqual(identity.call_count, 2)
		self.assertEqual(portal_upload.events_counted("vms:portal-meal-plan:peer-x"), 2)
		self.assertEqual(self._own_count(), 0)
		self.assertIs(lifecycle.rate_limit_identity, portal_upload.rate_limit_identity)

	def test_a_call_that_is_not_a_web_request_is_not_counted(self):
		del frappe.local.request
		try:
			self._call("Guest")
		finally:
			frappe.local.request = frappe._dict(method="GET", headers={}, path="/api/v2/method/" + CMD)
		self.assertEqual(self._own_count(), 0)


class TestSiteStaff(site_staff.StaffedTestCase):
	"""The tests that take "any Active Employee" and "the first user holding role X"
	from the site used to skip on a site that has neither, and the run still said OK."""

	def test_a_site_without_staff_gets_what_the_tests_look_up(self):
		self.assertGreaterEqual(
			frappe.db.count("Employee", {"status": "Active"}), site_staff.EMPLOYEES_NEEDED
		)
		employee_user = site_staff._user_holding("Employee")
		self.assertTrue(employee_user)
		for role in site_staff._roles_to_staff():
			self.assertTrue(site_staff._user_holding(role), f"nobody holds {role}")
		for role in site_staff.APPROVER_ROLES:
			self.assertIn(role, site_staff._roles_to_staff())
		# Whatever the site lacked has been made; asking again makes nothing.
		self.assertEqual(site_staff.ensure_site_staff(), {"employees": [], "users": []})

	def test_only_the_tests_own_staff_can_be_removed(self):
		created = self.site_staff_created
		for user in created["users"]:
			self.assertRegex(user, r"^staff-.+-[0-9a-f]{6}@vms-test\.example\.com$")
			self.assertEqual(frappe.db.get_value("User", user, "user_type"), "System User")
		for employee in created["employees"]:
			self.assertRegex(employee, r"^ZZ-STAFF-[0-9A-F]{6}$")
			self.assertIn(frappe.db.get_value("Employee", employee, "user_id"), created["users"])
		# Names that are not on the site: nothing is deleted and nothing is committed.
		self.assertEqual(
			site_staff.remove_site_staff(
				{"users": ["nobody@vms-test.example.com"], "employees": ["ZZ-STAFF-NOBODY"]}
			),
			[],
		)


def _module_level_statements(body):
	"""Statements that bind names of the module itself: not those inside a def or a class."""
	for node in body:
		yield node
		if isinstance(node, ast.If | ast.Try | ast.With):
			for part in ("body", "orelse", "finalbody"):
				yield from _module_level_statements(getattr(node, part, []))
			for handler in getattr(node, "handlers", []):
				yield from _module_level_statements(handler.body)


class TestGuestEndpointsHaveOnePublicName(FrappeTestCase):
	"""`frappe.get_attr` resolves any attribute of any importable module, and the
	whitelist holds function objects, not paths (frappe/__init__.py get_attr,
	is_whitelisted). A guest endpoint imported by name into another module — a
	test module included — is therefore callable as `<that module>.<name>`, and
	Frappe's rate limiter gives every such path a budget of its own."""

	def _guest_endpoints(self):
		return {
			fn.__name__: fn for fn in frappe.guest_methods if fn.__module__.startswith("visitormanagement.")
		}

	def test_the_app_has_these_three_guest_endpoints(self):
		endpoints = self._guest_endpoints()
		self.assertEqual(
			sorted(f"{fn.__module__}.{name}" for name, fn in endpoints.items()),
			sorted(
				[
					f"{lifecycle.__name__}.get_hospitality_meal_plan",
					f"{invitation_module.__name__}.get_web_form_context",
					f"{portal.__name__}.submit_pre_registration",
				]
			),
		)

	def test_no_module_binds_one_under_a_second_name(self):
		endpoints = self._guest_endpoints()
		root = frappe.get_app_path("visitormanagement")
		offenders = []
		for path in glob.glob(os.path.join(root, "**", "*.py"), recursive=True):
			relative = os.path.relpath(path, root)
			module = "visitormanagement." + relative[:-3].replace(os.sep, ".")
			with open(path, encoding="utf-8") as handle:
				tree = ast.parse(handle.read(), filename=path)
			for node in _module_level_statements(tree.body):
				names = []
				if isinstance(node, ast.ImportFrom):
					names = [alias.name for alias in node.names]
				elif isinstance(node, ast.Assign | ast.AnnAssign) and node.value is not None:
					value = node.value
					names = [getattr(value, "attr", None) or getattr(value, "id", None)]
				for name in names:
					if name in endpoints and module != endpoints[name].__module__:
						offenders.append(f"{relative}:{node.lineno} binds {name}")
		self.assertEqual(offenders, [])

	def test_no_loaded_module_holds_one_under_a_second_name(self):
		endpoints = list(self._guest_endpoints().values())
		offenders = []
		for module_name, module in list(sys.modules.items()):
			if not module_name.startswith("visitormanagement.") or module is None:
				continue
			for name, value in list(vars(module).items()):
				if any(value is fn for fn in endpoints) and value.__module__ != module_name:
					offenders.append(f"{module_name}.{name}")
		self.assertEqual(offenders, [])

	def test_the_page_render_uses_the_plain_function(self):
		"""The web form page reads the invitation without the endpoint's wrappers — and
		that plain function is not itself callable over HTTP."""
		plain = web_form_module._load_invitation_context
		self.assertNotIn(plain, frappe.whitelisted)
		self.assertNotIn(plain, frappe.guest_methods)
		self.assertIn(invitation_module.get_web_form_context, frappe.guest_methods)
		self.assertFalse(hasattr(web_form_module, "get_web_form_context"))
