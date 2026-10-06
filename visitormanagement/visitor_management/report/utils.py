# For license information, please see license.txt
"""Shared pieces of the Visitor Management script reports.

Two things every report here must get right, and both went wrong before:

1. Who sees which rows. A script report returns whatever its code selects, so
   the row scope has to be applied in the report. The reports used to rebuild
   it by hand from the app's `permission_query_conditions` hook only, which
   left out the caller's User Permissions and shares. `visitor_pass_rows`
   reads through `frappe.get_list`, so a report shows exactly the passes the
   Visitor Pass list shows the same user; `list_scope` gives the same
   conditions as SQL for the reports that must select with SQL.

2. What a column is. Frappe filters a report's rows by the caller's User
   Permissions on every DocType a `Link` column points at, and refuses the
   report outright if the caller cannot read that DocType
   (frappe/desk/query_report.py get_filtered_data -> get_user_match_filters ->
   DatabaseQuery.build_match_conditions). A "Host" column linking to Employee
   therefore emptied the gate reports for anyone HRMS had restricted to their
   own Employee record, and failed with "No permission to read Employee" for a
   guard. People from other apps' DocTypes are shown by name, as Data
   (`employee_names`); a Link column to one of this app's own DocTypes is used
   only for a viewer who can read it (`link_or_data`).
"""

import frappe

GATE_MOVEMENTS = ("Check-In", "Check-Out")
_BATCH = 1000


def visitor_pass_rows(filters, fields, order_by="modified desc", limit=0):
	"""Visitor Pass rows the caller may see — the list view's own scope.

	`frappe.get_list` applies role permissions, the app's row conditions
	(permissions.get_visitor_pass_permission_query_conditions), the caller's
	User Permissions and shares, and drops fields the caller may not read.
	"""
	return frappe.get_list(
		"Visitor Pass",
		filters=filters,
		fields=fields,
		order_by=order_by,
		limit_page_length=limit,
	)


def list_scope(doctype):
	"""The caller's list-view conditions for `doctype` as SQL, or "".

	Written against the real table name (`` `tab<DocType>` ``), so the query
	using it must not alias that table. `%` is doubled: pass the result only to
	a `frappe.db.sql` call that is given a non-empty values dict.
	"""
	from frappe.desk.reportview import build_match_conditions

	return build_match_conditions(doctype) or ""


def link_or_data(doctype, **column):
	"""A report column that links to `doctype` only for a viewer who can read it.

	For anyone else it is plain Data: the value is shown, nothing can be opened,
	and Frappe has no Link to check the viewer's permission against.
	"""
	if frappe.has_permission(doctype, "read"):
		return {"fieldtype": "Link", "options": doctype, **column}
	return {"fieldtype": "Data", **column}


def employee_names(employees):
	"""{Employee ID: employee_name} for display, read without role permissions.

	The same disclosure as the app's own pickers (link_details.link_query): a
	name, to someone who is already allowed to see the record it appears on.
	"""
	ids = sorted({e for e in employees if e})
	names = {}
	for start in range(0, len(ids), _BATCH):
		for row in frappe.get_all(
			"Employee",
			filters={"name": ("in", ids[start : start + _BATCH])},
			fields=["name", "employee_name"],
		):
			names[row.name] = row.employee_name
	return names


def fill_host(rows):
	"""Set `host` on each row: the name stored on the pass, else the Employee's.

	The Employee ID (`person_to_visit`) is removed from the row: it is not a
	column, and the row dict is sent to the browser as it is.
	"""
	missing = [r.get("person_to_visit") for r in rows if not r.get("host_name")]
	looked_up = employee_names(missing) if any(missing) else {}
	for row in rows:
		employee = row.pop("person_to_visit", None)
		row["host"] = row.pop("host_name", None) or looked_up.get(employee) or ""
	return rows


def gate_movements(pass_names):
	"""Per pass: its gate movements, summarised from the Security Log.

	    first_in        earliest check-in
	    last_in         latest check-in
	    last_in_gate    gate of the latest check-in
	    last_out        latest check-out
	    entries         number of check-ins

	Only times and gate names are read, and only for the passes given — which
	the caller got from `visitor_pass_rows`, so they are passes it may see. One
	summary per pass, however many times the visitor went in and out; joining
	the log straight onto the pass produced one row per check-in x check-out.
	"""
	summary = {}
	names = list(pass_names)
	for start in range(0, len(names), _BATCH):
		batch = tuple(names[start : start + _BATCH])
		if not batch:
			continue
		for log in frappe.db.sql(
			"""
			SELECT visitor_pass, event_type, gate_name, check_in_date_time, check_out_date_time
			FROM `tabSecurity Log`
			WHERE visitor_pass IN %(names)s AND event_type IN %(movements)s
			""",
			{"names": batch, "movements": GATE_MOVEMENTS},
			as_dict=True,
		):
			entry = summary.setdefault(
				log.visitor_pass,
				frappe._dict(first_in=None, last_in=None, last_in_gate=None, last_out=None, entries=0),
			)
			if log.event_type == "Check-In":
				entry.entries += 1
				moment = log.check_in_date_time
				if moment and (not entry.first_in or moment < entry.first_in):
					entry.first_in = moment
				if moment and (not entry.last_in or moment >= entry.last_in):
					entry.last_in = moment
					entry.last_in_gate = log.gate_name
				elif not entry.last_in_gate:
					entry.last_in_gate = log.gate_name
			else:
				moment = log.check_out_date_time
				if moment and (not entry.last_out or moment > entry.last_out):
					entry.last_out = moment
	return summary
