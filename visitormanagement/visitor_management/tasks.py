import frappe
from frappe.utils import add_to_date, get_datetime, getdate, now_datetime, nowdate


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
		fields=["name", "visit_date", "expected_checkout"],
	)

	flagged = 0
	for cand in candidates:
		if not cand.visit_date:
			continue

		# Build the deadline: visit_date + expected_checkout (or end of day) + grace.
		if cand.expected_checkout:
			deadline_str = f"{cand.visit_date} {cand.expected_checkout}"
		else:
			deadline_str = f"{cand.visit_date} 23:59:59"

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
