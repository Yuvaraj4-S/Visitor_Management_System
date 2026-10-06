# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import add_days, today

from visitormanagement.visitor_management.report.utils import (
	fill_host,
	gate_movements,
	link_or_data,
	visitor_pass_rows,
)

MAX_ROWS = 5000


def execute(filters=None):
	filters = _validate_filters(filters)
	columns = get_columns()
	data, truncated = get_data(filters)
	summary = get_summary(data)
	chart = get_chart(data)
	message = None
	if truncated:
		message = _(
			"Showing the first {0} matching passes for this date range. "
			"Narrow the From Date / To Date filters to see the rest."
		).format(f"{MAX_ROWS:,}")
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
		link_or_data("Visitor Pass", label=_("Pass ID"), fieldname="visitor_pass", width=140),
		{"label": _("Visit Date"), "fieldname": "visit_date", "fieldtype": "Date", "width": 105},
		{"label": _("Visitor"), "fieldname": "visitor_name", "fieldtype": "Data", "width": 180},
		{"label": _("Type"), "fieldname": "visitor_type", "fieldtype": "Data", "width": 100},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Data", "width": 160},
		# The host by name, as Data. A Link to Employee made Frappe filter the rows
		# by the viewer's User Permissions on Employee and refuse viewers without
		# access to it — see report/utils.py.
		{"label": _("Host"), "fieldname": "host", "fieldtype": "Data", "width": 160},
		{"label": _("Purpose"), "fieldname": "purpose_of_visit", "fieldtype": "Small Text", "width": 220},
		{"label": _("First Check-In"), "fieldname": "checkin", "fieldtype": "Datetime", "width": 155},
		{"label": _("Last Check-Out"), "fieldname": "checkout", "fieldtype": "Datetime", "width": 155},
		{"label": _("Entries"), "fieldname": "entries", "fieldtype": "Int", "width": 80},
		{"label": _("Gate"), "fieldname": "gate_name", "fieldtype": "Data", "width": 110},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 115},
	]


def get_data(filters):
	# from_date/to_date are guaranteed present by _validate_filters() — always
	# bound the scan, never an optional condition.
	pass_filters = [
		["visit_date", ">=", filters["from_date"]],
		["visit_date", "<=", filters["to_date"]],
	]
	if filters.get("visitor_type"):
		pass_filters.append(["visitor_type", "=", filters["visitor_type"]])
	if filters.get("status"):
		pass_filters.append(["status", "=", filters["status"]])
	if filters.get("host"):
		pass_filters.append(["person_to_visit", "=", filters["host"]])

	# One row per pass, and only the passes this user's Visitor Pass list shows.
	rows = visitor_pass_rows(
		pass_filters,
		[
			"name as visitor_pass",
			"visit_date",
			"visitor_full_name as visitor_name",
			"visitor_type",
			"company__organisation as company",
			"person_to_visit",
			"host_name",
			"purpose_of_visit",
			"status",
		],
		order_by="visit_date desc, modified desc",
		limit=MAX_ROWS + 1,
	)

	truncated = len(rows) > MAX_ROWS
	if truncated:
		rows = rows[:MAX_ROWS]

	fill_host(rows)
	movements = gate_movements(r.visitor_pass for r in rows)
	for row in rows:
		moved = movements.get(row.visitor_pass)
		row["checkin"] = moved.first_in if moved else None
		# A check-out older than the latest check-in belongs to an earlier entry:
		# the visitor is inside again, so there is no "last" check-out to show.
		left = moved.last_out if moved else None
		if left and moved.last_in and left < moved.last_in:
			left = None
		row["checkout"] = left
		row["entries"] = moved.entries if moved else 0
		row["gate_name"] = moved.last_in_gate if moved else None

	# Newest visit date first; within a day, the latest arrival first.
	rows.sort(key=lambda r: (str(r.visit_date or ""), str(r.checkin or "")), reverse=True)
	return rows, truncated


def get_summary(data):
	total = len(data)
	checked_in = sum(1 for r in data if r.status == "Checked-In")
	checked_out = sum(1 for r in data if r.status == "Checked-Out")
	approved = sum(1 for r in data if r.status == "Approved")

	return [
		{"value": total, "label": _("Total Visits"), "indicator": "Blue"},
		{"value": checked_in, "label": _("Currently Inside"), "indicator": "Green"},
		{"value": checked_out, "label": _("Completed Visits"), "indicator": "Grey"},
		{"value": approved, "label": _("Awaiting Check-In"), "indicator": "Orange"},
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
			"datasets": [{"name": _("Visits"), "values": list(by_type.values())}],
		},
	}
