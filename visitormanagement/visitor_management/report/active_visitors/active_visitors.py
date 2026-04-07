# Copyright (c) 2026, Harthesh and contributors
# For license information, please see license.txt

import frappe


def execute(filters=None):
    columns = [
        {"label": "Badge", "fieldname": "badge_number", "fieldtype": "Data", "width": 110},
        {"label": "Visitor", "fieldname": "visitor_name", "fieldtype": "Data", "width": 160},
        {"label": "Type", "fieldname": "visitor_type", "fieldtype": "Data", "width": 100},
        {"label": "Items Status", "fieldname": "item_verification_status", "fieldtype": "Data", "width": 120},
        {"label": "Gate Entered", "fieldname": "gate_name", "fieldtype": "Data", "width": 120},
        {"label": "Checked In At", "fieldname": "checkin_time", "fieldtype": "Datetime", "width": 160},
        {"label": "Duration (min)", "fieldname": "duration", "fieldtype": "Int", "width": 120},
        {"label": "Security Officer", "fieldname": "security_officer", "fieldtype": "Link", "options": "Employee", "width": 140},
        {"label": "Expected Out", "fieldname": "expected_checkout", "fieldtype": "Time", "width": 120},
    ]

    data = frappe.db.sql(
        """
        SELECT
            vp.badge_number,
            vp.visitor_full_name AS visitor_name,
            vp.visitor_type,
            vp.item_verification_status,
            sl.gate_name,
            sl.check_in_date_time AS checkin_time,
            TIMESTAMPDIFF(MINUTE, sl.check_in_date_time, NOW()) AS duration,
            sl.security_officer,
            vp.expected_checkout
        FROM `tabVisitor Pass` vp
        LEFT JOIN `tabSecurity Log` sl
            ON sl.visitor_pass = vp.name
            AND sl.event_type = 'Check-In'
        WHERE vp.status = 'Checked-In'
        ORDER BY sl.check_in_date_time ASC
        """,
        as_dict=True,
    )

    return columns, data
