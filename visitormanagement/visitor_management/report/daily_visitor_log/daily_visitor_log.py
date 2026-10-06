# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import add_days, get_datetime, today

MAX_ROWS = 5000


def execute(filters=None):
	filters = _validate_filters(filters)
	columns = get_columns()
	data, truncated = get_data(filters)
	summary = get_summary(data)
	chart = get_chart(data)
	message = None
	if truncated:
		message = (
			f"Showing the first {MAX_ROWS:,} matching passes for this date range. "
			"Narrow the From Date / To Date filters to see the rest."
		)
	return columns, data, message, chart, summary


def _validate_filters(filters):
	"""`from_date`/`to_date` are marked `reqd: 1` in daily_visitor_log.js, but that
	only guards the filter form — `execute()` can be called directly (bench console,
	another report, a scheduled job) with an empty dict, and without a boundary here
	it would scan every Visitor Pass ever recorded. Default to the last 7 days so
	there is no code path left that runs unbounded.
	"""
	filters = dict(filters or {})
	if not filters.get("from_date"):
		filters["from_date"] = add_days(today(), -6)
	if not filters.get("to_date"):
		filters["to_date"] = today()
	return filters


def get_columns():
	return [
		{
			"label": _("Pass ID"),
			"fieldname": "visitor_pass",
			"fieldtype": "Link",
			"options": "Visitor Pass",
			"width": 140,
		},
		{"label": _("Visit Date"), "fieldname": "visit_date", "fieldtype": "Date", "width": 105},
		{"label": _("Visitor"), "fieldname": "visitor_name", "fieldtype": "Data", "width": 180},
		{"label": _("Type"), "fieldname": "visitor_type", "fieldtype": "Data", "width": 100},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Data", "width": 160},
		# The host's name as text, not a Link to Employee: Frappe drops every report
		# row whose Link value falls outside the user's User Permissions
		# (frappe/desk/query_report.py:get_filtered_data), which emptied this log for
		# a guard restricted to his own Employee record while the totals above it
		# still counted the visits. `_visitor_pass_scope` decides who sees a row.
		{"label": _("Host"), "fieldname": "host_name", "fieldtype": "Data", "width": 160},
		{"label": _("Purpose"), "fieldname": "purpose_of_visit", "fieldtype": "Small Text", "width": 220},
		{"label": _("Checked-In"), "fieldname": "checkin", "fieldtype": "Datetime", "width": 155},
		{"label": _("Checked-Out"), "fieldname": "checkout", "fieldtype": "Datetime", "width": 155},
		{"label": _("Gate"), "fieldname": "gate_name", "fieldtype": "Data", "width": 110},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 115},
	]


def get_data(filters):
	# from_date/to_date are guaranteed present by _validate_filters() — always
	# bound the scan, never an optional condition.
	conditions = [
		"vp.visit_date >= %(from_date)s",
		"vp.visit_date <= %(to_date)s",
	]
	values = {
		"from_date": filters["from_date"],
		"to_date": filters["to_date"],
		"row_limit": MAX_ROWS + 1,
	}

	if filters.get("visitor_type"):
		conditions.append("vp.visitor_type = %(visitor_type)s")
		values["visitor_type"] = filters["visitor_type"]

	if filters.get("status"):
		conditions.append("vp.status = %(status)s")
		values["status"] = filters["status"]

	if filters.get("host"):
		conditions.append("vp.person_to_visit = %(host)s")
		values["host"] = filters["host"]

	scope = _visitor_pass_scope("vp")
	if scope:
		conditions.append(scope)

	where = "WHERE " + " AND ".join(conditions)

	passes = frappe.db.sql(
		"""
        SELECT
            vp.name AS visitor_pass,
            vp.visit_date,
            vp.visitor_full_name AS visitor_name,
            vp.visitor_type,
            vp.company__organisation AS company,
            vp.host_name,
            vp.purpose_of_visit,
            vp.status
        FROM `tabVisitor Pass` vp
        """
		+ where
		+ """
        ORDER BY vp.visit_date DESC, vp.name DESC
        LIMIT %(row_limit)s
        """,
		values,
		as_dict=True,
	)

	truncated = len(passes) > MAX_ROWS
	if truncated:
		passes = passes[:MAX_ROWS]

	entries = _gate_entries([p.visitor_pass for p in passes])

	# One row per entry through the gate, not per pair of logs. Joining every
	# Check-In with every Check-Out gave a visitor who came in twice four rows,
	# two of them showing a check-out earlier than its check-in. A pass nobody
	# has used yet still gets its one row, with the gate columns empty.
	rows = []
	for visitor_pass in passes:
		for entry in entries.get(visitor_pass.visitor_pass) or [{}]:
			rows.append(
				frappe._dict(
					visitor_pass,
					checkin=entry.get("checkin"),
					checkout=entry.get("checkout"),
					gate_name=entry.get("gate_name"),
				)
			)

	# Newest first, as before: by visit date, then by time of entry. Both sorts are
	# stable, so passes not yet used keep their place after the day's entries.
	rows.sort(key=lambda r: r.checkin or get_datetime("1900-01-01"), reverse=True)
	rows.sort(key=lambda r: get_datetime(r.visit_date), reverse=True)
	return rows, truncated


def _gate_entries(pass_names):
	"""{pass: [{checkin, checkout, gate_name}]} — each Check-In with the Check-Out that closed it.

	The logs of a pass are walked in the order they happened: a Check-In opens an
	entry and the next Check-Out closes it. A Check-Out with no open entry before
	it (a log entered out of order) is not attached to anything.
	"""
	entries = {}
	for start in range(0, len(pass_names), 1000):
		logs = frappe.db.sql(
			"""
            SELECT
                sl.visitor_pass,
                sl.event_type,
                sl.gate_name,
                COALESCE(
                    CASE
                        WHEN sl.event_type = 'Check-In' THEN sl.check_in_date_time
                        ELSE sl.check_out_date_time
                    END,
                    sl.creation
                ) AS event_time
            FROM `tabSecurity Log` sl
            WHERE sl.visitor_pass IN %(names)s
                AND sl.event_type IN ('Check-In', 'Check-Out')
            ORDER BY sl.visitor_pass, event_time, sl.creation
            """,
			{"names": pass_names[start : start + 1000]},
			as_dict=True,
		)
		for log in logs:
			pass_entries = entries.setdefault(log.visitor_pass, [])
			if log.event_type == "Check-In":
				pass_entries.append({"checkin": log.event_time, "checkout": None, "gate_name": log.gate_name})
			elif pass_entries and not pass_entries[-1]["checkout"]:
				pass_entries[-1]["checkout"] = log.event_time
	return entries


def _visits(data):
	"""The rows of `data` reduced to one per pass: a re-entry is the same visit."""
	return list({row.visitor_pass: row for row in data}.values())


def get_summary(data):
	visits = _visits(data)
	total = len(visits)
	checked_in = sum(1 for r in visits if r.status == "Checked-In")
	checked_out = sum(1 for r in visits if r.status == "Checked-Out")
	approved = sum(1 for r in visits if r.status == "Approved")

	return [
		{"value": total, "label": _("Total Visits"), "indicator": "Blue"},
		{"value": checked_in, "label": _("Currently Inside"), "indicator": "Green"},
		{"value": checked_out, "label": _("Completed Visits"), "indicator": "Grey"},
		{"value": approved, "label": _("Awaiting Check-In"), "indicator": "Orange"},
	]


def get_chart(data):
	by_type = {}
	for row in _visits(data):
		by_type[row.visitor_type] = by_type.get(row.visitor_type, 0) + 1
	if not by_type:
		return None
	return {
		"type": "donut",
		"data": {
			"labels": list(by_type.keys()),
			"datasets": [{"name": "Visits", "values": list(by_type.values())}],
		},
	}


def _visitor_pass_scope(alias="vp"):
	"""The caller's Visitor Pass row scope, as a SQL fragment for `alias`.

	Script reports build their rows with raw SQL, which bypasses
	`permission_query_conditions` entirely — so the row filter the list view
	applies has to be re-applied here by hand. Without it the report is a way to
	read every visitor's ID proof regardless of who you are; the roles on the
	report are the only thing standing in the way, and those are one JSON edit
	from changing.

	The shared helper writes conditions against the real table name, so they are
	rewritten to whatever this report aliased it to.
	"""
	from visitormanagement.permissions import get_visitor_pass_permission_query_conditions

	condition = get_visitor_pass_permission_query_conditions()
	if not condition:
		return None
	return condition.replace("`tabVisitor Pass`", f"`{alias}`")
