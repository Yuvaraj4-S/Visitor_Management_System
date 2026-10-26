# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import (
    add_months, flt, get_first_day, get_last_day, get_first_day_of_week,
    getdate, today,
)

# Every person (main visitor or group member) has their own Visitor Pass, so
# one counted pass == one person who actually came through the gate.
GROUP_COLUMNS = {
    "Day": ("Date", "DATE(vp.actual_checkin)", "Date", "Date"),
    "Month": ("Month", "DATE_FORMAT(vp.actual_checkin, '%%Y-%%m')", "Data", "Month"),
    "Visitor Type": ("Visitor Type", "vp.visitor_type", "Data", "Visitor Type"),
    "Department": ("Department", "vp.host_department", "Data", "Department"),
    "Host": ("Host", "vp.person_to_visit", "Link", "Host"),
    "Visitor List": ("Visitor", "DATE(vp.actual_checkin)", "Date", "Visitor"),
}


def execute(filters=None):
    filters = frappe._dict(filters or {})
    from_date, to_date = get_date_range(filters)
    group_by = filters.group_by or "Day"
    if group_by not in GROUP_COLUMNS:
        frappe.throw(_("Invalid Group By"))

    passes = get_passes(filters, from_date, to_date)
    if group_by == "Visitor List":
        return get_list_columns(), get_list_data(passes), None, None, get_summary(passes)
    data = get_data(passes, group_by)
    return get_columns(group_by), data, None, get_chart(data, group_by), get_summary(passes)


def get_date_range(filters):
    period = filters.period or "This Month"
    now = getdate(today())

    if period == "Today":
        return now, now
    if period == "This Week":
        start = get_first_day_of_week(now)
        return start, now
    if period == "This Month":
        return get_first_day(now), now
    if period == "Last Month":
        last = add_months(now, -1)
        return get_first_day(last), get_last_day(last)
    if period == "This Year":
        return getdate(f"{now.year}-01-01"), now
    if period == "Till Date":
        return None, now
    if period == "Custom":
        if not (filters.from_date and filters.to_date):
            frappe.throw(_("Please set From Date and To Date for a Custom period"))
        from_date, to_date = getdate(filters.from_date), getdate(filters.to_date)
        if from_date > to_date:
            frappe.throw(_("From Date cannot be after To Date"))
        return from_date, to_date
    frappe.throw(_("Invalid Period"))


def get_passes(filters, from_date, to_date):
    conditions = ["vp.docstatus < 2", "vp.actual_checkin IS NOT NULL"]
    values = {"to_date": to_date}
    conditions.append("DATE(vp.actual_checkin) <= %(to_date)s")
    if from_date:
        conditions.append("DATE(vp.actual_checkin) >= %(from_date)s")
        values["from_date"] = from_date
    if filters.visitor_type:
        conditions.append("vp.visitor_type = %(visitor_type)s")
        values["visitor_type"] = filters.visitor_type
    if filters.host:
        conditions.append("vp.person_to_visit = %(host)s")
        values["host"] = filters.host
    if filters.department:
        conditions.append("vp.host_department = %(department)s")
        values["department"] = filters.department

    group_by = filters.group_by or "Day"
    group_expr = GROUP_COLUMNS.get(group_by, GROUP_COLUMNS["Day"])[1]

    return frappe.db.sql(
        f"""
        SELECT
            {group_expr} AS grp,
            vp.name AS visitor_pass,
            vp.visitor_full_name AS visitor_name,
            vp.company__organisation AS company,
            vp.visitor_type,
            vp.person_to_visit AS host,
            vp.host_department AS department,
            vp.status,
            vp.actual_checkin,
            vp.actual_checkout,
            COALESCE(NULLIF(vp.id_proof_number, ''), NULLIF(vp.mobile_number, ''),
                     vp.visitor_full_name) AS person_key
        FROM `tabVisitor Pass` vp
        WHERE {" AND ".join(conditions)}
        ORDER BY vp.actual_checkin
        """,
        values,
        as_dict=True,
    )


def _duration_minutes(row):
    if row.actual_checkin and row.actual_checkout and row.actual_checkout >= row.actual_checkin:
        return (row.actual_checkout - row.actual_checkin).total_seconds() / 60
    return None


def get_data(passes, group_by):
    groups = {}
    for r in passes:
        key = r.grp if r.grp not in (None, "") else _("Not Specified")
        g = groups.setdefault(key, {"total": 0, "inside": 0, "mins": [],
                                          "names": {}, "companies": {}})
        g["total"] += 1
        if r.status == "Checked-In":
            g["inside"] += 1
        if r.visitor_name:
            g["names"][r.visitor_name] = 1
        if r.company:
            g["companies"][r.company] = 1
        m = _duration_minutes(r)
        if m is not None:
            g["mins"].append(m)

    # Day/Month read best oldest-first; the others, biggest-first.
    if group_by in ("Day", "Month"):
        keys = sorted(groups, key=str)
    else:
        keys = sorted(groups, key=lambda k: (-groups[k]["total"], str(k)))

    rows = []
    for k in keys:
        g = groups[k]
        rows.append({
            "group": k,
            "visitor_names": ", ".join(g["names"]),
            "visitor_companies": ", ".join(g["companies"]),
            "total_visitors": g["total"],
            "currently_inside": g["inside"],
            "avg_duration": round(sum(g["mins"]) / len(g["mins"]), 1) if g["mins"] else 0,
        })
    return rows


def get_list_data(passes):
    rows = []
    for r in passes:
        mins = _duration_minutes(r)
        rows.append({
            "visit_date": r.actual_checkin.date() if r.actual_checkin else None,
            "visitor_pass": r.visitor_pass,
            "visitor_name": r.visitor_name,
            "company": r.company,
            "visitor_type": r.visitor_type,
            "host": r.host,
            "department": r.department,
            "checkin": r.actual_checkin,
            "checkout": r.actual_checkout,
            "duration": round(mins, 1) if mins is not None else None,
            "status": r.status,
        })
    return rows


def get_list_columns():
    return [
        {"label": _("Date"), "fieldname": "visit_date", "fieldtype": "Date", "width": 100},
        {"label": _("Visitor Pass"), "fieldname": "visitor_pass", "fieldtype": "Link",
         "options": "Visitor Pass", "width": 140},
        {"label": _("Visitor Name"), "fieldname": "visitor_name", "fieldtype": "Data", "width": 170},
        {"label": _("Visitor Company"), "fieldname": "company", "fieldtype": "Data", "width": 170},
        {"label": _("Visitor Type"), "fieldname": "visitor_type", "fieldtype": "Data", "width": 110},
        {"label": _("Host"), "fieldname": "host", "fieldtype": "Link", "options": "Employee", "width": 130},
        {"label": _("Department"), "fieldname": "department", "fieldtype": "Data", "width": 140},
        {"label": _("Check-In"), "fieldname": "checkin", "fieldtype": "Datetime", "width": 155},
        {"label": _("Check-Out"), "fieldname": "checkout", "fieldtype": "Datetime", "width": 155},
        {"label": _("Duration (min)"), "fieldname": "duration", "fieldtype": "Float", "width": 120},
        {"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 110},
    ]


def get_columns(group_by):
    label, _expr, fieldtype, _l = GROUP_COLUMNS[group_by]
    group_col = {"label": _(label), "fieldname": "group", "fieldtype": fieldtype, "width": 200}
    if fieldtype == "Link":
        group_col["options"] = "Employee"
    return [
        group_col,
        {"label": _("Visitor Name"), "fieldname": "visitor_names", "fieldtype": "Data", "width": 260},
        {"label": _("Visitor Company"), "fieldname": "visitor_companies", "fieldtype": "Data", "width": 220},
        {"label": _("Total Visitors"), "fieldname": "total_visitors", "fieldtype": "Int", "width": 130},
        {"label": _("Currently Inside"), "fieldname": "currently_inside", "fieldtype": "Int", "width": 130},
        {"label": _("Avg Duration (min)"), "fieldname": "avg_duration", "fieldtype": "Float", "width": 150},
    ]


def get_summary(passes):
    mins = [m for m in (_duration_minutes(r) for r in passes) if m is not None]
    avg = sum(mins) / len(mins) if mins else 0
    return [
        {"value": len(passes), "label": _("Total Visitors"), "indicator": "Blue", "datatype": "Int"},
        {"value": sum(1 for r in passes if r.status == "Checked-In"), "label": _("Currently Inside"),
         "indicator": "Orange", "datatype": "Int"},
        {"value": flt(avg, 1), "label": _("Avg Duration (min)"), "indicator": "Grey", "datatype": "Float"},
    ]


def get_chart(data, group_by):
    if not data:
        return None
    return {
        "type": "line" if group_by in ("Day", "Month") else "bar",
        "data": {
            "labels": [str(r["group"]) for r in data],
            "datasets": [{"name": _("Visitors"), "values": [r["total_visitors"] for r in data]}],
        },
    }
