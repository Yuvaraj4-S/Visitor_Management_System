# For license information, please see license.txt
"""One row per gate visit: each Check-In paired with the Check-Out that ended it
(a multi-day pass has one row per day). Covers invitation, desk and walk-in passes."""

import frappe
from frappe import _
from frappe.utils import add_days, get_datetime, getdate, now_datetime, time_diff_in_seconds, today

STATUS_OPTIONS = ("Completed", "Not Checked Out", "Left Late")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	data = get_data(filters)
	return get_columns(), data, None, get_chart(data), get_summary(data)


def get_columns():
	return [
		{"label": _("Date"), "fieldname": "visit_day", "fieldtype": "Date", "width": 100},
		{"label": _("Visitor Pass"), "fieldname": "visitor_pass", "fieldtype": "Link", "options": "Visitor Pass", "width": 135},
		{"label": _("Visitor"), "fieldname": "visitor_name", "fieldtype": "Data", "width": 160},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Data", "width": 140},
		{"label": _("Type"), "fieldname": "visitor_type", "fieldtype": "Data", "width": 95},
		{"label": _("Host"), "fieldname": "host_name", "fieldtype": "Data", "width": 140},
		{"label": _("Channel"), "fieldname": "request_channel", "fieldtype": "Data", "width": 85},
		{"label": _("Expected"), "fieldname": "expected", "fieldtype": "Data", "width": 105},
		{"label": _("Check-In"), "fieldname": "check_in", "fieldtype": "Datetime", "width": 150},
		{"label": _("Check-Out"), "fieldname": "check_out", "fieldtype": "Datetime", "width": 150},
		{"label": _("Duration (min)"), "fieldname": "duration_min", "fieldtype": "Int", "width": 105},
		{"label": _("Overstay (min)"), "fieldname": "overstay_min", "fieldtype": "Int", "width": 105},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 125},
		{"label": _("ID Card No"), "fieldname": "id_card_number", "fieldtype": "Data", "width": 95},
		{"label": _("Card Returned"), "fieldname": "id_card_returned", "fieldtype": "Data", "width": 105},
		{"label": _("Gate"), "fieldname": "gate_name", "fieldtype": "Data", "width": 100},
		{"label": _("Check-In Log"), "fieldname": "checkin_log", "fieldtype": "Link", "options": "Security Log", "width": 120},
		{"label": _("Check-Out Log"), "fieldname": "checkout_log", "fieldtype": "Link", "options": "Security Log", "width": 120},
	]


def _fmt_time(value):
	return frappe.utils.format_time(value, "HH:mm") if value else ""


def get_data(filters):
	from_date = getdate(filters.get("from_date") or today())
	to_date = getdate(filters.get("to_date") or from_date)
	log_meta = frappe.get_meta("Security Log")
	card_fields = ", sl.id_card_number, sl.id_card_returned" if log_meta.has_field("id_card_number") else ""

	logs = frappe.db.sql(
		f"""
		select sl.name, sl.visitor_pass, sl.event_type, sl.gate_name,
			sl.check_in_date_time, sl.check_out_date_time {card_fields}
		from `tabSecurity Log` sl
		where sl.docstatus < 2 and sl.event_type in ('Check-In', 'Check-Out')
			and sl.visitor_pass in (
				select ci.visitor_pass from `tabSecurity Log` ci
				where ci.event_type = 'Check-In' and ci.docstatus < 2
					and date(ci.check_in_date_time) between %(from)s and %(to)s
			)
		order by sl.visitor_pass, coalesce(sl.check_in_date_time, sl.check_out_date_time), sl.creation
		""",
		{"from": from_date, "to": to_date},
		as_dict=True,
	)
	passes = {
		p.name: p
		for p in frappe.get_all(
			"Visitor Pass",
			filters={"name": ["in", list({l.visitor_pass for l in logs}) or [""]]},
			fields=["name", "visitor_full_name", "company__organisation", "visitor_type", "person_to_visit",
					"request_channel", "expected_checkin", "expected_checkout"],
		)
	}
	hosts = {
		e.name: e.employee_name
		for e in frappe.get_all(
			"Employee", filters={"name": ["in", list({p.person_to_visit for p in passes.values()}) or [""]]},
			fields=["name", "employee_name"],
		)
	}

	rows, open_visit = [], {}
	now = now_datetime()
	for log in logs:
		if log.event_type == "Check-In":
			if log.visitor_pass in open_visit:   # an earlier check-in never checked out
				rows.append(_row(open_visit.pop(log.visitor_pass), None, passes, hosts, now))
			open_visit[log.visitor_pass] = log
		elif log.visitor_pass in open_visit:
			rows.append(_row(open_visit.pop(log.visitor_pass), log, passes, hosts, now))
	rows.extend(_row(ci, None, passes, hosts, now) for ci in open_visit.values())

	rows = [r for r in rows if r and from_date <= r.visit_day <= to_date]
	if filters.get("status"):
		rows = [r for r in rows if r.status == filters.status or (filters.status == "Completed" and r.status == "Left Late")]
	for key, field in (("visitor_type", "visitor_type"), ("host", "person_to_visit"), ("request_channel", "request_channel")):
		if filters.get(key):
			rows = [r for r in rows if r.get(field) == filters.get(key)]
	if filters.get("card_not_returned"):
		rows = [r for r in rows if r.id_card_number and r.id_card_returned != _("Yes")]
	rows.sort(key=lambda r: (r.visit_day, r.check_in), reverse=True)
	return rows


def _row(checkin, checkout, passes, hosts, now):
	vp = passes.get(checkin.visitor_pass)
	if not vp or not checkin.check_in_date_time:
		return None
	check_in = get_datetime(checkin.check_in_date_time)
	check_out = get_datetime(checkout.check_out_date_time) if checkout and checkout.check_out_date_time else None
	deadline = get_datetime(f"{check_in.date()} {vp.expected_checkout}") if vp.expected_checkout else None
	end = check_out or now
	overstay = int(max(0, time_diff_in_seconds(end, deadline)) // 60) if deadline and end > deadline else 0
	if not check_out:
		status = _("Not Checked Out")
	elif overstay:
		status = _("Left Late")
	else:
		status = _("Completed")
	card_no = (checkout or {}).get("id_card_number") or checkin.get("id_card_number")
	return frappe._dict(
		visit_day=check_in.date(),
		visitor_pass=vp.name,
		visitor_name=vp.visitor_full_name,
		company=vp.company__organisation,
		visitor_type=vp.visitor_type,
		person_to_visit=vp.person_to_visit,
		host_name=hosts.get(vp.person_to_visit) or vp.person_to_visit,
		request_channel=vp.request_channel,
		expected=f"{_fmt_time(vp.expected_checkin)}–{_fmt_time(vp.expected_checkout)}",
		check_in=check_in,
		check_out=check_out,
		duration_min=int(time_diff_in_seconds(check_out, check_in) // 60) if check_out else None,
		overstay_min=overstay or None,
		status=status,
		id_card_number=card_no,
		id_card_returned=(_("Yes") if checkout.get("id_card_returned") else _("No")) if checkout and card_no else "",
		gate_name=checkin.gate_name,
		checkin_log=checkin.name,
		checkout_log=checkout.name if checkout else None,
	)


def get_summary(data):
	count = lambda s: sum(1 for r in data if r.status == s)
	cards_out = sum(1 for r in data if r.id_card_number and r.id_card_returned != _("Yes"))
	return [
		{"label": _("Visits"), "value": len(data), "indicator": "Blue"},
		{"label": _("Completed"), "value": count(_("Completed")) + count(_("Left Late")), "indicator": "Green"},
		{"label": _("Not Checked Out"), "value": count(_("Not Checked Out")), "indicator": "Red"},
		{"label": _("Left Late"), "value": count(_("Left Late")), "indicator": "Orange"},
		{"label": _("ID Cards Not Returned"), "value": cards_out, "indicator": "Red" if cards_out else "Green"},
	]


def get_chart(data):
	days = sorted({r.visit_day for r in data})
	if not days:
		return None
	per = lambda s: [sum(1 for r in data if r.visit_day == d and r.status == s) for d in days]
	return {
		"data": {
			"labels": [frappe.utils.formatdate(d) for d in days],
			"datasets": [
				{"name": _("Completed"), "values": per(_("Completed"))},
				{"name": _("Left Late"), "values": per(_("Left Late"))},
				{"name": _("Not Checked Out"), "values": per(_("Not Checked Out"))},
			],
		},
		"type": "bar",
		"barOptions": {"stacked": 1},
	}
