"""Upgrade steps for the gate: Security Log, gate API, reports.

Run by `upgrades.run_all()` on install and on every migrate, so everything here
is idempotent.
"""

import frappe


def run():
	_backfill_security_log_names()


# Each entry: (what is filled, the columns it needs, how many rows are waiting,
# the statement that fills them). Fixed statements — nothing in them comes from
# data or from a caller. Every table is aliased and every column qualified:
# Security Log and Visitor Pass both have a `host_name`.
_NAME_BACKFILLS = (
	(
		"security_officer_name from Employee",
		("security_officer", "security_officer_name"),
		"""
		SELECT COUNT(*)
		FROM `tabSecurity Log` sl
		JOIN `tabEmployee` emp ON emp.name = sl.security_officer
		WHERE IFNULL(sl.security_officer_name, '') = '' AND IFNULL(emp.employee_name, '') != ''
		""",
		"""
		UPDATE `tabSecurity Log` sl
		JOIN `tabEmployee` emp ON emp.name = sl.security_officer
		SET sl.security_officer_name = emp.employee_name
		WHERE IFNULL(sl.security_officer_name, '') = '' AND IFNULL(emp.employee_name, '') != ''
		""",
	),
	(
		"host_name from Employee",
		("person_to_visit", "host_name"),
		"""
		SELECT COUNT(*)
		FROM `tabSecurity Log` sl
		JOIN `tabEmployee` emp ON emp.name = sl.person_to_visit
		WHERE IFNULL(sl.host_name, '') = '' AND IFNULL(emp.employee_name, '') != ''
		""",
		"""
		UPDATE `tabSecurity Log` sl
		JOIN `tabEmployee` emp ON emp.name = sl.person_to_visit
		SET sl.host_name = emp.employee_name
		WHERE IFNULL(sl.host_name, '') = '' AND IFNULL(emp.employee_name, '') != ''
		""",
	),
	(
		# A log whose own host link is empty (older rows did not always copy it)
		# takes the host's name from its pass.
		"host_name from the pass",
		("visitor_pass", "host_name"),
		"""
		SELECT COUNT(*)
		FROM `tabSecurity Log` sl
		JOIN `tabVisitor Pass` vp ON vp.name = sl.visitor_pass
		WHERE IFNULL(sl.host_name, '') = '' AND IFNULL(vp.host_name, '') != ''
		""",
		"""
		UPDATE `tabSecurity Log` sl
		JOIN `tabVisitor Pass` vp ON vp.name = sl.visitor_pass
		SET sl.host_name = vp.host_name
		WHERE IFNULL(sl.host_name, '') = '' AND IFNULL(vp.host_name, '') != ''
		""",
	),
)


def _backfill_security_log_names():
	"""Fill the display names on Security Logs recorded before the fields existed.

	A Security Log links to Employee twice — the officer who recorded it and the
	host being visited — and the people who read the log cannot read Employee,
	so they saw "HR-EMP-00012". New logs store the names when they are recorded
	(SecurityLog.before_save); this fills them on older rows.

	Only empty names are filled, so a second run finds nothing to do. `modified`
	is left alone: this derives a column, it is not an edit of the gate record.
	"""
	if not frappe.db.table_exists("Security Log"):
		return
	columns = set(frappe.db.get_table_columns("Security Log"))
	if not frappe.db.has_column("Visitor Pass", "host_name"):
		columns.discard("visitor_pass")

	for label, needed, count_sql, update_sql in _NAME_BACKFILLS:
		if not set(needed) <= columns:
			continue
		pending = frappe.db.sql(count_sql)[0][0]
		if not pending:
			continue
		frappe.db.sql(update_sql)
		print(f"  Security Log: filled {label} on {pending} existing row(s)")
