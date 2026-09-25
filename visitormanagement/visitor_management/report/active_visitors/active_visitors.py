# For license information, please see license.txt

import frappe
from frappe import _


# Indicator colours come from Visitor Type.badge_colour so a custom type is
# coloured by its own configuration instead of falling back to grey.
_SWATCH_TO_INDICATOR = {
    "Orange": "orange", "Purple": "purple", "Green": "green", "Teal": "blue",
    "Gold": "yellow", "Blue": "blue", "Red": "red", "Grey": "grey",
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
        {"label": _("Pass ID"), "fieldname": "visitor_pass", "fieldtype": "Link",
         "options": "Visitor Pass", "width": 140},
        {"label": _("Visitor"), "fieldname": "visitor_name", "fieldtype": "Data", "width": 180},
        {"label": _("Type"), "fieldname": "visitor_type", "fieldtype": "Data", "width": 100},
        {"label": _("Company"), "fieldname": "company", "fieldtype": "Data", "width": 170},
        {"label": _("Host"), "fieldname": "person_to_visit", "fieldtype": "Link",
         "options": "Employee", "width": 140},
        {"label": _("Gate"), "fieldname": "gate_name", "fieldtype": "Data", "width": 110},
        {"label": _("Checked-In"), "fieldname": "checkin_time", "fieldtype": "Datetime", "width": 160},
        {"label": _("Time Inside"), "fieldname": "duration_label", "fieldtype": "Data", "width": 110},
        {"label": _("Expected Out"), "fieldname": "expected_checkout", "fieldtype": "Time", "width": 110},
        {"label": _("Item Status"), "fieldname": "item_verification_status", "fieldtype": "Data", "width": 100},
        {"label": _("Badge"), "fieldname": "badge_number", "fieldtype": "Data", "width": 140},
    ]


def get_data(filters):
    conditions = ["vp.status = 'Checked-In'"]
    values = {}

    if filters.get("visitor_type"):
        conditions.append("vp.visitor_type = %(visitor_type)s")
        values["visitor_type"] = filters["visitor_type"]

    if filters.get("gate_name"):
        conditions.append("sl.gate_name = %(gate_name)s")
        values["gate_name"] = filters["gate_name"]

    if filters.get("host"):
        conditions.append("vp.person_to_visit = %(host)s")
        values["host"] = filters["host"]

    scope = _visitor_pass_scope("vp")
    if scope:
        conditions.append(scope)

    where = " AND ".join(conditions)

    rows = frappe.db.sql(
        """
        SELECT
            vp.name AS visitor_pass,
            vp.badge_number,
            vp.visitor_full_name AS visitor_name,
            vp.visitor_type,
            vp.company__organisation AS company,
            vp.person_to_visit,
            vp.item_verification_status,
            vp.expected_checkout,
            sl.gate_name,
            sl.check_in_date_time AS checkin_time,
            TIMESTAMPDIFF(MINUTE, sl.check_in_date_time, NOW()) AS duration_minutes
        FROM `tabVisitor Pass` vp
        LEFT JOIN `tabSecurity Log` sl
            ON sl.visitor_pass = vp.name AND sl.event_type = 'Check-In'
        WHERE """
        + where
        + """
        ORDER BY sl.check_in_date_time ASC
        """,
        values,
        as_dict=True,
    )

    for r in rows:
        r["duration_label"] = format_duration(r.get("duration_minutes"))
    return rows


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
    vip_types = set(
        frappe.get_all("Visitor Type", filters={"detail_layout": "VIP"}, pluck="name")
    )
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
            "datasets": [{"name": "Visitors", "values": list(by_type.values())}],
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
