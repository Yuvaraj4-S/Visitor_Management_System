import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime, getdate, now_datetime, nowdate

from visitormanagement.visitor_management.multi_day import can_check_in_again, get_visit_end_date, is_valid_on
from visitormanagement.visitor_management.time_utils import format_time_hhmm


DIGEST_RECIPIENTS_BY_ROLE = [
	"Hospitality Manager",
	"Hospitality User",
	"Transport Coordinator",
	"Front Office Executive",
	"Factory Tour Coordinator",
	"Greeting Staff",
]


def _get_recipients():
	users = frappe.get_all(
		"Has Role",
		filters={
			"role": ("in", DIGEST_RECIPIENTS_BY_ROLE),
			"parenttype": "User",
		},
		fields=["parent"],
	)
	emails = set()
	for u in users:
		enabled, email = frappe.db.get_value(
			"User", u.parent, ["enabled", "email"]
		) or (0, None)
		if enabled and email:
			emails.add(email)
	return sorted(emails)


def _fetch_today_rows(today):
	return frappe.get_all(
		"Hospitality Request",
		filters={
			"status": ("not in", ("Cancelled", "Completed")),
		},
		or_filters=[
			["pickup_datetime", "between", [f"{today} 00:00:00", f"{today} 23:59:59"]],
			["drop_datetime", "between", [f"{today} 00:00:00", f"{today} 23:59:59"]],
			["check_in", "=", today],
			["tour_date", "=", today],
			["buggy_datetime", "between", [f"{today} 00:00:00", f"{today} 23:59:59"]],
			["greeting_delivery_time", "between", [f"{today} 00:00:00", f"{today} 23:59:59"]],
		],
		fields=[
			"name", "visitor_pass", "status",
			"cab_required", "cab_type", "pickup_location", "pickup_datetime",
			"drop_location", "drop_datetime", "driver_name",
			"hotel_required", "hotel_name", "check_in", "booking_reference",
			"factory_tour_required", "tour_date", "tour_start_time", "tour_guide",
			"buggy_required", "buggy_pickup_point", "buggy_datetime", "buggy_driver",
			"greeting_required", "greeting_type", "greeting_delivery_time", "greeting_assigned_to",
		],
	)


def _build_html(today, rows):
	cabs, hotels, tours, buggies, greetings = [], [], [], [], []
	for r in rows:
		if r.cab_required and (
			(r.pickup_datetime and getdate(r.pickup_datetime) == getdate(today))
			or (r.drop_datetime and getdate(r.drop_datetime) == getdate(today))
		):
			cabs.append(r)
		if r.hotel_required and r.check_in and getdate(r.check_in) == getdate(today):
			hotels.append(r)
		if r.factory_tour_required and r.tour_date and getdate(r.tour_date) == getdate(today):
			tours.append(r)
		if r.buggy_required and r.buggy_datetime and getdate(r.buggy_datetime) == getdate(today):
			buggies.append(r)
		if r.greeting_required and r.greeting_delivery_time and getdate(r.greeting_delivery_time) == getdate(today):
			greetings.append(r)

	def section(title, items, render_row):
		if not items:
			return f"<h3>{title} (0)</h3><p style='color:#829ab1'>None scheduled.</p>"
		html = [f"<h3>{title} ({len(items)})</h3><ul>"]
		for r in items:
			html.append(f"<li>{render_row(r)}</li>")
		html.append("</ul>")
		return "".join(html)

	parts = [
		f"<h2 style='color:#102a43'>Hospitality Schedule — {today}</h2>",
		section("🚗 Cabs", cabs, lambda r: (
			f"{r.pickup_datetime or r.drop_datetime} — {r.cab_type} — "
			f"{r.pickup_location or r.drop_location or '-'} "
			f"(Driver: {r.driver_name or 'Not assigned'}) "
			f"[{r.status or 'Pending'}] — {r.visitor_pass}"
		)),
		section("🏨 Hotel Check-ins", hotels, lambda r: (
			f"{r.hotel_name or '-'} — Ref: {r.booking_reference or '-'} "
			f"[{r.status or 'Pending'}] — {r.visitor_pass}"
		)),
		section("🏭 Factory Tours", tours, lambda r: (
			f"{r.tour_start_time or '-'} — Guide: {r.tour_guide or 'Not assigned'} "
			f"[{r.status or 'Pending'}] — {r.visitor_pass}"
		)),
		section("🛺 Buggy Requests", buggies, lambda r: (
			f"{r.buggy_datetime} — {r.buggy_pickup_point or '-'} — "
			f"Driver: {r.buggy_driver or 'Not assigned'} "
			f"[{r.status or 'Pending'}] — {r.visitor_pass}"
		)),
		section("🎁 Greetings", greetings, lambda r: (
			f"{r.greeting_delivery_time} — {r.greeting_type or '-'} — "
			f"Assigned: {r.greeting_assigned_to or 'Not assigned'} "
			f"[{r.status or 'Pending'}] — {r.visitor_pass}"
		)),
	]
	return "<div style='font-family:Arial,sans-serif;font-size:13px;color:#1f2933'>" + "".join(parts) + "</div>"


def send_daily_hospitality_digest():
	today = nowdate()
	rows = _fetch_today_rows(today)
	if not rows:
		return

	recipients = _get_recipients()
	if not recipients:
		return

	frappe.sendmail(
		recipients=recipients,
		subject=f"Today's Hospitality Schedule — {today}",
		message=_build_html(today, rows),
		reference_doctype="Hospitality Request",
		now=False,
	)


# ---------------------------------------------------------------------------
# No-show detection
# ---------------------------------------------------------------------------
# Hourly job. Flags Visitor Passes that were Approved/Items-Verified but never
# checked-in past their expected_checkout + grace window. Marks no_show=1 and
# logs a Visitor Event so admins can audit. Idempotent: passes already flagged
# are skipped.
NO_SHOW_GRACE_HOURS = 4


def flag_no_show_passes():
	"""Set no_show=1 on Visitor Passes that missed their visit window.

	A pass is a no-show when:
	  - status in (Approved, Items Verified)  -- never made it past the gate
	  - visit_date + expected_checkout + grace is in the past
	  - no_show is currently 0
	"""
	now = now_datetime()
	candidates = frappe.get_all(
		"Visitor Pass",
		filters={
			"status": ["in", ["Approved", "Items Verified"]],
			"no_show": 0,
			"docstatus": ["<", 2],
		},
		fields=["name", "visit_date", "expected_checkout", "multi_day_pass", "pass_valid_until"],
	)

	flagged = 0
	for cand in candidates:
		if not cand.visit_date:
			continue

		# Build the deadline: last visit day + expected_checkout (or end of day) + grace.
		# A multi-day visit is only a no-show once its end date has passed.
		last_day = get_visit_end_date(cand)
		if cand.expected_checkout:
			deadline_str = f"{last_day} {cand.expected_checkout}"
		else:
			deadline_str = f"{last_day} 23:59:59"

		try:
			deadline = get_datetime(deadline_str)
		except Exception:
			continue

		deadline = add_to_date(deadline, hours=NO_SHOW_GRACE_HOURS)
		if now < deadline:
			continue

		frappe.db.set_value(
			"Visitor Pass",
			cand.name,
			{"no_show": 1, "current_location": "No Show"},
			update_modified=False,
		)
		try:
			from visitormanagement.visitor_management.lifecycle import log_visitor_event
			log_visitor_event(
				cand.name,
				"No Show",
				event_status="Auto-flagged",
				source_doctype="Scheduled Task",
				source_name="flag_no_show_passes",
				details={"deadline": str(deadline), "grace_hours": NO_SHOW_GRACE_HOURS},
			)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "flag_no_show_passes log_visitor_event")
		flagged += 1


# ---------------------------------------------------------------------------
# Host morning reminder
# ---------------------------------------------------------------------------
# Daily job. Emails each host (Person to Visit) one list of the approved visitors
# coming to see them today — including multi-day visitors returning for another day.
HOST_REMINDER_STATUSES = ("Approved", "Items Verified", "Checked-Out")


def _visitors_due_today(today):
	rows = frappe.get_all(
		"Visitor Pass",
		filters={
			"docstatus": 1,
			"status": ("in", HOST_REMINDER_STATUSES),
			"visit_date": ("<=", today),
			"no_show": 0,
			"person_to_visit": ("is", "set"),
		},
		or_filters=[
			["visit_date", "=", today],
			["pass_valid_until", ">=", today],
		],
		fields=[
			"name", "person_to_visit", "visitor_full_name", "company__organisation", "visitor_type",
			"purpose_of_visit", "visit_date", "expected_checkin", "expected_checkout", "status",
			"multi_day_pass", "pass_valid_until", "number_of_people",
		],
		order_by="expected_checkin asc",
	)
	# Checked-Out only counts for a multi-day visit that comes back today.
	return [
		r for r in rows
		if is_valid_on(r, today) and (r.status != "Checked-Out" or can_check_in_again(r, today))
	]


def _build_host_reminder_html(today, host_name, rows):
	esc = frappe.utils.escape_html
	td = "padding:6px 8px;border:1px solid #d9e2ec;"
	body = []
	for r in rows:
		window = f"{format_time_hhmm(r.expected_checkin, '-')} – {format_time_hhmm(r.expected_checkout, '-')}"
		visit_note = ""
		if r.multi_day_pass and r.pass_valid_until:
			visit_note = (
				f"<br><span style='color:#627d98'>Multi-day visit: {r.visit_date} to {r.pass_valid_until}</span>"
			)
		people = f" (+{int(r.number_of_people) - 1})" if (r.number_of_people or 0) > 1 else ""
		link = frappe.utils.get_url_to_form("Visitor Pass", r.name)
		body.append(
			f"<tr><td style='{td}'>{window}</td>"
			f"<td style='{td}'><b>{esc(r.visitor_full_name or '-')}</b>{people}{visit_note}</td>"
			f"<td style='{td}'>{esc(r.company__organisation or '-')}</td>"
			f"<td style='{td}'>{esc(r.visitor_type or '-')}</td>"
			f"<td style='{td}'>{esc(r.purpose_of_visit or '-')}</td>"
			f"<td style='{td}'><a href='{link}'>{r.name}</a></td></tr>"
		)
	return (
		"<div style='font-family:Arial,sans-serif;font-size:13px;color:#1f2933'>"
		f"<p>Dear {esc(host_name or 'Host')},</p>"
		f"<p>These visitors are coming to see you today (<b>{frappe.utils.formatdate(today)}</b>):</p>"
		"<table style='border-collapse:collapse;width:100%'>"
		f"<tr style='background:#f0f4f8'><th style='{td}'>Time</th><th style='{td}'>Visitor</th>"
		f"<th style='{td}'>Company</th><th style='{td}'>Type</th><th style='{td}'>Purpose</th>"
		f"<th style='{td}'>Pass</th></tr>"
		+ "".join(body)
		+ "</table>"
		"<p style='color:#627d98'>Security will notify you when each visitor checks in at the gate. "
		"If a visit needs more time, use <b>Extend Visit Time</b> on the Visitor Pass.</p>"
		"</div>"
	)


def send_host_daily_visitor_reminder():
	"""One morning email per host listing today's approved visitors."""
	today = getdate(nowdate())
	by_host = {}
	for r in _visitors_due_today(today):
		by_host.setdefault(r.person_to_visit, []).append(r)

	for host, rows in by_host.items():
		employee = frappe.db.get_value(
			"Employee", host, ["employee_name", "company_email", "personal_email", "user_id"], as_dict=True
		)
		email = employee and (employee.company_email or employee.personal_email or employee.user_id)
		if not email or "@" not in email:
			continue
		try:
			frappe.sendmail(
				recipients=[email],
				subject=_("Your visitors today — {0} ({1})").format(frappe.utils.formatdate(today), len(rows)),
				message=_build_host_reminder_html(today, employee.employee_name, rows),
				reference_doctype="Employee",
				reference_name=host,
				now=False,
			)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "send_host_daily_visitor_reminder")
