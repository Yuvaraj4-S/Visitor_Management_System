# Copyright (c) 2026, Harthesh and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import today


def execute(filters=None):
    filters = filters or {}
    columns = get_columns()
    data = get_data(filters)
    summary = get_summary(data)
    chart = get_chart(data)
    return columns, data, None, chart, summary


def get_columns():
    return [
        {"label": "Gate", "fieldname": "gate_name", "fieldtype": "Data", "width": 180},
        {"label": "Check-Ins", "fieldname": "checkins", "fieldtype": "Int", "width": 110},
        {"label": "Check-Outs", "fieldname": "checkouts", "fieldtype": "Int", "width": 110},
        {"label": "Currently Inside", "fieldname": "inside", "fieldtype": "Int", "width": 140},
        {"label": "Items Pending", "fieldname": "pending_verify", "fieldtype": "Int", "width": 130},
        {"label": "% Occupied", "fieldname": "occupancy_pct", "fieldtype": "Percent", "width": 110},
    ]


def get_data(filters):
    date_val = filters.get("date") or today()

    rows = frappe.db.sql(
        """
        SELECT
            sl.gate_name,
            SUM(CASE WHEN sl.event_type='Check-In' AND DATE(sl.check_in_date_time)=%(d)s THEN 1 ELSE 0 END) AS checkins,
            SUM(CASE WHEN sl.event_type='Check-Out' AND DATE(sl.check_out_date_time)=%(d)s THEN 1 ELSE 0 END) AS checkouts,
            SUM(CASE WHEN sl.event_type='Check-In' AND sl.all_items_confirmed=0 AND DATE(sl.check_in_date_time)=%(d)s THEN 1 ELSE 0 END) AS pending_verify
        FROM `tabSecurity Log` sl
        WHERE sl.gate_name IS NOT NULL AND sl.gate_name != ''
        GROUP BY sl.gate_name
        ORDER BY checkins DESC
        """,
        {"d": date_val},
        as_dict=True,
    )

    for r in rows:
        r["inside"] = max(0, (r.get("checkins") or 0) - (r.get("checkouts") or 0))
        if r.get("checkins"):
            r["occupancy_pct"] = round((r["inside"] / r["checkins"]) * 100, 1)
        else:
            r["occupancy_pct"] = 0
    return rows


def get_summary(data):
    total_in = sum(r.get("checkins") or 0 for r in data)
    total_out = sum(r.get("checkouts") or 0 for r in data)
    currently_inside = sum(r.get("inside") or 0 for r in data)
    pending = sum(r.get("pending_verify") or 0 for r in data)

    return [
        {"value": total_in, "label": "Total Check-Ins", "indicator": "Green"},
        {"value": total_out, "label": "Total Check-Outs", "indicator": "Grey"},
        {"value": currently_inside, "label": "Currently Inside", "indicator": "Blue"},
        {"value": pending, "label": "Items Pending", "indicator": "Orange"},
    ]


def get_chart(data):
    if not data:
        return None
    return {
        "type": "bar",
        "data": {
            "labels": [r["gate_name"] for r in data],
            "datasets": [
                {"name": "Check-Ins", "values": [r.get("checkins") or 0 for r in data]},
                {"name": "Currently Inside", "values": [r.get("inside") or 0 for r in data]},
            ],
        },
        "colors": ["#28a745", "#007bff"],
    }
