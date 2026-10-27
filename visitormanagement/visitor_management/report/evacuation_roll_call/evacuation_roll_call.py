# For license information, please see license.txt
"""Everyone on site right now, for the evacuation marshal.

Built for a bad moment: one click from the workspace, one click to print, and
nobody left off. It therefore does not lean on the Visitor Pass row scope the
other reports apply (`permission_query_conditions`) or on User Permissions —
either could hide a visitor from the person counting heads at the assembly
point. Who may open it is decided here instead, by role.

A person is on site as the other head counts define it (report/on_site.py):
the lead visitor of every pass that is "Checked-In", and every accompanying
visitor the gate has checked in and not yet out.
"""

import frappe
from frappe import _

from visitormanagement.visitor_management.report.on_site import member_location_sql, members_on_site

# Security runs the muster, Facility Manager owns the building's emergency
# plan; the app ships no separate safety role.
ROLL_CALL_ROLES = {"System Manager", "Security", "Facility Manager"}

UNKNOWN_LOCATION = "—"


def execute(filters=None):
	_check_access()
	filters = filters or {}
	columns = get_columns()
	data = get_data(filters)
	return columns, data, None, None, get_summary(data)


def _check_access():
	if frappe.session.user == "Administrator" or ROLL_CALL_ROLES & set(frappe.get_roles()):
		return
	frappe.throw(
		_("Only Security, Facility Manager and System Manager can open the roll call."),
		frappe.PermissionError,
	)


def get_columns():
	# Data, never Link: Frappe drops a report row whose Link value is outside the
	# user's User Permissions (query_report.get_filtered_data). On this report a
	# dropped row is a person nobody looks for.
	return [
		{"label": _("Location"), "fieldname": "location", "fieldtype": "Data", "width": 130},
		{"label": _("Name"), "fieldname": "visitor_name", "fieldtype": "Data", "width": 180},
		{"label": _("On Pass As"), "fieldname": "role", "fieldtype": "Data", "width": 150},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Data", "width": 150},
		{"label": _("Mobile"), "fieldname": "mobile_number", "fieldtype": "Data", "width": 130},
		{"label": _("Host"), "fieldname": "host_name", "fieldtype": "Data", "width": 150},
		{"label": _("Host Mobile"), "fieldname": "host_mobile", "fieldtype": "Data", "width": 130},
		{"label": _("Checked In"), "fieldname": "checkin_time", "fieldtype": "Datetime", "width": 150},
		{"label": _("Badge"), "fieldname": "badge_number", "fieldtype": "Data", "width": 140},
		{"label": _("VIP"), "fieldname": "vip", "fieldtype": "Check", "width": 55},
		{"label": _("Pass"), "fieldname": "visitor_pass", "fieldtype": "Data", "width": 130},
		{"label": _("Present at Assembly Point"), "fieldname": "present", "fieldtype": "Check", "width": 110},
	]


def get_data(filters):
	location = filters.get("location")
	lead_location = "COALESCE(NULLIF(vp.current_location, ''), sl.gate_name)"

	lead_conditions = ["vp.status = 'Checked-In'"]
	values = {}
	if location:
		lead_conditions.append(f"{lead_location} = %(location)s")
		values["location"] = location

	leads = frappe.db.sql(
		f"""
        SELECT
            vp.name AS visitor_pass,
            vp.visitor_full_name AS visitor_name,
            vp.company__organisation AS company,
            vp.mobile_number,
            vp.host_name,
            emp.cell_number AS host_mobile,
            {lead_location} AS location,
            COALESCE(sl.check_in_date_time, vp.actual_checkin) AS checkin_time,
            vp.badge_number,
            vp.visitor_type_layout,
            vp.is_group_visit
        FROM `tabVisitor Pass` vp
        LEFT JOIN `tabSecurity Log` sl
            ON sl.name = (
                SELECT latest.name
                FROM `tabSecurity Log` latest
                WHERE latest.visitor_pass = vp.name
                    AND latest.event_type = 'Check-In'
                    AND latest.group_members_only = 0
                ORDER BY latest.check_in_date_time DESC, latest.creation DESC
                LIMIT 1
            )
        LEFT JOIN `tabEmployee` emp ON emp.name = vp.person_to_visit
        WHERE {" AND ".join(lead_conditions)}
        """,
		values,
		as_dict=True,
	)

	member_conditions = [f"{member_location_sql()} = %(location)s"] if location else []
	members = members_on_site(
		member_conditions,
		values,
		extra_fields="""
            , vp.company__organisation AS company
            , vp.host_name
            , emp.cell_number AS host_mobile
            , vp.visitor_type_layout
        """,
	)

	rows = []
	for lead in leads:
		rows.append(
			frappe._dict(
				lead,
				role=_("Group lead") if lead.is_group_visit else _("Visitor"),
				vip=1 if lead.visitor_type_layout == "VIP" else 0,
				present=0,
			)
		)
	for member in members:
		rows.append(
			frappe._dict(
				visitor_pass=member.visitor_pass,
				visitor_name=member.visitor_name,
				role=_("With {0}").format(member.lead_name),
				company=member.company,
				mobile_number=member.mobile_number,
				host_name=member.host_name,
				host_mobile=member.host_mobile,
				location=member.gate_name,
				checkin_time=member.checkin_time,
				badge_number=member.badge_number,
				vip=1 if member.visitor_type_layout == "VIP" else 0,
				present=0,
			)
		)

	for row in rows:
		row.location = row.location or UNKNOWN_LOCATION
		for fieldname in ("visitor_pass", "visitor_name", "company", "mobile_number", "host_name"):
			row[fieldname] = row.get(fieldname) or ""
		row.pop("visitor_type_layout", None)
		row.pop("is_group_visit", None)

	# By location, so the marshal at each assembly point reads one block; an
	# unknown location last. A group's lead first, then the people with them.
	rows.sort(
		key=lambda r: (
			r.location == UNKNOWN_LOCATION,
			r.location.lower(),
			r.visitor_pass,
			r.role != _("Group lead"),
			r.visitor_name.lower(),
		)
	)
	return rows


def get_summary(data):
	by_location = {}
	for row in data:
		by_location[row.location] = by_location.get(row.location, 0) + 1
	summary = [
		{"value": len(data), "label": _("People On Site"), "indicator": "Red", "datatype": "Int"},
		{
			"value": sum(1 for row in data if row.vip),
			"label": _("VIP / Executive"),
			"indicator": "Orange",
			"datatype": "Int",
		},
	]
	for location, count in by_location.items():
		summary.append({"value": count, "label": location, "indicator": "Blue", "datatype": "Int"})
	return summary
