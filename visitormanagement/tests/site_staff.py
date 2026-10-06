# See license.txt
"""Staff for the tests that look their people up on the site.

Several test modules were written against a site that already had staff: the
host is "any Active Employee", the approver is "the first user holding role X",
and a test that finds none skips itself. On a site without them — a fresh
install, a CI site, this site once its demo data was removed — a quarter of the
suite skipped and still reported OK.

`StaffedTestCase` gives such a site what those look-ups search for: two Active
Employees whose users hold the Employee role, and one user for every role the
app or its Visitor Types name. A record is created only when the site has none
of its kind, so a site with real staff is tested with its real staff as before.
Everything is written inside the test class's transaction, which FrappeTestCase
rolls back when the class is done.

This is a helper, not a test module: its name does not start with `test_`.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from visitormanagement.visitor_management.link_details import APP_MODULES

# Approvers the tests walk a pass through, whether or not a Visitor Type on the
# site names them today.
APPROVER_ROLES = ("System Manager", "HOD", "CEO", "HR Manager", "Sales Manager")
NOT_A_STAFF_ROLE = {"All", "Guest", "Administrator", "Desk User", "Employee"}
EMPLOYEES_NEEDED = 2


def _token():
	return frappe.generate_hash(length=6)


def _user_holding(role):
	"""An enabled user holding `role`, if the site has one."""
	rows = frappe.db.sql(
		"""SELECT u.name FROM `tabUser` u
		JOIN `tabHas Role` h ON h.parent = u.name AND h.parenttype = 'User'
		WHERE h.role = %(role)s AND u.enabled = 1
		ORDER BY u.creation
		LIMIT 1""",
		{"role": role},
	)
	return rows[0][0] if rows else None


def _make_user(label, role):
	email = f"staff-{frappe.scrub(label)}-{_token()}@vms-test.example.com"
	frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": f"Staff {label}",
			"send_welcome_email": 0,
			"enabled": 1,
			"user_type": "System User",
		}
	).insert(ignore_permissions=True)
	if not frappe.db.exists("Role", role):
		frappe.get_doc({"doctype": "Role", "role_name": role}).insert(ignore_permissions=True)
	# Written directly, not with `user.add_roles`: other apps' User hooks drop
	# roles on save (ERPNext removes "Employee" from a user who has no Employee
	# record yet, and the Employee is created after the user).
	row = frappe.get_doc(
		{"doctype": "Has Role", "parent": email, "parenttype": "User", "parentfield": "roles", "role": role}
	)
	row.name = frappe.generate_hash(length=10)
	row.db_insert()
	# Inserted without a role the user was typed "Website User".
	frappe.db.set_value("User", email, "user_type", "System User", update_modified=False)
	frappe.clear_cache(user=email)
	return email


def _make_employee(user, label):
	"""An Active Employee linked to `user`, written straight to the table.

	The tests need the row to exist, be Active and name its user. Employee's own
	validation would need a holiday list, a naming series and more of ERPNext
	than these tests have any business creating.
	"""
	company = frappe.db.get_value("Company", {}, "name", order_by="creation")
	employee = frappe.new_doc("Employee")
	employee.update(
		{
			"first_name": "Staff",
			"last_name": label,
			"employee_name": f"Staff {label}",
			"status": "Active",
			"user_id": user,
			"company": company,
			"department": frappe.db.get_value("Department", {"company": company, "is_group": 0}, "name")
			if company
			else None,
		}
	)
	employee.name = f"ZZ-STAFF-{_token().upper()}"
	employee.employee = employee.name
	employee.db_insert()
	return employee.name


def _roles_to_staff():
	doctypes = frappe.get_all("DocType", filters={"module": ("in", APP_MODULES)}, pluck="name")
	roles = set(APPROVER_ROLES)
	for table in ("DocPerm", "Custom DocPerm"):
		roles |= set(frappe.get_all(table, filters={"parent": ("in", doctypes)}, pluck="role"))
	for row in frappe.get_all("Visitor Type", fields=["approver_role", "secondary_approver_role"]):
		roles |= {row.approver_role, row.secondary_approver_role}
	return sorted(role for role in roles if role and role not in NOT_A_STAFF_ROLE)


def ensure_site_staff():
	"""Create whatever staff the site lacks. Returns what was created, for a test to read."""
	created = {"employees": [], "users": []}

	missing = EMPLOYEES_NEEDED - frappe.db.count("Employee", {"status": "Active"})
	for number in range(max(missing, 0)):
		label = "Host One" if number == 0 else f"Host {number + 1}"
		user = _make_user(label, "Employee")
		created["users"].append(user)
		created["employees"].append(_make_employee(user, label))
	if not _user_holding("Employee"):
		# The site has Employees, but nobody who can sign in as one. A real
		# Employee is never touched: this adds one of the tests' own.
		user = _make_user("Host", "Employee")
		created["users"].append(user)
		created["employees"].append(_make_employee(user, "Host"))

	for role in _roles_to_staff():
		if not _user_holding(role):
			created["users"].append(_make_user(role, role))
	return created


def remove_site_staff(created):
	"""Delete staff of `ensure_site_staff` that outlived the class's rollback.

	Normally there is nothing to do: the rollback took them. They survive only
	when something committed while the class ran, and then they would stay on
	the site as users nobody created. Returns what had to be deleted.
	"""
	employees = [name for name in created.get("employees", []) if frappe.db.exists("Employee", name)]
	users = [name for name in created.get("users", []) if frappe.db.exists("User", name)]
	for name in employees:
		frappe.db.delete("Employee", {"name": name})
	for name in users:
		# Frappe gives every user a Contact, and User.on_trash only unlinks it.
		for contact in frappe.get_all("Contact", filters={"user": name}, pluck="name"):
			frappe.delete_doc(
				"Contact", contact, force=True, ignore_permissions=True, delete_permanently=True
			)
		# First what User.on_trash would otherwise move to Deleted Document.
		frappe.db.delete("Notification Settings", {"name": name})
		frappe.delete_doc("User", name, force=True, ignore_permissions=True, delete_permanently=True)
		frappe.db.delete("DefaultValue", {"parent": name})
		frappe.db.delete("Has Role", {"parent": name, "parenttype": "User"})
		frappe.clear_cache(user=name)
	if employees or users:
		frappe.db.commit()  # these rows were committed by the code under test; nothing else is pending
	return employees + users


class StaffedTestCase(FrappeTestCase):
	"""A FrappeTestCase whose site has the staff the tests look up (see the module text)."""

	site_staff_created = None

	@classmethod
	def setUpClass(cls):
		# Registered before FrappeTestCase adds its rollback, so it runs after it.
		cls.addClassCleanup(cls._remove_staff_that_was_committed)
		super().setUpClass()
		cls.site_staff_created = ensure_site_staff()

	@classmethod
	def _remove_staff_that_was_committed(cls):
		removed = remove_site_staff(cls.site_staff_created or {})
		if removed:
			print(
				f"\n{cls.__module__}.{cls.__name__}: a commit kept {len(removed)} test staff records; removed"
			)
