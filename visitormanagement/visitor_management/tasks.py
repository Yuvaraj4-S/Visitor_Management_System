from functools import partial

import frappe
from frappe import _
from frappe.model.naming import make_autoname
from frappe.query_builder.functions import IfNull
from frappe.utils import (
	add_days,
	add_to_date,
	cint,
	get_datetime,
	get_url_to_form,
	getdate,
	now_datetime,
	nowdate,
	time_diff_in_hours,
	to_timedelta,
)
from pypika.terms import Function

from visitormanagement.visitor_management import settings as vms_settings
from visitormanagement.visitor_management.lifecycle import _format_event_details, log_visitor_event
from visitormanagement.visitor_management.mail import esc

# Recipient roles are configured in VMS Settings; see
# visitor_management.settings.digest_recipient_roles for the fallback list.


def _get_recipients(roles=None):
	"""Enabled users holding any of `roles` (default: the digest roles)."""
	users = frappe.get_all(
		"Has Role",
		filters={
			"role": ("in", roles or vms_settings.digest_recipient_roles()),
			"parenttype": "User",
		},
		fields=["parent"],
	)
	emails = set()
	for u in users:
		enabled, email = frappe.db.get_value("User", u.parent, ["enabled", "email"]) or (0, None)
		if enabled and email:
			emails.add(email)
	return sorted(emails)


# The workflow states in which a Hospitality Request is something to act on: sent
# for approval, or approved. A Draft was never submitted, and a Rejected or
# Cancelled one has been turned down - none of them is a cab to send or a greeting
# to prepare. The digest and the workspace's "Today's ..." cards count the same
# requests, so the mail and the dashboard cannot give two different numbers.
LIVE_HOSPITALITY_STATES = ("Pending Approval", "Approved")


# The request fields that put a request on a day's digest, besides its meal.
_SCHEDULE_FIELDS = (
	"pickup_datetime",
	"drop_datetime",
	"check_in",
	"tour_date",
	"buggy_datetime",
	"greeting_delivery_time",
)


def _meal_today(row, today):
	"""Whether a meal is served on this request today: at its service time, else on the visit day."""
	meal_day = row.service_time or row.visit_start_time
	return bool(row.meal_required and meal_day and getdate(meal_day) == getdate(today))


def _fetch_today_rows(today):
	rows = _fetch_candidate_rows(today)
	# `visit_start_time` is in the filter only to find meals with no service time
	# set; a visit today with nothing arranged for it is not on the digest.
	return [
		r
		for r in rows
		if _meal_today(r, today)
		or any(r.get(f) and getdate(r.get(f)) == getdate(today) for f in _SCHEDULE_FIELDS)
	]


def _fetch_candidate_rows(today):
	return frappe.get_all(
		"Hospitality Request",
		filters={
			"status": ("not in", ("Cancelled", "Completed")),
			"workflow_state": ("in", LIVE_HOSPITALITY_STATES),
		},
		or_filters=[
			["pickup_datetime", "between", [f"{today} 00:00:00", f"{today} 23:59:59"]],
			["drop_datetime", "between", [f"{today} 00:00:00", f"{today} 23:59:59"]],
			["check_in", "=", today],
			["tour_date", "=", today],
			["buggy_datetime", "between", [f"{today} 00:00:00", f"{today} 23:59:59"]],
			["greeting_delivery_time", "between", [f"{today} 00:00:00", f"{today} 23:59:59"]],
			# Meals: served today, or — when no service time is set — for a visit today.
			["service_time", "between", [f"{today} 00:00:00", f"{today} 23:59:59"]],
			["visit_start_time", "between", [f"{today} 00:00:00", f"{today} 23:59:59"]],
		],
		fields=[
			"name",
			"visitor_pass",
			"visitor_name_display",
			"status",
			"cab_required",
			"cab_type",
			"pickup_location",
			"pickup_datetime",
			"drop_location",
			"drop_datetime",
			"driver_name",
			"hotel_required",
			"hotel_name",
			"check_in",
			"booking_reference",
			"factory_tour_required",
			"tour_date",
			"tour_start_time",
			"tour_guide",
			"buggy_required",
			"buggy_pickup_point",
			"buggy_datetime",
			"buggy_driver",
			"greeting_required",
			"greeting_type",
			"greeting_delivery_time",
			"greeting_assigned_to",
			"meal_required",
			"meal_type",
			"assigned_meal_slots",
			"service_time",
			"visit_start_time",
			"special_diet",
			"dietary_allergies",
		],
	)


def _build_html(today, rows):
	cabs, hotels, tours, buggies, greetings, meals = [], [], [], [], [], []
	for r in rows:
		if _meal_today(r, today):
			meals.append(r)
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
		if (
			r.greeting_required
			and r.greeting_delivery_time
			and getdate(r.greeting_delivery_time) == getdate(today)
		):
			greetings.append(r)

	def section(title, items, render_row):
		if not items:
			return f"<h3>{title} (0)</h3><p style='color:#829ab1'>None scheduled.</p>"
		html = [f"<h3>{title} ({len(items)})</h3><ul>"]
		for r in items:
			# Driver names, booking references and pickup points are typed by staff;
			# escaped so a planted link or image cannot reach the digest's readers.
			# A link to the request itself, so the kitchen or the driver desk opens
			# it in one click instead of searching for a pass number.
			link = f" — <a href='{esc(get_url_to_form('Hospitality Request', r.name))}'>{esc(r.name)}</a>"
			html.append(f"<li>{render_row(frappe._dict({k: esc(v, None) for k, v in r.items()}))}{link}</li>")
		html.append("</ul>")
		return "".join(html)

	parts = [
		f"<h2 style='color:#102a43'>Hospitality Schedule — {today}</h2>",
		section(
			"🚗 Cabs",
			cabs,
			# nosemgrep: string-concat-in-list - one string split over lines on purpose, not a missing comma
			lambda r: (
				f"{r.pickup_datetime or r.drop_datetime} — {r.cab_type} — "
				f"{r.pickup_location or r.drop_location or '-'} "
				f"(Driver: {r.driver_name or 'Not assigned'}) "
				f"[{r.status or 'Pending'}] — {r.visitor_pass}"
			),
		),
		section(
			"🏨 Hotel Check-ins",
			hotels,
			# nosemgrep: string-concat-in-list - one string split over lines on purpose, not a missing comma
			lambda r: (
				f"{r.hotel_name or '-'} — Ref: {r.booking_reference or '-'} "
				f"[{r.status or 'Pending'}] — {r.visitor_pass}"
			),
		),
		section(
			"🏭 Factory Tours",
			tours,
			# nosemgrep: string-concat-in-list - one string split over lines on purpose, not a missing comma
			lambda r: (
				f"{r.tour_start_time or '-'} — Guide: {r.tour_guide or 'Not assigned'} "
				f"[{r.status or 'Pending'}] — {r.visitor_pass}"
			),
		),
		section(
			"🛺 Buggy Requests",
			buggies,
			# nosemgrep: string-concat-in-list - one string split over lines on purpose, not a missing comma
			lambda r: (
				f"{r.buggy_datetime} — {r.buggy_pickup_point or '-'} — "
				f"Driver: {r.buggy_driver or 'Not assigned'} "
				f"[{r.status or 'Pending'}] — {r.visitor_pass}"
			),
		),
		section(
			"🎁 Greetings",
			greetings,
			# nosemgrep: string-concat-in-list - one string split over lines on purpose, not a missing comma
			lambda r: (
				f"{r.greeting_delivery_time} — {r.greeting_type or '-'} — "
				f"Assigned: {r.greeting_assigned_to or 'Not assigned'} "
				f"[{r.status or 'Pending'}] — {r.visitor_pass}"
			),
		),
		section("🍽️ Meals", meals, _meal_line),
	]
	return (
		"<div style='font-family:Arial,sans-serif;font-size:13px;color:#1f2933'>" + "".join(parts) + "</div>"
	)


def _meal_line(r):
	"""One meal row of the digest. `r` is already escaped (see `section`).

	Allergies are the one thing on this mail that can hurt somebody, so they are
	set apart in bold red rather than left inside the line.
	"""
	line = (
		f"{r.service_time or r.visit_start_time or '-'} — {r.visitor_name_display or '-'} — "
		f"{r.meal_type or '-'} (Slots: {r.assigned_meal_slots or '-'}) — "
		f"Diet: {r.special_diet or 'None'} [{r.status or 'Pending'}] — {r.visitor_pass}"
	)
	if r.dietary_allergies:
		line += f"<br><span style='color:#b42318;font-weight:bold'>⚠ Allergies: {r.dietary_allergies}</span>"
	return line


def send_daily_hospitality_digest():
	today = nowdate()
	rows = _fetch_today_rows(today)
	if not rows:
		return

	recipients = _get_recipients()
	if not recipients:
		return

	try:
		frappe.sendmail(
			recipients=recipients,
			subject=f"Today's Hospitality Schedule — {today}",
			message=_build_html(today, rows),
			reference_doctype="Hospitality Request",
			now=False,
		)
	except Exception as exc:
		# No outgoing mail account is the normal state of a new site. The job then
		# failed with a bare traceback in Scheduled Job Log every morning; say what
		# was not sent, where an administrator looks for it.
		frappe.log_error(f"Daily hospitality digest not sent: {exc}", "VMS Hospitality Digest")


# ---------------------------------------------------------------------------
# No-show detection
# ---------------------------------------------------------------------------
# Hourly job. Flags Visitor Passes that were Approved/Items-Verified but never
# checked-in past their expected_checkout + grace window. Marks no_show=1 and
# logs a Visitor Event so admins can audit. Idempotent: passes already flagged
# are skipped.
# Fallback only — the live value comes from VMS Settings.
NO_SHOW_GRACE_HOURS = 4

# Both jobs below batch their DB writes instead of doing one round trip per
# row (measured at ~16,800 round trips / 199s for 8,393 candidates on a
# 50k-row seed). `IN (...)` lists and bulk inserts are chunked at this size
# so a single statement never grows unbounded.
_CHUNK_SIZE = 500


def _chunked(items, size=_CHUNK_SIZE):
	for i in range(0, len(items), size):
		yield items[i : i + size]


_VEL_COLUMNS = [
	"name",
	"creation",
	"modified",
	"modified_by",
	"owner",
	"docstatus",
	"idx",
	"visitor_pass",
	"event_type",
	"event_status",
	"event_time",
	"source_doctype",
	"source_name",
	"details",
]


def _event_log_names(count):
	"""`count` new Visitor Event Log names, the way the DocType names its own ("hash").

	No counter: a "VEL-.YYYY.-.#####" series made every event-log insert lock its
	`tabSeries` row until the transaction ended, and an hourly job holding a block
	of names kept the guard's check-in waiting until it failed with "Lock wait
	timeout exceeded". A random name takes no lock, so a job and the gate never
	queue behind each other. Unique within the batch; across batches the random
	part makes a clash as unlikely as for any hash-named document.
	"""
	names = set()
	while len(names) < count:
		names.add(make_autoname("hash", "Visitor Event Log"))
	return list(names)


def _bulk_write_event_logs(rows):
	"""Bulk-insert Visitor Event Log rows built as dicts with `_VEL_COLUMNS` keys.

	Equivalent to calling `log_visitor_event(...)` once per row when no
	matching log already exists (the common case for both jobs below), but as
	one `INSERT ... VALUES (...), (...), ...` per chunk instead of one
	`doc.insert()` per row.
	"""
	if not rows:
		return
	values = [
		[name] + [row.get(col) for col in _VEL_COLUMNS if col != "name"]
		for name, row in zip(_event_log_names(len(rows)), rows, strict=True)
	]
	frappe.db.bulk_insert("Visitor Event Log", _VEL_COLUMNS, values, chunk_size=_CHUNK_SIZE)


def _planned_end(day, expected_checkin, expected_checkout):
	"""When a stay that starts on `day` is planned to end, or None without a check-out time.

	A check-out time at or before the check-in time is an overnight visit: the
	visitor leaves the next morning (a 22:00-06:00 shift). Reading 06:00 as the
	same day put the end of the visit before its start, so the no-show and
	overstay jobs flagged such visitors hours early.
	"""
	if not expected_checkout:
		return None
	end = get_datetime(f"{getdate(day)} {expected_checkout}")
	if expected_checkin and to_timedelta(expected_checkout) <= to_timedelta(expected_checkin):
		end = add_to_date(end, days=1)
	return end


def _stay_day(actual_checkin, expected_checkin, expected_checkout):
	"""The day a stay belongs to. An overnight visitor who arrives after midnight
	(00:30 on a 22:00-06:00 shift) is on the previous day's shift; reading the
	arrival date as the start put the planned end a whole day late."""
	arrived = get_datetime(actual_checkin)
	day = getdate(arrived)
	if (
		expected_checkin
		and expected_checkout
		and to_timedelta(expected_checkout) <= to_timedelta(expected_checkin)
		and to_timedelta(arrived.strftime("%H:%M:%S")) < to_timedelta(expected_checkout)
	):
		day = add_days(day, -1)
	return day


def flag_no_show_passes():
	"""Set no_show=1 on Visitor Passes that missed their visit window.

	A pass is a no-show when:
	  - status in (Approved, Items Verified)  -- never made it past the gate
	  - its last valid day + expected_checkout + grace is in the past
	  - no_show is currently 0

	The last valid day is `visit_date`, or `pass_valid_until` for a multi-day
	pass. Measuring a multi-day pass from its first day flagged it "No Show" that
	same evening, while the gate would still admit the visitor for days. An
	overnight visit ends the morning after that day (`_planned_end`).
	"""
	now = now_datetime()
	grace_hours = vms_settings.no_show_grace_hours()
	candidates = frappe.get_all(
		"Visitor Pass",
		filters={
			"status": ["in", ["Approved", "Items Verified"]],
			"no_show": 0,
			"docstatus": ["<", 2],
		},
		fields=[
			"name",
			"visit_date",
			"expected_checkin",
			"expected_checkout",
			"multi_day_pass",
			"pass_valid_until",
		],
		# Order doesn't matter here — every candidate is evaluated regardless
		# of order — and the implicit "modified desc" default forces a
		# filesort alongside these unindexed filters. Skip it.
		order_by=None,
	)

	to_flag = []  # [(name, deadline)]
	for cand in candidates:
		if not cand.visit_date:
			continue

		last_day = cand.visit_date
		if cint(cand.multi_day_pass) and cand.pass_valid_until:
			last_day = max(getdate(cand.pass_valid_until), getdate(cand.visit_date))

		# Build the deadline: last valid day + expected_checkout (or end of day) + grace.
		try:
			deadline = _planned_end(last_day, cand.expected_checkin, cand.expected_checkout)
			deadline = deadline or get_datetime(f"{last_day} 23:59:59")
		except Exception:
			continue

		deadline = add_to_date(deadline, hours=grace_hours)
		if now < deadline:
			continue

		to_flag.append((cand.name, deadline))

	if not to_flag:
		return

	# Hoisted out of the per-candidate loop: this Scheduled Job Type lookup
	# used a leading-wildcard LIKE, and it does not vary per candidate — it
	# was previously re-run once per flagged pass for no reason.
	job_name = frappe.db.get_value(
		"Scheduled Job Type",
		{"method": ["like", "%flag_no_show_passes%"]},
		"name",
	)

	names = [name for name, _ in to_flag]
	user = frappe.session.user
	# Reuse the `now` snapshot taken above rather than calling now_datetime()
	# again per row — the original also called it fresh per log_visitor_event,
	# microseconds apart; one snapshot for the whole batch changes nothing a
	# reader or a test could observe.
	now_ts = now

	# log_visitor_event() upserts: when source_doctype+source_name already
	# matches an existing log for this visitor_pass/event_type it updates
	# that row instead of inserting a new one. Reproduce that with one bulk
	# lookup instead of one `exists()` per candidate. In practice this job's
	# own no_show=0 filter means a given pass only ever passes through here
	# once, so `existing` is expected to be empty — but the fallback is kept
	# so the audit trail is provably identical even if that ever isn't true
	# (e.g. no_show manually reset on an already-logged pass).
	existing = {}
	for chunk in _chunked(names):
		for r in frappe.get_all(
			"Visitor Event Log",
			filters={
				"visitor_pass": ("in", chunk),
				"source_doctype": "Scheduled Job Type",
				"source_name": job_name,
				"event_type": "No Show",
			},
			fields=["name", "visitor_pass"],
		):
			existing[r.visitor_pass] = r.name

	insert_rows = []
	for name, deadline in to_flag:
		details = _format_event_details({"deadline": str(deadline), "grace_hours": grace_hours})
		if name in existing:
			frappe.db.set_value(
				"Visitor Event Log",
				existing[name],
				{
					"event_status": "Auto-flagged",
					"event_time": now_ts,
					"details": details,
				},
			)
		else:
			insert_rows.append(
				{
					"creation": now_ts,
					"modified": now_ts,
					"modified_by": user,
					"owner": user,
					"docstatus": 0,
					"idx": 0,
					"visitor_pass": name,
					"event_type": "No Show",
					"event_status": "Auto-flagged",
					"event_time": now_ts,
					"source_doctype": "Scheduled Job Type",
					"source_name": job_name,
					"details": details,
				}
			)

	_bulk_write_event_logs(insert_rows)

	# One bulk UPDATE per chunk instead of one `db.set_value()` per pass.
	# `db.set_value()` also clears each document's cache entry (in-process
	# value cache + the Redis document cache) as a side effect the raw SQL
	# UPDATE does not get for free -- reproduce it explicitly so a subsequent
	# `get_cached_doc("Visitor Pass", ...)` in the same worker cannot read a
	# stale no_show=0 copy.
	# The WHERE repeats the candidate filters instead of trusting `names`.
	#
	# Batching opened a time-of-check/time-of-use window that the per-row
	# version effectively did not have: it wrote each pass immediately after
	# evaluating it, so the gap was microseconds, whereas this version selects
	# every candidate, writes every event log, and only then updates — a window
	# spanning the whole job (~11s for 50k candidates, measured). A visitor who
	# checks in inside that window has `current_location` set to their real gate
	# by `sync_contact_trace`; an unguarded `where name in (...)` would then
	# overwrite it with "No Show" and mark a person who is physically on site as
	# absent, corrupting the one question the gate screen exists to answer.
	#
	# Re-asserting the filters makes the write conditional: a row that changed
	# state in the interim simply falls out of the batch rather than being
	# stomped. The filters are the same three the candidate query used above.
	#
	# A raw UPDATE never loads the document, so on_change never runs and no
	# Notification watching `no_show` is evaluated: the No-Show alert had never
	# fired for any pass. The before-images are read first, so the alerts can be
	# shown the transition afterwards (`_notify_no_show`).
	# VMS Settings may switch the host's no-show alert off (`_notify_no_show`):
	# then there is nobody to tell and no before-image is read.
	alerts_watching = vms_settings.feature_on("alert_host_on_no_show") and bool(
		frappe.get_all(
			"Notification",
			filters={"enabled": 1, "document_type": "Visitor Pass", "event": "Value Change"},
			limit=1,
		)
	)
	visitor_pass = frappe.qb.DocType("Visitor Pass")
	for chunk in _chunked(names):
		before_images = (
			{name: frappe.get_doc("Visitor Pass", name) for name in chunk} if alerts_watching else {}
		)
		(
			frappe.qb.update(visitor_pass)
			.set(visitor_pass.no_show, 1)
			.set(visitor_pass.current_location, "No Show")
			.where(visitor_pass.name.isin(chunk))
			.where(visitor_pass.no_show == 0)
			.where(visitor_pass.docstatus < 2)
			.where(visitor_pass.status.isin(("Approved", "Items Verified")))
			.run()
		)
		for name in chunk:
			frappe.clear_document_cache("Visitor Pass", name)
		for name, before in before_images.items():
			_notify_no_show(name, before)


def _notify_no_show(name, before):
	"""Let the Value-Change alerts see a pass become a no-show.

	The same hand-run pass `SecurityLog._advance_pass` makes after its own
	db.set_value, for the same reason: evaluate_alert diffs the document against
	its before-image to decide a field changed. A pass the conditional UPDATE
	skipped (it was checked in meanwhile) is still `no_show = 0` and is left alone.

	An alert that cannot be delivered must not stop the job: the passes are
	already flagged, and the rest of the batch still has to be told.

	Not at all when VMS Settings -> "Tell the host when a visitor does not show
	up" is off: the pass is still flagged, and the host is not mailed.
	"""
	if not vms_settings.feature_on("alert_host_on_no_show"):
		return
	after = frappe.get_doc("Visitor Pass", name)
	if not cint(after.no_show) or cint(before.no_show):
		return
	after._doc_before_save = before

	messages_before_alert = list(frappe.message_log)
	try:
		after.run_notifications("on_change")
	except Exception:
		frappe.log_error(
			title=f"Visitor Pass no-show alert failed for {name}",
			message=frappe.get_traceback(with_context=True),
		)
	finally:
		frappe.local.message_log = messages_before_alert


# ---------------------------------------------------------------------------
# Invitation expiry
# ---------------------------------------------------------------------------
# The statuses an invitation never leaves on its own, the same three
# `visitor_invitation.get_valid_invitation_by_token` refuses outright.
_CLOSED_INVITATION_STATUSES = ("Submitted", "Expired", "Cancelled")


def expire_stale_invitations():
	"""Mark invitations past `invitation_expires_on` as Expired.

	Expiry used to be written only when somebody opened the link
	(`get_valid_invitation_by_token`). An invitation nobody opened again stayed
	"Sent" for ever, so the "Pending Invitations" card and the invitation funnel
	kept counting links that had been dead for weeks. The link check keeps its
	own test - a link must die at its expiry time, not at the next hourly run -
	this job only makes the stored status say so as well. It also clears the
	token and the portal link: left in place, a host extending an expired
	invitation brought the old link back to life.

	The UPDATE repeats the conditions instead of trusting the names read a moment
	earlier: an invitation submitted in between must stay Submitted. Like the link
	check, it does not touch `modified`. Returns the number of invitations expired.
	"""
	now = now_datetime()
	names = frappe.get_all(
		"Visitor Invitation",
		filters={
			"invitation_status": ("not in", _CLOSED_INVITATION_STATUSES),
			"invitation_expires_on": ("<", now),
		},
		pluck="name",
		order_by=None,
	)

	invitation = frappe.qb.DocType("Visitor Invitation")
	for chunk in _chunked(names):
		(
			frappe.qb.update(invitation)
			.set(invitation.invitation_status, "Expired")
			.set(invitation.invitation_token, None)
			.set(invitation.portal_submission_url, None)
			.where(invitation.name.isin(chunk))
			.where(IfNull(invitation.invitation_status, "").notin(_CLOSED_INVITATION_STATUSES))
			.where(invitation.invitation_expires_on < now)
			.run()
		)
		for name in chunk:
			frappe.clear_document_cache("Visitor Invitation", name)

	return len(names)


# ---------------------------------------------------------------------------
# Unapproved passes past their visit
# ---------------------------------------------------------------------------
# Passes handled per run. The job is hourly, so a backlog drains over a few
# runs without one long transaction.
EXPIRE_PENDING_BATCH = 200

EXPIRED_UNAPPROVED_REASON = "Expired — not approved before the visit date."


def expire_unapproved_passes():
	"""Take passes still awaiting approval after their last valid day out of the queue.

	Nothing ever decided them: a pass nobody approved before the visit sat in the
	approver's "Pending ..." lane for good, though the gate would never admit it.
	Each one is moved to Rejected, with a comment on the pass saying why and a
	"Pass Expired" event on its history.

	Written straight to the row (workflow_state and status, as
	`lifecycle._call_off` does for a called-off request) rather than through
	`apply_workflow(..., "Reject")`:
	  - a workflow action re-runs the whole pass validation on a record whose
	    date is now in the past, and an old pass can fail it for reasons that
	    have nothing to do with this (a Visitor Type since switched off, a host
	    who left), which would leave it in the queue anyway;
	  - it would act as Administrator and send the "Pass rejected" mails, telling
	    the host and visitor that someone turned the visit down when nobody did.
	The UPDATE repeats the conditions, so a pass an approver acts on while this
	runs keeps that decision. Meal and room requests made for the pass are
	called off as for any rejected visit. Returns the number of passes expired.
	"""
	from visitormanagement.visitor_management.lifecycle import call_off_pass_arrangements
	from visitormanagement.visitor_management.workflow_builder import REJECTED, pending_lanes

	lanes = sorted(pending_lanes())
	if not lanes:
		return 0

	today_date = getdate(nowdate())
	visitor_pass = frappe.qb.DocType("Visitor Pass")
	names = (
		frappe.qb.from_(visitor_pass)
		.select(visitor_pass.name)
		.where(visitor_pass.docstatus == 0)
		.where(visitor_pass.workflow_state.isin(lanes))
		.where(visitor_pass.visit_date < today_date)
		.where(IfNull(visitor_pass.pass_valid_until, visitor_pass.visit_date) < today_date)
		.orderby(visitor_pass.visit_date)
		.limit(EXPIRE_PENDING_BATCH)
		.run(pluck=True)
	)

	expired = 0
	reason = _(EXPIRED_UNAPPROVED_REASON)
	for name in names:
		(
			frappe.qb.update(visitor_pass)
			.set(visitor_pass.workflow_state, REJECTED)
			.set(visitor_pass.status, "Rejected")
			# Shown to the host on the pass and in the rejection mail.
			.set(visitor_pass.rejection_reason, reason)
			.where(visitor_pass.name == name)
			.where(visitor_pass.docstatus == 0)
			.where(visitor_pass.workflow_state.isin(lanes))
			.run()
		)
		# The row may have been decided since it was selected.
		if frappe.db.get_value("Visitor Pass", name, "workflow_state") != REJECTED:
			continue
		frappe.clear_document_cache("Visitor Pass", name)
		vp = frappe.get_doc("Visitor Pass", name)
		vp.add_comment("Info", reason)
		log_visitor_event(name, "Pass Expired", event_status="Auto-rejected", details={"reason": reason})
		try:
			call_off_pass_arrangements(vp)
		except Exception:
			frappe.log_error(
				title=f"VMS: could not call off the arrangements of expired pass {name}",
				message=frappe.get_traceback(with_context=True),
			)
		expired += 1

	return expired


# How far back the overstay scan looks for "Checked-In" passes. The job never
# auto-checks-out (see the docstring below), so without a bound the scan grows
# forever; anyone still overstaying past `max_hours` was flagged well within
# this window on an earlier hourly run, so bounding it does not change who
# gets flagged.
OVERSTAY_SCAN_WINDOW_DAYS = 30


def flag_overstaying_visitors():
	"""Alert on visitors the building still believes are inside.

	`flag_no_show_passes` catches the visitor who never arrived. Nothing caught
	the opposite and more serious case: the visitor who arrived, never checked
	out, and stays "Checked-In" forever — shown as on the premises for days or
	weeks, so the honest answer to "who is
	in the building right now", the one question a gate exists to answer, was
	wrong by 17.

	`Max Visit Duration (hrs)` in VMS Settings sounded like it governed this but
	does not: it only rejects a booking form whose planned window is too long. It
	is reused here as the overstay threshold, which is what a reader expects it
	to mean.

	This flags and notifies; it deliberately does not auto-check-out. A checkout
	is a statement that somebody watched the visitor leave, and inventing one
	would put a falsehood into the gate record.
	"""
	# Read the same way visitor_pass.py:439 does, rather than adding an accessor
	# to settings.py while another change is in flight there.
	settings = frappe.get_cached_doc("VMS Settings")
	max_hours = cint(getattr(settings, "max_visit_duration_hrs", 0)) or 12
	now = now_datetime()

	overstaying = []
	for row in frappe.get_all(
		"Visitor Pass",
		filters={
			"status": "Checked-In",
			"docstatus": ("<", 2),
			# A pass stuck at Checked-In is never auto-checked-out (see the
			# docstring above), so without a bound this scan only ever grows.
			# Anything that checked in more than OVERSTAY_SCAN_WINDOW_DAYS ago
			# and is still "overstaying" was already over `max_hours` (which is
			# hours, not days) long before it aged out of this window, so it was
			# already flagged on an earlier hourly run — bounding the scan does
			# not change who gets flagged, only how much stale history gets
			# rescanned every run.
			"actual_checkin": (">=", add_to_date(now, days=-OVERSTAY_SCAN_WINDOW_DAYS)),
		},
		fields=[
			"name",
			"visitor_full_name",
			"actual_checkin",
			"expected_checkin",
			"expected_checkout",
			"host_name",
		],
		order_by=None,
	):
		if not row.actual_checkin:
			continue
		hours_in = time_diff_in_hours(now, get_datetime(row.actual_checkin))
		if hours_in <= max_hours:
			continue
		# Not before the stay is planned to end. An overnight visit's check-out
		# time is the next morning, and a visitor who came in early for one is
		# still inside their own booking until then.
		try:
			planned_end = _planned_end(
				_stay_day(row.actual_checkin, row.expected_checkin, row.expected_checkout),
				row.expected_checkin,
				row.expected_checkout,
			)
		except Exception:
			planned_end = None
		if planned_end and now <= planned_end:
			continue
		row.hours_in = int(hours_in)
		overstaying.append(row)

	if not overstaying:
		return 0

	# Only the ones crossing the line for the first time. Without this the job
	# would re-mail the same standing list every hour until somebody checked the
	# visitor out, and an alert that arrives every hour is one nobody reads.
	# Previously this was one `frappe.db.exists()` per overstaying visitor
	# (type=ALL, ~3.1s each on this data) — replaced with one bulk lookup.
	#
	# Once per stay, not once per pass: a multi-day contractor flagged on Monday
	# was never flagged again, however long they stayed on Thursday. An Overstay
	# event counts only if it is from the current stay — logged at or after the
	# pass's `actual_checkin`.
	checked_in_at = {row.name: get_datetime(row.actual_checkin) for row in overstaying}
	already_logged = set()
	names = [row.name for row in overstaying]
	for chunk in _chunked(names):
		for event in frappe.get_all(
			"Visitor Event Log",
			filters={
				"visitor_pass": ("in", chunk),
				"event_type": "Overstay",
				"event_time": (">=", min(checked_in_at[name] for name in chunk)),
			},
			fields=["visitor_pass", "event_time"],
			order_by=None,
		):
			if event.event_time and get_datetime(event.event_time) >= checked_in_at[event.visitor_pass]:
				already_logged.add(event.visitor_pass)

	newly_flagged = [row for row in overstaying if row.name not in already_logged]
	if not newly_flagged:
		return 0

	user = frappe.session.user
	insert_rows = [
		{
			"creation": now,
			"modified": now,
			"modified_by": user,
			"owner": user,
			"docstatus": 0,
			"idx": 0,
			"visitor_pass": row.name,
			"event_type": "Overstay",
			"event_status": "Auto-flagged",
			"event_time": now,
			"source_doctype": None,
			"source_name": None,
			"details": _format_event_details(
				{
					"hours_on_site": row.hours_in,
					"limit_hours": max_hours,
					"checked_in_at": str(row.actual_checkin),
				}
			),
		}
		for row in newly_flagged
	]
	# log_visitor_event() is called here with no source_doctype/source_name, so
	# its own upsert lookup never matches — every call was already a plain
	# insert. No update path to reproduce, unlike the no-show job above.
	_bulk_write_event_logs(insert_rows)

	_notify_overstay(newly_flagged, max_hours)
	return len(newly_flagged)


def _notify_overstay(rows, max_hours):
	"""Mail the security roles a list of everyone over the limit."""
	recipients = _get_recipients(vms_settings.security_alert_roles())
	if not recipients:
		return

	lines = [
		# nosemgrep: string-concat-in-list - one string split over lines on purpose, not a missing comma
		f"<p style='color:#1f2933;'>{len(rows)} visitor(s) have been on site longer than "
		f"the {max_hours}-hour limit and have not been checked out.</p>",
		"<table role='presentation' width='100%' style='border-collapse:collapse;table-layout:fixed;'>",
	]
	for row in rows:
		lines.append(
			"<tr>"
			f"<td style='padding:4px 2px;color:#1f2933;word-break:break-word;'>{esc(row.visitor_full_name or row.name)}</td>"
			f"<td style='padding:4px 2px;color:#5b6b7b;word-break:break-word;'>host {esc(row.host_name, '-')}</td>"
			f"<td style='padding:4px 2px;color:#b42318;'>{esc(row.hours_in)} h on site</td>"
			"</tr>"
		)
	lines.append("</table>")

	try:
		frappe.sendmail(
			recipients=recipients,
			subject=f"{len(rows)} visitor(s) still on site past the {max_hours}h limit",
			message=(
				"<div style='font-family:Arial,sans-serif;font-size:13px;color:#1f2933;"
				"background-color:#ffffff;padding:14px;border-radius:6px;max-width:520px;'>"
				+ "".join(lines)
				+ "</div>"
			),
			now=False,
		)
	except Exception as exc:
		# An alert that cannot be delivered must not stop the flagging that
		# already happened, exactly as the approval mail does.
		frappe.log_error(f"Overstay alert failed: {exc}", "VMS Overstay Alert")


# ---------------------------------------------------------------------------
# Data retention / purge (DPDP / GDPR)
# ---------------------------------------------------------------------------
# The app stores visitor names, mobiles, emails, photos and government ID
# numbers forever unless this job is on. It is OFF by default
# (VMS Settings.data_retention_enabled) and every read below re-checks that
# flag — nothing here ever runs, or deletes anything, on a site that has not
# explicitly opted in. See VMS Settings' "Data Retention & Purge" section.
#
# What gets anonymised: the fields that identify a natural person — name,
# mobile, email, government ID number, vehicle number, and the ID-scan, photo
# and visa-copy Files — on Visitor Pass, on its accompanying-visitor (group member) rows, and
# on the Security Log rows for the same visit.
# What is deliberately kept: the visit record itself (dates, host, gate,
# badge, workflow history, item verification), so "how many contractor
# visits last quarter" and the gate's own audit trail keep working after the
# visitor's own PAN or Aadhaar number is gone. Anonymising the row rather
# than deleting it is what makes that possible.
#
# What this job never touches:
#   - A pass/invitation still in an active state — not yet checked out,
#     still pending approval, still checked in. Only a visit that is over
#     (Checked-Out / Rejected / Cancelled / flagged No-Show, or an invitation
#     that was Submitted / Expired / Cancelled) is ever a candidate, and only
#     once it is also older than the configured retention period.
#   - Visitor Blacklist. A blacklist entry's whole purpose is durable
#     identification — anonymising its name/mobile/ID number would silently
#     disable blacklist matching for that person going forward. That is a
#     security regression dressed up as a privacy fix, so this job does not
#     go near that doctype at all, however old an entry is.

# Visitor Pass states that mean the visit is over and nothing operational
# will read this identity data again. `no_show` is handled separately below:
# a no-show pass never reaches any of these `status` values (flag_no_show_passes
# sets the flag but leaves `status` at Approved/Items Verified), so it needs
# its own branch to ever be recognised as "over".
_TERMINAL_PASS_STATUSES = ("Checked-Out", "Rejected", "Cancelled")

# The ONLY statuses flag_no_show_passes (above, in this same file) ever sets
# no_show=1 on — its own candidate filter is
# `status in ("Approved", "Items Verified")`. The no_show branch below must
# reuse that same restriction rather than trusting the flag alone.
#
# Without it, a Visitor Pass sitting in "Pending Approval" — still actively
# awaiting approval — was purged solely because it carried a stale no_show=1 that
# had no business surviving whatever earlier event (a Reject-then-Reapply,
# most likely) put it back into an active lane. `no_show` is never cleared
# on that path, so treating no_show=1 as sufficient by itself is wrong: it
# reads a flag set under one status as still meaning "the visit is over"
# after the status has since moved back into an active workflow lane. The
# pass's own visitor_full_name/mobile_number/email_id/id_proof_number were
# already anonymised on that site before this was caught; no attachment
# files existed on that particular record, so nothing was lost on disk, but
# the identity fields are gone unless restored from a backup. Restricting
# the no_show branch to the same two statuses flag_no_show_passes itself
# requires closes this for every future run — a stale no_show flag under
# any OTHER status is now read as "we cannot be sure this visit is over",
# which is the conservative reading the brief calls for, not "purge it".
_NO_SHOW_ELIGIBLE_STATUSES = ("Approved", "Items Verified")

_TERMINAL_INVITATION_STATUSES = ("Submitted", "Expired", "Cancelled")

_PURGE_MARKER_TEXT = "[Purged — data retention]"

# Used both to decide "is there still anything to purge here" (idempotency —
# a row already purged has none of these set, so it drops out of the
# candidate query on its own) and, unioned together, as the set of columns
# actually cleared.
_PASS_IDENTITY_OR_FILTERS = [
	["visitor_full_name", "not in", ["", _PURGE_MARKER_TEXT]],
	["mobile_number", "is", "set"],
	["mobile_digits", "is", "set"],
	["email_id", "is", "set"],
	["id_proof_number", "is", "set"],
	["id_proof_masked", "is", "set"],
	["vehicle_number", "is", "set"],
	["company__organisation", "is", "set"],
	["id_proof_scan", "is", "set"],
	["visitor_photo", "is", "set"],
	["custom_visa_copy", "is", "set"],
	["gate_verified_photo", "is", "set"],
]
# The visa copy is a scan of an identity document like the ID scan; it was left
# out, so the purge anonymised a foreign visitor's pass and kept the visa on disk
# for good (R3-F02) — and since FX-005 only Administrator could remove it by hand.
_PASS_IDENTITY_FILE_FIELDS = ("id_proof_scan", "visitor_photo", "custom_visa_copy", "gate_verified_photo")

_SECURITY_LOG_IDENTITY_OR_FILTERS = [
	["visitor_name", "not in", ["", _PURGE_MARKER_TEXT]],
	["visitor_company", "is", "set"],
	["mobile_number", "is", "set"],
	["id_proof_number", "is", "set"],
	["vehicle_number", "is", "set"],
	["id_proof_scan", "is", "set"],
	["visitor_photo", "is", "set"],
	["photo_at_gate", "is", "set"],
]
_SECURITY_LOG_IDENTITY_FILE_FIELDS = ("id_proof_scan", "visitor_photo", "photo_at_gate")

_INVITATION_IDENTITY_OR_FILTERS = [
	["visitor_full_name", "not in", ["", _PURGE_MARKER_TEXT]],
	["visitor_mobile", "is", "set"],
	["visitor_email", "is", "set"],
	["invitation_token", "is", "set"],
]


# ---------------------------------------------------------------------------
# Long clean-ups: on the long queue, in chunks, each one committed
# ---------------------------------------------------------------------------
# The nightly upload clean-up and the retention purge can each have tens of
# thousands of rows to go through. Run as one transaction on the default queue,
# they were stopped at its 300 s limit with nothing kept (measured on a 50k-pass
# site: 1,247 s for the upload clean-up, over 6 minutes for the purge), holding
# their locks all the while. Their scheduler entries now only queue the work on
# the long queue; the work goes in chunks, each committed before the next, and
# each run takes a bounded batch, so a backlog drains over a few nights.


def _commit_chunk():
	"""Commit one chunk of a long clean-up, so a later failure or the job's time limit cannot undo it.

	The commit also runs the file deletions the chunk queued (`_delete_file_rows`).
	Not in tests: VMSTestCase forbids commits and rolls the whole run back.
	"""
	if frappe.in_test:
		return
	# nosemgrep: frappe-manual-commit - one chunk of a long background clean-up, see above
	frappe.db.commit()


# What frappe.delete_doc clears for a deleted document besides its row
# (frappe.model.delete_doc.delete_dynamic_links, the tags, the global search
# entry): (DocType, field holding "File", field holding the File's name).
_FILE_REFERENCES = (
	("Submission Queue", "ref_doctype", "ref_docname"),
	("ToDo", "reference_type", "reference_name"),
	("Email Unsubscribe", "reference_doctype", "reference_name"),
	("DocShare", "share_doctype", "share_name"),
	("Version", "ref_doctype", "docname"),
	("Comment", "reference_doctype", "reference_name"),
	("View Log", "reference_doctype", "reference_name"),
	("Document Follow", "ref_doctype", "ref_docname"),
	("Notification Log", "document_type", "document_name"),
	("Communication Link", "link_doctype", "link_name"),
	("Tag Link", "document_type", "document_name"),
)

_FILE_ROW_FIELDS = (
	"name",
	"file_name",
	"file_url",
	"thumbnail_url",
	"content_hash",
	"is_private",
	"attached_to_doctype",
	"attached_to_name",
	"attached_to_field",
)


def _delete_file_rows(names, note_on_record=False):
	"""Delete File rows now, and their bytes from disk only once that deletion is committed.

	Not `frappe.delete_doc("File", ...)`, for two reasons measured on a 50k-pass site:
	  - it removes the bytes inside the call (File.on_trash), before anything is
	    committed. A rollback after it - an error further on, the job's time limit -
	    put the row back, and the record still pointing at it, with no file behind
	    either. The bytes go after the commit here, by Frappe's own rule
	    (`_delete_unused_bytes`); a rollback drops that step too;
	  - it queues one background job per deleted document (delete_dynamic_links).
	    A purge deleting thousands of files filled the queue until Frappe refused
	    new jobs ("Too many queued background jobs"), and every delete after that
	    failed. The same clean-up is done here once per chunk (_FILE_REFERENCES).
	Folders are never deleted. Returns how many rows were deleted.

	`note_on_record` leaves the "Attachment Removed" note File.on_trash would
	have left on the record each file was attached to.
	"""
	rows = frappe.get_all(
		"File",
		filters={"name": ("in", sorted(set(names))), "is_folder": 0},
		fields=list(_FILE_ROW_FIELDS),
		order_by=None,
	)
	if not rows:
		return 0
	found = [row.name for row in rows]
	frappe.db.delete("File", {"name": ("in", found)})
	for doctype, type_field, name_field in _FILE_REFERENCES:
		frappe.db.delete(doctype, {type_field: "File", name_field: ("in", found)})
	frappe.db.delete("__global_search", {"doctype": "File", "name": ("in", found)})

	if note_on_record:
		now_ts, user = now_datetime(), frappe.session.user
		frappe.db.bulk_insert(
			"Comment",
			[
				"name",
				"creation",
				"modified",
				"owner",
				"modified_by",
				"comment_type",
				"comment_email",
				"reference_doctype",
				"reference_name",
				"content",
			],
			[
				[
					make_autoname("hash", "Comment"),
					now_ts,
					now_ts,
					user,
					user,
					"Attachment Removed",
					user,
					row.attached_to_doctype,
					row.attached_to_name,
					row.file_name,
				]
				for row in rows
				if row.attached_to_doctype and row.attached_to_name
			],
		)
	for row in rows:
		frappe.clear_document_cache("File", row.name)
		frappe.db.after_commit.add(partial(_delete_unused_bytes, frappe.get_doc({"doctype": "File", **row})))
	return len(found)


def _delete_unused_bytes(file_doc):
	"""After the commit: remove a deleted File row's bytes, unless another row still holds them.

	One file often has several rows: a returning visitor's new pass reuses the old
	pass's photo with a row of its own, and each gate log has its copies. A file
	another row still names (by URL, or by content as Frappe de-duplicates
	uploads) stays on disk and goes with that row (File._delete_file_on_disk).
	A failure is logged and the rest of the batch goes on: the row is already gone.
	"""
	try:
		if file_doc.file_url and frappe.db.exists("File", {"file_url": file_doc.file_url}):
			return
		file_doc._delete_file_on_disk()
	except Exception:
		frappe.log_error(
			title=f"VMS: could not remove the file of deleted File {file_doc.name}",
			message=frappe.get_traceback(with_context=True),
		)


def _delete_record_files(doctype, urls_by_name):
	"""Delete the File rows these records' purged fields point at, their bytes after the commit.

	`urls_by_name` is {record name: [file URLs]} for one chunk of `doctype`.
	Clearing the field is not clearing the document: the image stays in the files
	directory until the File row itself is deleted.

	Only the rows attached to THESE records. One file often has several rows: a
	returning visitor's new pass reuses the old pass's photo with a row of its own
	(uploads.share_files_from_source_pass), and each gate log has its copies. The
	row used to be picked by URL alone, so purging the old pass could delete the
	live pass's row instead and leave the old one: the live pass still showed the
	photo, but its host got "not permitted" on it (R3-F01). The bytes go only with
	the last row that holds them (`_delete_unused_bytes`), so a photo another
	record still uses stays on disk, and goes with that record's own purge. A
	stray upload row that no record owns is the nightly purge_abandoned_uploads'
	to remove once the field no longer holds its URL.

	Deleted by this job directly (`_delete_file_rows`), past the permission
	checks: this is the one place a finished record's documents can be deleted.
	For everyone else `uploads.has_file_permission` refuses to delete the
	documents of a pass that has left Draft or of a recorded gate log.
	"""
	wanted = {(name, url) for name, urls in urls_by_name.items() for url in urls if url}
	if not wanted:
		return
	names = []
	for row in frappe.get_all(
		"File",
		filters={
			"attached_to_doctype": doctype,
			"attached_to_name": ("in", sorted({name for name, _url in wanted})),
			"file_url": ("in", sorted({url for _name, url in wanted})),
		},
		fields=["name", "attached_to_name", "file_url"],
		order_by=None,
	):
		if (row.attached_to_name, row.file_url) in wanted:
			names.append(row.name)
	_delete_file_rows(names, note_on_record=True)


# Doctypes whose forms take uploads; a file attached to one of their unsaved
# ("new-...") names belongs to a form that was abandoned.
_UPLOAD_DOCTYPES = ("Visitor Pass", "Security Log", "Visitor Invitation", "Visitor Blacklist")


# The note left on a File the visitor portal accepted (`note_portal_upload`). It
# is what tells the portal's own uploads apart, a day later, from any other
# guest's file: nothing else about a File row does. Not translated - it is
# matched as stored.
PORTAL_UPLOAD_NOTE = "Uploaded from the visitor pre-registration form."


def note_portal_upload(doc, method=None):
	"""File after_insert: put the portal's note on an upload the portal accepted.

	`portal_upload.guard_guest_upload` has already admitted the file by the time
	this runs; the test here is the one it applies to recognise its own form. The
	note is an Info comment on the File: it needs no field on a DocType this app
	does not own, it is visible in the File's timeline, and Frappe deletes it with
	the File.

	A note that cannot be written is logged and the upload goes through: the
	visitor at the form matters more than the clean-up, and a file without the
	note is simply never swept.
	"""
	if frappe.session.user != "Guest" or not frappe.request or doc.is_folder:
		return
	if doc.attached_to_doctype or doc.attached_to_name:
		return

	from visitormanagement.visitor_management.portal_upload import _is_portal_request

	if not _is_portal_request():
		return
	try:
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Info",
				"reference_doctype": "File",
				"reference_name": doc.name,
				"content": PORTAL_UPLOAD_NOTE,
			}
		).insert(ignore_permissions=True)
	except Exception:
		frappe.log_error(
			title=f"Could not note portal upload {doc.name}",
			message=frappe.get_traceback(with_context=True),
		)


def _portal_uploads_before(cutoff):
	"""Names of the Files carrying the portal's note, uploaded before `cutoff`."""
	return frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": "File",
			"comment_type": "Info",
			"content": PORTAL_UPLOAD_NOTE,
			"creation": ("<", cutoff),
		},
		pluck="reference_name",
		distinct=True,
		order_by=None,
	)


# Files the upload clean-up looks at per run, oldest first, and per commit.
# Under 1 ms a file at 250k File rows: a full batch is well under a minute,
# far inside UPLOAD_PURGE_TIMEOUT. What is left over goes the next night.
UPLOAD_PURGE_BATCH = 20000
UPLOAD_PURGE_CHUNK = 200
UPLOAD_PURGE_TIMEOUT = 30 * 60


def purge_abandoned_uploads():
	"""Scheduler entry (03:30): queue the upload clean-up on the long queue.

	The work itself is `run_purge_abandoned_uploads`. Queued once: a run still
	waiting or going is not queued again (job_id). In tests it runs at once.
	"""
	return frappe.enqueue(
		"visitormanagement.visitor_management.tasks.run_purge_abandoned_uploads",
		queue="long",
		timeout=UPLOAD_PURGE_TIMEOUT,
		job_id="vms_purge_abandoned_uploads",
		deduplicate=True,
		now=frappe.in_test,
	)


def run_purge_abandoned_uploads(batch=UPLOAD_PURGE_BATCH):
	"""Put strays back on their record, and delete uploads nothing ever used.

	Two kinds of File row are looked at once they are a day old:
	  - desk uploads still on an unsaved form's "new-..." name. Frappe relinks
	    those only within 60 minutes of the upload; a pass finished later still
	    uses the file. Such a row is moved onto that record (or dropped if the
	    record already has its own row for it). Only a row no saved record uses
	    is deleted — the form really was abandoned;
	  - portal uploads a visitor never submitted (the portal adopts an unattached
	    guest file for 30 minutes only), unless a saved record uses the URL. Only
	    files carrying the portal's own note (`note_portal_upload`) are candidates.
	    Every Guest-owned unattached File used to be, which also deleted what a
	    guest uploaded on another app's page - one the administrator had allowed
	    in VMS Settings, or any page of a site that accepted guest uploads before
	    this app. Those files are not this app's to judge.
	They are mostly ID scans and face photos, so the abandoned ones should not
	linger. Whether a saved record uses a file is read from the File rows
	themselves (uploads.records_with_own_row, uploads.records_holding): reading
	every record's Attach columns for each file scanned Visitor Pass and Security
	Log in full, six times per file. A deleted row's bytes are removed after the
	chunk is committed, and only when no other File row holds them
	(`_delete_unused_bytes`). See visitor_management/uploads.py.

	At most `batch` files per run, oldest first, committed every
	UPLOAD_PURGE_CHUNK. Returns {"relinked": n, "deleted": n}.
	"""
	cutoff = add_to_date(now_datetime(), days=-1)
	relinked, deleted = 0, 0

	strays = frappe.get_all(
		"File",
		filters={
			"attached_to_doctype": ("in", _UPLOAD_DOCTYPES),
			"attached_to_name": ("like", "new-%"),
			"creation": ("<", cutoff),
		},
		fields=["name", "file_url", "attached_to_doctype", "owner", "creation"],
		order_by="creation asc",
		limit=batch,
	)
	for chunk in _chunked(strays, UPLOAD_PURGE_CHUNK):
		moved, dropped = _settle_strays(chunk)
		relinked += moved
		deleted += dropped
		_commit_chunk()

	room = batch - len(strays)
	portal_uploads = []
	for names in _chunked(_portal_uploads_before(cutoff)):
		if len(portal_uploads) >= room:
			break
		portal_uploads += frappe.get_all(
			"File",
			filters={
				"name": ("in", names),
				"owner": "Guest",
				"attached_to_doctype": ("is", "not set"),
				"is_folder": 0,
				"creation": ("<", cutoff),
			},
			fields=["name", "file_url", "creation"],
			order_by=None,
		)
	portal_uploads = sorted(portal_uploads, key=lambda f: f.creation)[: max(room, 0)]
	for chunk in _chunked(portal_uploads, UPLOAD_PURGE_CHUNK):
		deleted += _settle_portal_uploads(chunk)
		_commit_chunk()

	return {"relinked": relinked, "deleted": deleted}


def _settle_strays(files):
	"""Relink or delete one chunk of desk strays; returns (relinked, deleted)."""
	from visitormanagement.visitor_management.uploads import records_holding, records_with_own_row

	relinked, dropped = 0, []
	by_doctype = {}
	for f in files:
		by_doctype.setdefault(f.attached_to_doctype, []).append(f)

	for doctype, rows in by_doctype.items():
		urls = {f.file_url for f in rows if f.file_url}
		# A saved record with its own row for the URL: the stray is a duplicate,
		# and the record's row keeps the bytes.
		own_row = records_with_own_row((doctype,), urls)
		# A saved record holding the URL without a row of its own: the stray is moved
		# onto it. Only records saved after the upload can hold it.
		holding = records_holding(doctype, urls - set(own_row), min(f.creation for f in rows))
		for f in rows:
			if f.file_url in own_row or f.file_url not in holding:
				dropped.append(f.name)
				continue
			record, fieldname, record_owner = holding[f.file_url]
			if record_owner != f.owner:
				# Someone else's record holds this URL: a stray is its creator's own upload,
				# so this one was pasted in. Moving it would hand the file to that record's
				# readers; it is dropped like any abandoned upload.
				dropped.append(f.name)
				continue
			frappe.db.set_value(
				"File",
				f.name,
				{"attached_to_name": record, "attached_to_field": fieldname},
				update_modified=False,
			)
			relinked += 1
	return relinked, _delete_file_rows(dropped)


def _settle_portal_uploads(files):
	"""Delete the portal uploads of one chunk that no saved record uses; returns how many."""
	from visitormanagement.visitor_management.uploads import records_holding, records_with_own_row

	urls = {f.file_url for f in files if f.file_url}
	# The portal once stored a guest-supplied URL without attaching its File.
	in_use = set(records_with_own_row(_UPLOAD_DOCTYPES, urls))
	since = min(f.creation for f in files)
	for doctype in _UPLOAD_DOCTYPES:
		in_use.update(records_holding(doctype, urls - in_use, since))
	return _delete_file_rows([f.name for f in files if f.file_url not in in_use])


def _retention_cutoff():
	"""Return the settings snapshot and the date on/before which a visit counts
	as old enough to purge, or (None, None) if retention is off or misconfigured."""
	settings = frappe.get_cached_doc("VMS Settings")
	if not cint(settings.get("data_retention_enabled")):
		return None, None

	retention_days = cint(settings.get("data_retention_days"))
	if retention_days < 1:
		# A blank/zero period must never be read as "purge everything
		# immediately" — VMSSettings._validate_policy_numbers already blocks
		# entering 0 through the form, but a value written by some other means
		# (an import, a direct db_set) gets the same floor enforced here.
		frappe.log_error(
			"VMS Settings: Enable Data Retention Purge is on but Retention Period "
			"(days) is not a positive number. No records were purged this run.",
			"VMS Data Retention",
		)
		return None, None

	return retention_days, add_to_date(getdate(nowdate()), days=-retention_days)


# Records the retention purge anonymises per run, and passes it handles per
# commit. Under 1 ms a record at 50k passes (20k records in 13 s, files
# included): a full batch is about a minute, far inside RETENTION_PURGE_TIMEOUT.
# A site turning retention on with years of history behind it drains that
# backlog over a few nights instead of in one job that never ends.
RETENTION_PURGE_BATCH = 100000
RETENTION_PURGE_CHUNK = 200
RETENTION_PURGE_TIMEOUT = 60 * 60
# After a run that got through everything, the next runs look only at visits
# whose last valid day is at most this many days before that run's cutoff, plus
# every pass changed since it started. The whole history is gone through again
# once this many days have passed since the last time it was.
RETENTION_OVERLAP_DAYS = 7
RETENTION_FULL_SWEEP_DAYS = 7
# Where the last run that got through everything is remembered: its cutoff, when
# it started, and when the whole history was last gone through.
_RETENTION_STATE_KEY = "vms_retention_purge_state"


def purge_expired_visitor_data():
	"""Scheduler entry (03:00): queue the retention purge on the long queue.

	Nothing is queued while VMS Settings -> Enable Data Retention Purge is off;
	the work itself (`run_purge_expired_visitor_data`) checks the settings again.
	Queued once: a run still waiting or going is not queued again (job_id). In
	tests it runs at once and returns the number of records anonymised.
	"""
	if not cint(frappe.get_cached_doc("VMS Settings").get("data_retention_enabled")):
		return 0
	return frappe.enqueue(
		"visitormanagement.visitor_management.tasks.run_purge_expired_visitor_data",
		queue="long",
		timeout=RETENTION_PURGE_TIMEOUT,
		job_id="vms_purge_expired_visitor_data",
		deduplicate=True,
		now=frappe.in_test,
	)


def run_purge_expired_visitor_data(batch=RETENTION_PURGE_BATCH):
	"""Anonymise identity data on visits old enough that the retention policy
	no longer needs it. See the module docstring above for what is and is not
	touched.

	Driven by the passes whose visit is over and old enough, re-derived from the
	database every run (`_eligible_pass_names`). For each chunk of them the pass,
	its accompanying visitors, its gate logs and their member rows are each
	anonymised where they still hold identity data, each checked on its own. A
	Security Log row is purged because its own Visitor Pass is currently over and
	old enough, not because that pass was purged in this same run, so a partial
	failure, an odd ordering, or a gate log created after its pass was purged is
	picked up by a later run. Invitations go through on their own.

	Each chunk is committed before the next; the files of a chunk's records are
	removed from disk only after that commit (`_delete_record_files`). A run stops
	at the end of the chunk in which it reached `batch` records; what is left is
	picked up the next night.
	Idempotent: a row already purged has no identity field left to match.

	Not all of history every night: once a run has got through everything, the
	next ones look at the recent visits and the changed passes only
	(`_retention_window`), with the whole history gone through again weekly.
	"""
	retention_days, cutoff = _retention_cutoff()
	if not cutoff:
		return 0

	started = now_datetime()
	window = _retention_window(_retention_state(), cutoff, started)
	counts = {"passes": 0, "members": 0, "logs": 0, "invitations": 0}

	def budget_left():
		return sum(counts.values()) < batch

	finished = True
	for chunk in _chunked(_eligible_pass_names(cutoff, window), RETENTION_PURGE_CHUNK):
		if not budget_left():
			finished = False
			break
		counts["passes"] += _purge_passes(chunk, cutoff, retention_days)
		counts["members"] += _purge_group_members(chunk) + _purge_log_members(chunk)
		counts["logs"] += _purge_security_logs(chunk)
		_commit_chunk()

	for chunk in _chunked(_eligible_invitation_names(cutoff, window), RETENTION_PURGE_CHUNK):
		if not finished or not budget_left():
			finished = False
			break
		counts["invitations"] += _purge_invitations(chunk)
		_commit_chunk()

	if finished:
		_save_retention_state(cutoff, started, full_sweep=window is None)
		_commit_chunk()

	total = sum(counts.values())
	if total:
		print(
			f"  data retention: anonymised {counts['passes']} visitor pass(es), "
			f"{counts['members']} accompanying visitor(s), "
			f"{counts['logs']} security log(s), {counts['invitations']} invitation(s) "
			f"— visits over {retention_days}+ days ago (cutoff {cutoff})"
			+ ("" if finished else "; the rest goes on the next run")
		)
	return total


def _retention_state():
	"""What the last run that got through everything left (`_save_retention_state`), or None.

	Read from the table rather than Frappe's defaults cache, which a rollback
	does not put back.
	"""
	value = frappe.db.get_value(
		"DefaultValue", {"parent": "__default", "defkey": _RETENTION_STATE_KEY}, "defvalue"
	)
	try:
		state = frappe.parse_json(value) if value else None
	except ValueError:
		return None
	return frappe._dict(state) if isinstance(state, dict) else None


def _save_retention_state(cutoff, started, full_sweep):
	state = _retention_state() or frappe._dict()
	frappe.db.set_default(
		_RETENTION_STATE_KEY,
		frappe.as_json(
			{
				"cutoff": str(cutoff),
				"started": str(started),
				"full_sweep": str(started) if full_sweep else state.get("full_sweep"),
			},
			indent=None,
		),
	)


def _retention_window(state, cutoff, now):
	"""(since_day, changed_since) bounding tonight's run, or None to go through all history.

	A visit not purged by the last run that got through everything either became
	old enough since — its last valid day is after that run's cutoff — or was not
	over yet then, and became over since. A pass becomes over through a save or a
	db.set_value, which moves `modified` (a check-out, a rejection, a cancel); the
	no-show and expiry jobs write without it, but within hours of the visit's last
	day, long before the cutoff reaches it. RETENTION_OVERLAP_DAYS covers a
	scheduler that was down for a few days, and the weekly full run (no state, or
	RETENTION_FULL_SWEEP_DAYS since the last) whatever is left: a gate log added
	to a pass already purged, a pass changed by hand.
	"""
	if not state or not state.get("cutoff") or not state.get("full_sweep"):
		return None
	if get_datetime(state.full_sweep) <= add_to_date(now, days=-RETENTION_FULL_SWEEP_DAYS):
		return None
	since_day = add_days(min(getdate(state.cutoff), getdate(cutoff)), -RETENTION_OVERLAP_DAYS)
	return since_day, get_datetime(state.started)


def _eligible_pass_names(cutoff, window=None):
	"""Names of the passes whose visit is over and whose last valid day is on or before `cutoff`.

	`window` (see `_retention_window`) limits them to the passes that may have
	become eligible since the last complete run, each part read through its own
	index (visit_date, pass_valid_until, modified) rather than scanning history.
	"""
	visitor_pass = frappe.qb.DocType("Visitor Pass")

	def names(condition=None):
		query = (
			frappe.qb.from_(visitor_pass)
			.select(visitor_pass.name)
			# Narrows the scan to the visit_date index; the last valid day decides.
			.where(visitor_pass.visit_date <= cutoff)
			.where(_last_valid_day(visitor_pass) <= cutoff)
			.where(_visit_is_over(visitor_pass))
		)
		if condition is not None:
			query = query.where(condition)
		return set(query.run(pluck=True))

	if window is None:
		return sorted(names())
	since_day, changed_since = window
	return sorted(
		names(visitor_pass.visit_date > since_day)
		| names(visitor_pass.pass_valid_until > since_day)
		| names(visitor_pass.modified >= changed_since)
	)


def _purge_passes(names, cutoff, retention_days):
	"""Anonymise the passes among `names` that still hold identity data; returns how many."""
	rows = frappe.get_all(
		"Visitor Pass",
		filters={"name": ("in", names)},
		or_filters=_PASS_IDENTITY_OR_FILTERS,
		fields=["name", *_PASS_IDENTITY_FILE_FIELDS],
		order_by=None,
	)
	if not rows:
		return 0
	purged = sorted(row.name for row in rows)
	_delete_record_files(
		"Visitor Pass",
		{row.name: [row.get(fieldname) for fieldname in _PASS_IDENTITY_FILE_FIELDS] for row in rows},
	)

	visitor_pass = frappe.qb.DocType("Visitor Pass")
	(
		frappe.qb.update(visitor_pass)
		.set(visitor_pass.visitor_full_name, _PURGE_MARKER_TEXT)
		.set(visitor_pass.mobile_number, None)
		.set(visitor_pass.mobile_digits, None)
		.set(visitor_pass.email_id, None)
		.set(visitor_pass.id_proof_number, None)
		.set(visitor_pass.id_proof_key, None)
		.set(visitor_pass.id_proof_masked, None)
		.set(visitor_pass.vehicle_number, None)
		.set(visitor_pass.company__organisation, None)
		.set(visitor_pass.id_proof_scan, None)
		.set(visitor_pass.visitor_photo, None)
		.set(visitor_pass.custom_visa_copy, None)
		.set(visitor_pass.gate_verified_photo, None)
		.where(visitor_pass.name.isin(purged))
		.run()
	)
	for name in purged:
		frappe.clear_document_cache("Visitor Pass", name)

	user = frappe.session.user
	now_ts = now_datetime()
	details = _format_event_details({"retention_days": retention_days, "cutoff_date": str(cutoff)})
	_bulk_write_event_logs(
		[
			{
				"creation": now_ts,
				"modified": now_ts,
				"modified_by": user,
				"owner": user,
				"docstatus": 0,
				"idx": 0,
				"visitor_pass": name,
				"event_type": "Data Retention Purge",
				"event_status": "Auto-purged",
				"event_time": now_ts,
				"source_doctype": "VMS Settings",
				"source_name": "VMS Settings",
				"details": details,
			}
			for name in purged
		]
	)
	return len(purged)


def _purge_group_members(pass_names):
	"""Anonymise the accompanying visitors on the passes `pass_names`; returns how many rows.

	A group pass lists the people who came with the visitor in its
	`group_members` rows, each with a name, a mobile number and an ID number. The
	pass purge only cleared the columns of `tabVisitor Pass`, so those rows kept
	every accompanying visitor's identity on a pass that claimed to be purged.

	Checked on its own, not as part of the pass's own purge: a pass purged by an
	earlier run has no identity field left, and its rows must still be cleared.
	A row already purged has none of these fields set and drops out.
	"""
	member = frappe.qb.DocType("Visitor Group Member")
	rows = (
		frappe.qb.from_(member)
		.select(member.name, member.parent)
		.where(member.parenttype == "Visitor Pass")
		.where(member.parent.isin(pass_names))
		.where(
			IfNull(member.visitor_name, "").notin(("", _PURGE_MARKER_TEXT))
			| (IfNull(member.mobile_number, "") != "")
			| (IfNull(member.id_proof_number, "") != "")
			| (IfNull(member.id_proof_masked, "") != "")
		)
		.run(as_dict=True)
	)
	if not rows:
		return 0
	(
		frappe.qb.update(member)
		.set(member.visitor_name, _PURGE_MARKER_TEXT)
		.set(member.mobile_number, None)
		.set(member.id_proof_number, None)
		.set(member.id_proof_key, None)
		.set(member.id_proof_masked, None)
		.where(member.name.isin([row.name for row in rows]))
		.run()
	)
	for parent in {row.parent for row in rows}:
		frappe.clear_document_cache("Visitor Pass", parent)
	return len(rows)


def _purge_log_members(pass_names):
	"""Anonymise the accompanying visitors' names on the gate logs of the passes `pass_names`.

	A group pass's check-in and check-out logs list its members by name (Security
	Log Member), the same people `_purge_group_members` anonymises on the pass.
	"""
	member = frappe.qb.DocType("Security Log Member")
	log = frappe.qb.DocType("Security Log")
	names = (
		frappe.qb.from_(member)
		.inner_join(log)
		.on(log.name == member.parent)
		.select(member.name)
		.where(member.parenttype == "Security Log")
		.where(log.visitor_pass.isin(pass_names))
		.where(IfNull(member.visitor_name, "").notin(("", _PURGE_MARKER_TEXT)))
		.run(pluck=True)
	)
	if names:
		frappe.qb.update(member).set(member.visitor_name, _PURGE_MARKER_TEXT).where(
			member.name.isin(names)
		).run()
	return len(names)


def _purge_security_logs(pass_names):
	"""Anonymise the gate logs of the passes `pass_names` that still hold identity data.

	"Over" is the same test the pass purge applies (`_eligible_pass_names`). It used
	to accept `no_show = 1` under ANY status, so the gate logs of a pass that
	carried a stale no-show flag but was back in an active lane - or had since been
	checked in - lost the visitor's name and ID while the pass itself was, rightly, kept.
	"""
	log = frappe.qb.DocType("Security Log")
	rows = (
		frappe.qb.from_(log)
		.select(log.name, *(log[fieldname] for fieldname in _SECURITY_LOG_IDENTITY_FILE_FIELDS))
		.where(log.visitor_pass.isin(pass_names))
		.where(
			IfNull(log.visitor_name, "").notin(("", _PURGE_MARKER_TEXT))
			| (IfNull(log.visitor_company, "") != "")
			| (IfNull(log.mobile_number, "") != "")
			| (IfNull(log.id_proof_number, "") != "")
			| (IfNull(log.vehicle_number, "") != "")
			| (IfNull(log.id_proof_scan, "") != "")
			| (IfNull(log.visitor_photo, "") != "")
			| (IfNull(log.photo_at_gate, "") != "")
		)
		.run(as_dict=True)
	)
	if not rows:
		return 0
	names = sorted(row.name for row in rows)
	_delete_record_files(
		"Security Log",
		{row.name: [row.get(fieldname) for fieldname in _SECURITY_LOG_IDENTITY_FILE_FIELDS] for row in rows},
	)
	(
		frappe.qb.update(log)
		.set(log.visitor_name, _PURGE_MARKER_TEXT)
		.set(log.visitor_company, None)
		.set(log.mobile_number, None)
		.set(log.id_proof_number, None)
		.set(log.vehicle_number, None)
		.set(log.id_proof_scan, None)
		.set(log.visitor_photo, None)
		.set(log.photo_at_gate, None)
		.where(log.name.isin(names))
		.run()
	)
	for name in names:
		frappe.clear_document_cache("Security Log", name)
	return len(names)


def _eligible_invitation_names(cutoff, window=None):
	"""Invitations that reached a terminal status and whose visit (or, absent one,
	their own creation) is on or before `cutoff`. `window` as for the passes."""
	invitation = frappe.qb.DocType("Visitor Invitation")
	last_day = cutoff
	day_after = add_days(cutoff, 1)

	def names(condition=None):
		query = (
			frappe.qb.from_(invitation)
			.select(invitation.name)
			.where(invitation.invitation_status.isin(_TERMINAL_INVITATION_STATUSES))
			.where(
				(invitation.visit_date <= last_day)
				| (invitation.visit_date.isnull() & (invitation.creation < day_after))
			)
		)
		if condition is not None:
			query = query.where(condition)
		return set(query.run(pluck=True))

	if window is None:
		return sorted(names())
	since_day, changed_since = window
	return sorted(
		names(invitation.visit_date > since_day)
		| names(invitation.visit_date.isnull() & (invitation.creation >= add_days(since_day, 1)))
		| names(invitation.modified >= changed_since)
	)


def _purge_invitations(names):
	"""Anonymise the invitations among `names` that still hold identity data.

	`invitation_token` is cleared here too — it is a bearer secret with no
	further purpose once the invitation is Submitted/Expired/Cancelled
	(`get_valid_invitation_by_token` already refuses all three statuses), so
	there is nothing to lose and one fewer stale secret sitting in the table.
	"""
	purged = frappe.get_all(
		"Visitor Invitation",
		filters={"name": ("in", names)},
		or_filters=_INVITATION_IDENTITY_OR_FILTERS,
		pluck="name",
		order_by=None,
	)
	if not purged:
		return 0
	invitation = frappe.qb.DocType("Visitor Invitation")
	(
		frappe.qb.update(invitation)
		.set(invitation.visitor_full_name, _PURGE_MARKER_TEXT)
		.set(invitation.visitor_mobile, None)
		.set(invitation.visitor_email, None)
		.set(invitation.invitation_token, None)
		.where(invitation.name.isin(purged))
		.run()
	)
	for name in purged:
		frappe.clear_document_cache("Visitor Invitation", name)
	return len(purged)


def _visit_is_over(visitor_pass):
	"""Query condition: the row of the Visitor Pass table `visitor_pass` is a visit that is over.

	The one definition of "over" the purge uses for the pass and, through it, for
	its accompanying visitors and gate logs (`_eligible_pass_names`): a terminal
	status, or a no-show still
	in one of the two statuses the no-show job flags. See
	_NO_SHOW_ELIGIBLE_STATUSES for why the flag alone is not enough.
	"""
	return visitor_pass.status.isin(_TERMINAL_PASS_STATUSES) | (
		(visitor_pass.no_show == 1) & visitor_pass.status.isin(_NO_SHOW_ELIGIBLE_STATUSES)
	)


def _last_valid_day(visitor_pass):
	"""Query expression: the last day the pass in table `visitor_pass` is good for.

	A multi-day pass runs to `pass_valid_until`. The purge compared `visit_date`
	alone with the cutoff, so a long contractor pass that started before the
	retention period lost its visitor's name and ID while the visitor was still
	coming through the gate on it.
	"""
	return Function(
		"GREATEST", visitor_pass.visit_date, IfNull(visitor_pass.pass_valid_until, visitor_pass.visit_date)
	)
