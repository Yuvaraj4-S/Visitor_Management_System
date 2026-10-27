# For license information, please see license.txt
"""Who is on site: the people, not the passes.

A group pass is one pass for many people. The head counts (Active Visitors,
Gate Wise Count, the "Checked-In Visitors" card and the Evacuation Roll Call)
counted passes, so a delegation of twelve was one visitor to the marshal. The
lead visitor is on site while the pass is "Checked-In", as before; each
accompanying visitor is on site while the gate has their member row ticked
`checked_in` (security_log._record_group_attendance) — which can outlast the
lead's own check-out when part of a group stays behind.
"""

import frappe

# Where a group member is now. While the lead is inside the group moves with the
# pass (`current_location`, which a Gate Transfer moves); once the lead has gone,
# a member left behind is wherever they came in.
_MEMBER_LOCATION = (
	"COALESCE(CASE WHEN vp.status = 'Checked-In' THEN NULLIF(vp.current_location, '') END, "
	"NULLIF(cl.gate_name, ''), NULLIF(vp.current_location, ''))"
)


def members_on_site(conditions=None, values=None, extra_fields=""):
	"""The accompanying visitors the gate has recorded as inside, one dict each.

	`conditions` are SQL fragments on the pass, aliased `vp` (a report's own
	row scope, a filter); `extra_fields` more columns to select, from the pass
	(`vp`) or its host (`emp`).
	"""
	where = [
		"m.parenttype = 'Visitor Pass'",
		"m.parentfield = 'group_members'",
		"m.checked_in = 1",
		# Not cancelled. Anyone the gate let in is listed, whatever else the pass says.
		"vp.docstatus < 2",
		"vp.is_group_visit = 1",
		"vp.status IN ('Checked-In', 'Checked-Out')",
		*(conditions or []),
	]
	return frappe.db.sql(
		f"""
        SELECT
            m.name AS member_row,
            m.parent AS visitor_pass,
            m.visitor_name,
            m.mobile_number,
            m.badge_number,
            m.checked_in_on AS checkin_time,
            vp.visitor_full_name AS lead_name,
            {_MEMBER_LOCATION} AS gate_name
            {extra_fields}
        FROM `tabVisitor Group Member` m
        INNER JOIN `tabVisitor Pass` vp ON vp.name = m.parent
        LEFT JOIN `tabSecurity Log` cl ON cl.name = m.check_in_log
        LEFT JOIN `tabEmployee` emp ON emp.name = vp.person_to_visit
        WHERE {" AND ".join(where)}
        ORDER BY m.checked_in_on, m.idx
        """,
		values or {},
		as_dict=True,
	)


def member_location_sql():
	"""The SQL expression for a member's current location (aliases `vp`, `cl`)."""
	return _MEMBER_LOCATION
