# Copyright (c) 2026, Harthesh and contributors
# For license information, please see license.txt

import frappe


def execute(filters=None):
    filters = filters or {}
    columns = get_columns()
    data = get_data(filters)
    summary = get_summary(data)
    chart = get_chart(data)
    return columns, data, None, chart, summary


def get_columns():
    return [
        {"label": "Pass ID", "fieldname": "visitor_pass", "fieldtype": "Link",
         "options": "Visitor Pass", "width": 140},
        {"label": "Visit Date", "fieldname": "visit_date", "fieldtype": "Date", "width": 105},
        {"label": "Visitor", "fieldname": "visitor_name", "fieldtype": "Data", "width": 180},
        {"label": "Type", "fieldname": "visitor_type", "fieldtype": "Data", "width": 100},
        {"label": "Company", "fieldname": "company", "fieldtype": "Data", "width": 160},
        {"label": "Host", "fieldname": "person_to_visit", "fieldtype": "Link",
         "options": "Employee", "width": 140},
        {"label": "Purpose", "fieldname": "purpose_of_visit", "fieldtype": "Small Text", "width": 220},
        {"label": "Check-In", "fieldname": "checkin", "fieldtype": "Datetime", "width": 155},
        {"label": "Check-Out", "fieldname": "checkout", "fieldtype": "Datetime", "width": 155},
        {"label": "Gate", "fieldname": "gate_name", "fieldtype": "Data", "width": 110},
        {"label": "Status", "fieldname": "status", "fieldtype": "Data", "width": 115},
    ]


def get_data(filters):
    conditions = []
    values = {}

    if filters.get("from_date"):
        conditions.append("vp.visit_date >= %(from_date)s")
        values["from_date"] = filters["from_date"]

    if filters.get("to_date"):
        conditions.append("vp.visit_date <= %(to_date)s")
        values["to_date"] = filters["to_date"]

    if filters.get("visitor_type"):
        conditions.append("vp.visitor_type = %(visitor_type)s")
        values["visitor_type"] = filters["visitor_type"]

    if filters.get("status"):
        conditions.append("vp.status = %(status)s")
        values["status"] = filters["status"]

    if filters.get("host"):
        conditions.append("vp.person_to_visit = %(host)s")
        values["host"] = filters["host"]

    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

    return frappe.db.sql(
        f"""
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
        {where}
        ORDER BY vp.visit_date DESC, sl_in.check_in_date_time DESC
        """,
        values,
        as_dict=True,
    )


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
