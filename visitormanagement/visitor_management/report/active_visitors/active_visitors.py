# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import now_datetime, time_diff_in_seconds

from visitormanagement.visitor_management.report.utils import (
	fill_host,
	gate_movements,
	link_or_data,
	visitor_pass_rows,
)

# Indicator colours come from Visitor Type.badge_colour so a custom type is
# coloured by its own configuration instead of falling back to grey.
_SWATCH_TO_INDICATOR = {
	"Orange": "orange",
	"Purple": "purple",
	"Green": "green",
	"Teal": "blue",
	"Gold": "yellow",
	"Blue": "blue",
	"Red": "red",
	"Grey": "grey",
}


def _type_colours():
	rows = frappe.get_all("Visitor Type", fields=["name", "badge_colour"])
	return {r.name: _SWATCH_TO_INDICATOR.get(r.badge_colour, "grey") for r in rows}


def execute(filters=None):
	filters = filters or {}
	columns = get_columns()
	data = get_data(filters)
	report_summary = get_summary(data)
	chart = get_chart(data)
	return columns, data, None, chart, report_summary


def get_columns():
	return [
		link_or_data("Visitor Pass", label=_("Pass ID"), fieldname="visitor_pass", width=140),
		{"label": _("Visitor"), "fieldname": "visitor_name", "fieldtype": "Data", "width": 180},
		{"label": _("Type"), "fieldname": "visitor_type", "fieldtype": "Data", "width": 100},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Data", "width": 170},
		# The host by name, as Data — never a Link to Employee (see report/utils.py).
		{"label": _("Host"), "fieldname": "host", "fieldtype": "Data", "width": 160},
		{"label": _("Gate"), "fieldname": "gate_name", "fieldtype": "Data", "width": 110},
		{"label": _("Checked-In"), "fieldname": "checkin_time", "fieldtype": "Datetime", "width": 160},
		{"label": _("Time Inside"), "fieldname": "duration_label", "fieldtype": "Data", "width": 110},
		{"label": _("Expected Out"), "fieldname": "expected_checkout", "fieldtype": "Time", "width": 110},
		{
			"label": _("Item Status"),
			"fieldname": "item_verification_status",
			"fieldtype": "Data",
			"width": 100,
		},
		{"label": _("Badge"), "fieldname": "badge_number", "fieldtype": "Data", "width": 140},
	]


def get_data(filters):
	pass_filters = [["status", "=", "Checked-In"]]
	if filters.get("visitor_type"):
		pass_filters.append(["visitor_type", "=", filters["visitor_type"]])
	if filters.get("host"):
		pass_filters.append(["person_to_visit", "=", filters["host"]])

	# One row per visitor inside, and only the passes this user's Visitor Pass
	# list shows.
	rows = visitor_pass_rows(
		pass_filters,
		[
			"name as visitor_pass",
			"badge_number",
			"visitor_full_name as visitor_name",
			"visitor_type",
			"company__organisation as company",
			"person_to_visit",
			"host_name",
			"item_verification_status",
			"expected_checkout",
		],
	)

	fill_host(rows)
	movements = gate_movements(r.visitor_pass for r in rows)
	now = now_datetime()
	gate_filter = filters.get("gate_name")

	data = []
	for row in rows:
		# The entry that put the visitor inside is the latest check-in.
		moved = movements.get(row.visitor_pass)
		row["gate_name"] = moved.last_in_gate if moved else None
		row["checkin_time"] = moved.last_in if moved else None
		if gate_filter and row["gate_name"] != gate_filter:
			continue
		minutes = None
		if row["checkin_time"]:
			minutes = int(time_diff_in_seconds(now, row["checkin_time"]) // 60)
		row["duration_minutes"] = minutes
		row["duration_label"] = format_duration(minutes)
		data.append(row)

	# Longest inside first; a pass with no gate record (no time) goes last.
	data.sort(key=lambda r: (r["checkin_time"] is None, str(r["checkin_time"] or "")))
	return data


def format_duration(minutes):
	if not minutes or minutes < 0:
		return "-"
	hours = minutes // 60
	mins = minutes % 60
	if hours == 0:
		return f"{mins}m"
	return f"{hours}h {mins}m"


def get_summary(data):
	total = len(data)
	by_type = {}
	for row in data:
		by_type[row.visitor_type] = by_type.get(row.visitor_type, 0) + 1

	# "VIP" is whichever Visitor Types use the VIP layout, not a type literally
	# named VIP — so a site's own executive type is counted here too.
	vip_types = set(frappe.get_all("Visitor Type", filters={"detail_layout": "VIP"}, pluck="name"))
	vip_count = sum(count for vt, count in by_type.items() if vt in vip_types)
	pending_items = sum(1 for r in data if r.item_verification_status in ("Pending", "Partial"))

	return [
		{"value": total, "label": _("Currently Inside"), "indicator": "Green"},
		{"value": vip_count, "label": _("VIP / Executive"), "indicator": "Red"},
		{"value": pending_items, "label": _("Items Pending"), "indicator": "Orange"},
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
			"datasets": [{"name": _("Visitors"), "values": list(by_type.values())}],
		},
	}
