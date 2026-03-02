# Copyright (c) 2026, Harthesh and contributors
# For license information, please see license.txt

# import frappe

import frappe
def execute(filters=None):
	columns, data = [], []
	return columns, data
def execute(filters=None):

    columns = [
        {"label": "Gate", "fieldname": "gate_name", "width": 140},
        {"label": "Check-Ins Today", "fieldname": "checkins", "width": 140},
        {"label": "Check-Outs Today", "fieldname": "checkouts", "width": 140},
        {"label": "Currently Inside", "fieldname": "inside", "width": 130},
        {"label": "Items Pending Verify", "fieldname": "pending_verify", "width": 160},
    ]

    data = frappe.db.sql("""
        SELECT sl.gate_name,
        SUM(CASE WHEN sl.event_type="Check-In" AND DATE(sl.checkin_datetime)=CURDATE() THEN 1 ELSE 0 END) as checkins,
        SUM(CASE WHEN sl.event_type="Check-Out" AND DATE(sl.checkout_datetime)=CURDATE() THEN 1 ELSE 0 END) as checkouts,
        SUM(CASE WHEN sl.event_type="Check-In" AND DATE(sl.checkin_datetime)=CURDATE() THEN 1 ELSE 0 END) -
        SUM(CASE WHEN sl.event_type="Check-Out" AND DATE(sl.checkout_datetime)=CURDATE() THEN 1 ELSE 0 END) as inside,
        SUM(CASE WHEN sl.all_items_confirmed=0 AND DATE(sl.checkin_datetime)=CURDATE() THEN 1 ELSE 0 END) as pending_verify
        FROM `tabSecurity Log` sl
        GROUP BY sl.gate_name
        ORDER BY checkins DESC
    """, as_dict=True)

    return columns, data
