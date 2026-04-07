# Copyright (c) 2026, Harthesh and contributors
# For license information, please see license.txt

import frappe


def execute(filters=None):
    columns = [
        {"label": "Gate", "fieldname": "gate_name", "fieldtype": "Data", "width": 140},
        {"label": "Check-Ins Today", "fieldname": "checkins", "fieldtype": "Int", "width": 140},
        {"label": "Check-Outs Today", "fieldname": "checkouts", "fieldtype": "Int", "width": 140},
        {"label": "Currently Inside", "fieldname": "inside", "fieldtype": "Int", "width": 130},
        {"label": "Items Pending Verify", "fieldname": "pending_verify", "fieldtype": "Int", "width": 160},
    ]

    data = frappe.db.sql(
        """
        SELECT sl.gate_name,
        SUM(CASE WHEN sl.event_type='Check-In' AND DATE(sl.check_in_date_time)=CURDATE() THEN 1 ELSE 0 END) AS checkins,
        SUM(CASE WHEN sl.event_type='Check-Out' AND DATE(sl.check_out_date_time)=CURDATE() THEN 1 ELSE 0 END) AS checkouts,
        SUM(CASE WHEN sl.event_type='Check-In' AND DATE(sl.check_in_date_time)=CURDATE() THEN 1 ELSE 0 END) -
        SUM(CASE WHEN sl.event_type='Check-Out' AND DATE(sl.check_out_date_time)=CURDATE() THEN 1 ELSE 0 END) AS inside,
        SUM(CASE WHEN sl.event_type='Check-In' AND sl.all_items_confirmed=0 AND DATE(sl.check_in_date_time)=CURDATE() THEN 1 ELSE 0 END) AS pending_verify
        FROM `tabSecurity Log` sl
        GROUP BY sl.gate_name
        ORDER BY checkins DESC
        """,
        as_dict=True,
    )

    return columns, data
