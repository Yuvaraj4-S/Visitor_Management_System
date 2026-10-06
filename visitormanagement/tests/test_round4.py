# See license.txt
"""Round 4 of the 1.1.0 re-test.

Locks in:
  - whatever a workspace of this app shows to a role, that role can load: every
    chart, number card, report and list it is given answers without a permission
    error — for each role the app's records name, held on its own (a guard with
    the Security role and nothing else got "No permission for Visitor Type" on
    the workspace: two charts read the Visitor Type master with a permission
    check that only Employee and System Manager pass);
  - the by-type charts label their axis for such a user while the counts stay
    limited to the passes that user may read;
  - the advance-booking ceiling cannot be off: a stored 0 is "not set".

Run (never without the two skip flags on a working site):

	bench --site <site> run-tests --module visitormanagement.tests.test_round4 \\
		--skip-before-tests --skip-test-records
"""

import json

import frappe
from frappe.desk.desktop import get_desktop_page
from frappe.tests.utils import FrappeTestCase

from visitormanagement.visitor_management import settings as vms_settings
from visitormanagement.visitor_management.dashboard_chart_source.visitor_passes_by_type.visitor_passes_by_type import (
	get_visitor_pass_counts_by_type,
)
from visitormanagement.visitor_management.link_details import APP_MODULES

WORKSPACES = ("Visitor Management", "Conference Rooms")

# The runner walks Link dependencies before setUpClass; nothing here needs them.
test_ignore = ["Employee", "Visitor Type", "Visitor Pass", "User"]


def _single_role_user(role):
	"""A desk user holding `role` and nothing else, rolled back with the test class."""
	email = f"r4-{frappe.scrub(role)}-{frappe.generate_hash(length=6)}@example.com"
	frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": role,
			"send_welcome_email": 0,
			"user_type": "System User",
		}
	).insert(ignore_permissions=True)
	# Written directly: other apps' User hooks drop some roles on save.
	row = frappe.get_doc(
		{"doctype": "Has Role", "parent": email, "parenttype": "User", "parentfield": "roles", "role": role}
	)
	row.name = frappe.generate_hash(length=10)
	row.db_insert()
	# Inserted without a role the user was typed "Website User"; a desk role makes a System User.
	frappe.db.set_value("User", email, "user_type", "System User", update_modified=False)
	frappe.clear_cache(user=email)
	return email


def _roles_the_app_names():
	"""Every role on the app's DocTypes, reports, charts and workspaces."""
	doctypes = frappe.get_all("DocType", filters={"module": ("in", APP_MODULES)}, pluck="name")
	roles = set()
	for table in ("DocPerm", "Custom DocPerm"):
		roles |= set(frappe.get_all(table, filters={"parent": ("in", doctypes)}, pluck="role"))
	named = []
	for doctype in ("Report", "Dashboard Chart"):
		named += frappe.get_all(doctype, filters={"module": ("in", APP_MODULES)}, pluck="name")
	roles |= set(
		frappe.get_all("Has Role", filters={"parent": ("in", named + list(WORKSPACES))}, pluck="role")
	)
	return sorted(roles - {"All", "Guest", "Administrator", "Desk User"})


def _chart_data(chart_name):
	"""What the chart widget requests (frappe/public/js/frappe/widgets/chart_widget.js)."""
	chart = frappe.get_doc("Dashboard Chart", chart_name)
	if chart.chart_type == "Custom":
		source = frappe.get_doc("Dashboard Chart Source", chart.source)
		app = frappe.db.get_value("Module Def", source.module, "app_name")
		name = frappe.scrub(source.name)
		method = f"{app}.{frappe.scrub(source.module)}.dashboard_chart_source.{name}.{name}.get_data"
		return frappe.call(method, chart_name=chart_name, refresh=1)
	from frappe.desk.doctype.dashboard_chart.dashboard_chart import get

	return get(chart_name=chart_name, refresh=1)


def _card_value(card_name):
	"""What the number card widget requests (number_card_widget.js)."""
	card = frappe.get_doc("Number Card", card_name)
	if card.type == "Custom":
		return frappe.call(card.method, filters=card.filters_json)
	from frappe.desk.doctype.number_card.number_card import get_result

	return get_result(doc=card.as_json(), filters=card.filters_json or "[]")


def _report(name):
	from frappe.desk.query_report import run

	today = frappe.utils.nowdate()
	filters = {"date": today, "from_date": frappe.utils.add_days(today, -7), "to_date": today}
	return run(name, filters=filters, ignore_prepared_report=True)


def _list(doctype):
	if frappe.get_meta(doctype).issingle:
		return frappe.get_doc(doctype).check_permission("read")
	return frappe.get_list(doctype, limit_page_length=1)


def _items_shown(workspace):
	"""(label, loader, argument) for everything the desk puts on this workspace for the session user."""
	try:
		page = get_desktop_page(json.dumps({"name": workspace, "title": workspace, "public": 1}))
	except frappe.PermissionError:
		return None  # the workspace itself is not for this role
	items = []
	for chart in (page.get("charts") or {}).get("items") or []:
		items.append((f"chart {chart.get('chart_name')}", _chart_data, chart.get("chart_name")))
	for card in (page.get("number_cards") or {}).get("items") or []:
		items.append(
			(f"number card {card.get('number_card_name')}", _card_value, card.get("number_card_name"))
		)
	links = [(s.get("type"), s.get("link_to")) for s in (page.get("shortcuts") or {}).get("items") or []]
	for card in (page.get("cards") or {}).get("items") or []:
		links += [(link.get("link_type"), link.get("link_to")) for link in card.get("links") or []]
	for kind, target in dict.fromkeys(links):
		if (kind or "").lower() == "report":
			items.append((f"report {target}", _report, target))
		elif (kind or "").lower() == "doctype":
			items.append((f"list {target}", _list, target))
	return items


class TestEveryRoleCanLoadItsWorkspace(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		# Registered before FrappeTestCase adds its rollback, so it runs after it.
		cls.addClassCleanup(frappe.clear_cache)
		super().setUpClass()

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.clear_messages()

	def test_each_role_on_its_own_loads_everything_it_is_shown(self):
		roles = _roles_the_app_names()
		for expected in ("Security", "Employee", "Host Employee", "Hospitality User", "System Manager"):
			self.assertIn(expected, roles)

		loaded = 0
		for role in roles:
			user = _single_role_user(role)
			for workspace in WORKSPACES:
				frappe.set_user(user)
				items = _items_shown(workspace)
				for label, load, argument in items or []:
					with self.subTest(role=role, workspace=workspace, item=label):
						frappe.set_user(user)
						try:
							load(argument)
							loaded += 1
						except frappe.PermissionError as exc:
							self.fail(f"{role} is shown {label} on {workspace} but may not load it: {exc}")
						finally:
							frappe.clear_messages()
				frappe.set_user("Administrator")
		self.assertGreater(loaded, 100, "the workspaces showed almost nothing: the harness is not looking")

	def test_a_guard_with_only_the_security_role_gets_the_by_type_charts(self):
		"""The reported case: both charts list Security, and neither would load for one."""
		active_types = frappe.get_all("Visitor Type", filters={"is_active": 1}, pluck="name")
		if not active_types:
			self.skipTest("no active Visitor Type on this site")
		guard = _single_role_user("Security")
		frappe.set_user(guard)
		self.assertFalse(frappe.has_permission("Visitor Type", "read"), "the master itself stays closed")

		for chart in ("Visitors by Type", "Pending Approvals by Type"):
			_chart_data(chart)  # must not raise

		labels, values = get_visitor_pass_counts_by_type()
		self.assertEqual(sorted(labels), sorted(active_types))
		# The counts are the guard's own view of the passes, not the site's.
		mine = frappe.get_list(
			"Visitor Pass",
			filters={"docstatus": ("<", 2), "visitor_type": ("in", active_types)},
			pluck="name",
			limit_page_length=0,
		)
		self.assertEqual(sum(values), len(mine))
		frappe.set_user("Administrator")
		everyone = frappe.db.count(
			"Visitor Pass", {"docstatus": ("<", 2), "visitor_type": ("in", active_types)}
		)
		self.assertLessEqual(sum(values), everyone)
		# A guard never sees a pass that is still waiting for approval.
		frappe.set_user(guard)
		_labels, pending = get_visitor_pass_counts_by_type(workflow_state_like="Pending%")
		self.assertEqual(sum(pending), 0)

	def test_type_names_are_not_handed_to_someone_who_cannot_read_passes(self):
		frappe.set_user(_single_role_user("Blogger"))
		with self.assertRaises(frappe.PermissionError):
			get_visitor_pass_counts_by_type()


class TestAdvanceBookingCeiling(FrappeTestCase):
	"""The form refuses 0 ("would disable the rule silently"); the reader must agree."""

	def tearDown(self):
		frappe.clear_document_cache("VMS Settings", "VMS Settings")

	def _stored(self, value):
		frappe.db.set_single_value("VMS Settings", "max_advance_booking_days", value)
		frappe.clear_document_cache("VMS Settings", "VMS Settings")
		return vms_settings.max_advance_booking_days()

	def test_a_stored_zero_or_negative_is_not_no_limit(self):
		self.assertEqual(self._stored(45), 45)
		self.assertEqual(self._stored(1), 1)
		for not_a_limit in (0, -5):
			self.assertEqual(self._stored(not_a_limit), vms_settings.DEFAULT_MAX_ADVANCE_DAYS)

	def test_form_and_help_text_say_the_same(self):
		doc = frappe.get_doc("VMS Settings")
		doc.max_advance_booking_days = 0
		with self.assertRaises(frappe.ValidationError):
			doc.save()
		description = frappe.get_meta("VMS Settings").get_field("max_advance_booking_days").description
		self.assertNotIn("0 = no limit", description)
