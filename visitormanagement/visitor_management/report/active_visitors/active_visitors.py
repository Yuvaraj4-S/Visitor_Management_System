# For license information, please see license.txt

import frappe


TYPE_INDICATOR = {
    "Contractor": "orange",
    "Candidate": "purple",
    "Customer": "green",
    "Supplier": "blue",
    "VIP": "red",
}


def execute(filters=None):
    filters = filters or {}
    columns = get_columns()
    data = get_data(filters)
    report_summary = get_summary(data)
    chart = get_chart(data)
    return columns, data, None, chart, report_summary


def get_columns():
    return [
        {"label": "Pass ID", "fieldname": "visitor_pass", "fieldtype": "Link",
         "options": "Visitor Pass", "width": 140},
        {"label": "Visitor", "fieldname": "visitor_name", "fieldtype": "Data", "width": 180},
        {"label": "Type", "fieldname": "visitor_type", "fieldtype": "Data", "width": 100},
        {"label": "Company", "fieldname": "company", "fieldtype": "Data", "width": 170},
        {"label": "Host", "fieldname": "person_to_visit", "fieldtype": "Link",
         "options": "Employee", "width": 140},
        {"label": "Gate", "fieldname": "gate_name", "fieldtype": "Data", "width": 110},
        {"label": "Checked In", "fieldname": "checkin_time", "fieldtype": "Datetime", "width": 160},
        {"label": "Duration", "fieldname": "duration_label", "fieldtype": "Data", "width": 110},
        {"label": "Expected Out", "fieldname": "expected_checkout", "fieldtype": "Time", "width": 110},
        {"label": "Items", "fieldname": "item_verification_status", "fieldtype": "Data", "width": 100},
        {"label": "Badge", "fieldname": "badge_number", "fieldtype": "Data", "width": 140},
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

    where = " AND ".join(conditions)

    rows = frappe.db.sql(
        f"""
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
        WHERE {where}
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

    vip_count = by_type.get("VIP", 0)
    pending_items = sum(1 for r in data if r.item_verification_status in ("Pending", "Partial"))

    return [
        {"value": total, "label": "Currently Inside", "indicator": "Green"},
        {"value": vip_count, "label": "VIP", "indicator": "Red"},
        {"value": pending_items, "label": "Items Pending", "indicator": "Orange"},
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
