# For license information, please see license.txt

import frappe
from frappe.utils import add_days, today

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
			"label": "Pass ID",
			"fieldname": "visitor_pass",
			"fieldtype": "Link",
			"options": "Visitor Pass",
			"width": 140,
		},
		{"label": "Visit Date", "fieldname": "visit_date", "fieldtype": "Date", "width": 105},
		{"label": "Visitor", "fieldname": "visitor_name", "fieldtype": "Data", "width": 180},
		{"label": "Type", "fieldname": "visitor_type", "fieldtype": "Data", "width": 100},
		{"label": "Company", "fieldname": "company", "fieldtype": "Data", "width": 160},
		{
			"label": "Host",
			"fieldname": "person_to_visit",
			"fieldtype": "Link",
			"options": "Employee",
			"width": 140,
		},
		{"label": "Purpose", "fieldname": "purpose_of_visit", "fieldtype": "Small Text", "width": 220},
		{"label": "Checked-In", "fieldname": "checkin", "fieldtype": "Datetime", "width": 155},
		{"label": "Checked-Out", "fieldname": "checkout", "fieldtype": "Datetime", "width": 155},
		{"label": "Gate", "fieldname": "gate_name", "fieldtype": "Data", "width": 110},
		{"label": "Status", "fieldname": "status", "fieldtype": "Data", "width": 115},
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

	rows = frappe.db.sql(
		"""
        SELECT
            vp.name AS visitor_pass,
            vp.visit_date,
            vp.visitor_full_name AS visitor_name,
            vp.visitor_type,
            vp.company__organisation AS company,
            vp.person_to_visit,
            vp.purpose_of_visit,
            sl_in.check_in_date_time AS checkin,
            sl_out.check_out_date_time AS checkout,
            sl_in.gate_name,
            vp.status
        FROM `tabVisitor Pass` vp
        LEFT JOIN `tabSecurity Log` sl_in
            ON sl_in.visitor_pass = vp.name AND sl_in.event_type = 'Check-In'
        LEFT JOIN `tabSecurity Log` sl_out
            ON sl_out.visitor_pass = vp.name AND sl_out.event_type = 'Check-Out'
        """
		+ where
		+ """
        ORDER BY vp.visit_date DESC, sl_in.check_in_date_time DESC
        LIMIT %(row_limit)s
        """,
		values,
		as_dict=True,
	)

	truncated = len(rows) > MAX_ROWS
	if truncated:
		rows = rows[:MAX_ROWS]
	return rows, truncated


def get_summary(data):
	total = len(data)
	checked_in = sum(1 for r in data if r.status == "Checked-In")
	checked_out = sum(1 for r in data if r.status == "Checked-Out")
	approved = sum(1 for r in data if r.status == "Approved")
	no_show = sum(1 for r in data if r.status == "Approved" and not r.checkin)

	return [
		{"value": total, "label": "Total Visits", "indicator": "Blue"},
		{"value": checked_in, "label": "Currently Inside", "indicator": "Green"},
		{"value": checked_out, "label": "Completed Visits", "indicator": "Grey"},
		{"value": approved, "label": "Awaiting Check-In", "indicator": "Orange"},
	]


def get_chart(data):
	by_type = {}
	for row in data:
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
