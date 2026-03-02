# Copyright (c) 2026, Harthesh and contributors
# For license information, please see license.txt

# import frappe

import frappe
def execute(filters=None):
	columns, data = [], []
	return columns, data
def execute(filters=None):

    columns = [
        {'label':'Badge','fieldname':'badge_number','fieldtype':'Data','width':110},
        {'label':'Visitor','fieldname':'visitor_name','fieldtype':'Data','width':140},
        {'label':'Type','fieldname':'visitor_type','fieldtype':'Data','width':100},
        {'label':'Person to Visit','fieldname':'person_to_visit','fieldtype':'Data','width':130},
        {'label':'Purpose','fieldname':'purpose_of_visit','fieldtype':'Data','width':150},
        {'label':'Items Status','fieldname':'item_verification_status','fieldtype':'Data','width':120},
        {'label':'Check-In','fieldname':'checkin','fieldtype':'Datetime','width':140},
        {'label':'Check-Out','fieldname':'checkout','fieldtype':'Datetime','width':140},
        {'label':'Status','fieldname':'status','fieldtype':'Data','width':100},
        {'label':'Gate','fieldname':'gate_name','fieldtype':'Data','width':110},
    ]

    conditions = []
    values = {}

    if filters.get('from_date'):
        conditions.append("vp.visit_date >= %(from_date)s")
        values["from_date"] = filters["from_date"]

    if filters.get('to_date'):
        conditions.append("vp.visit_date <= %(to_date)s")
        values["to_date"] = filters["to_date"]

    if filters.get('visitor_type'):
        conditions.append("vp.visitor_type = %(visitor_type)s")
        values["visitor_type"] = filters["visitor_type"]

    where_clause = " AND ".join(conditions)
    if where_clause:
        where_clause = "WHERE " + where_clause

    data = frappe.db.sql(f"""
        SELECT vp.badge_number, vp.visitor_name, vp.visitor_type,
               vp.person_to_visit, vp.purpose_of_visit,
               vp.item_verification_status,
               sl_in.checkin_datetime as checkin,
               sl_out.checkout_datetime as checkout,
               vp.status, sl_in.gate_name
        FROM `tabVisitor Pass` vp
        LEFT JOIN `tabSecurity Log` sl_in
            ON sl_in.visitor_pass = vp.name AND sl_in.event_type = "Check-In"
        LEFT JOIN `tabSecurity Log` sl_out
            ON sl_out.visitor_pass = vp.name AND sl_out.event_type = "Check-Out"
        {where_clause}
        ORDER BY vp.visit_date DESC
    """, values, as_dict=True)

    return columns, data
