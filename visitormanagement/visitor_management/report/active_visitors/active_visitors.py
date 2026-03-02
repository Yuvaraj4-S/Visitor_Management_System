# Copyright (c) 2026, Harthesh and contributors
# For license information, please see license.txt

# import frappe

import frappe
def execute(filters=None):
	columns, data = [], []
	return columns, data
def execute(filters=None):

    columns = [
        {"label":"Badge","fieldname":"badge_number","width":110},
        {"label":"Visitor","fieldname":"visitor_name","width":140},
        {"label":"Type","fieldname":"visitor_type","width":100},
        {"label":"Items Status","fieldname":"item_verification_status","width":120},
        {"label":"Gate Entered","fieldname":"gate_name","width":120},
        {"label":"Checked In At","fieldname":"checkin_time","fieldtype":"Datetime","width":140},
        {"label":"Duration (min)","fieldname":"duration","fieldtype":"Int","width":120},
        {"label":"Security Officer","fieldname":"security_officer","width":130},
        {"label":"Expected Out","fieldname":"expected_checkout","fieldtype":"Time","width":120},
    ]

    data = frappe.db.sql("""
        SELECT 
            vp.badge_number,
            vp.visitor_name,
            vp.visitor_type,
            vp.item_verification_status,
            sl.gate_name,
            sl.checkin_datetime AS checkin_time,
            TIMESTAMPDIFF(MINUTE, sl.checkin_datetime, NOW()) AS duration,
            sl.security_officer,
            vp.expected_checkout
        FROM `tabVisitor Pass` vp
        LEFT JOIN `tabSecurity Log` sl
            ON sl.visitor_pass = vp.name
            AND sl.event_type = "Check-In"
        WHERE vp.status = "Checked-In"
        ORDER BY sl.checkin_datetime ASC
    """, as_dict=True)

    return columns, data
