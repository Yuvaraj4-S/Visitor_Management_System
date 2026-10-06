import frappe
from frappe.utils import (
	add_to_date,
	cint,
	get_datetime,
	getdate,
	now_datetime,
	nowdate,
	time_diff_in_hours,
)

from visitormanagement.visitor_management import settings as vms_settings
from visitormanagement.visitor_management.lifecycle import _format_event_details
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
			"name",
			"visitor_pass",
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
			html.append(f"<li>{render_row(frappe._dict({k: esc(v, None) for k, v in r.items()}))}</li>")
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
	]
	return (
		"<div style='font-family:Arial,sans-serif;font-size:13px;color:#1f2933'>" + "".join(parts) + "</div>"
	)


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


def _reserve_block_names(doctype, count):
	"""Reserve `count` sequential autonames for `doctype` in ONE round trip.

	Mirrors frappe.model.naming.parse_naming_series / getseries for the parts
	an autoname like "VEL-.YYYY.-.#####" actually uses (literal text, YY/MM/
	DD/YYYY, and the trailing "#####" counter), but reserves the whole block
	against `tabSeries` at once instead of calling getseries() once per
	document — that per-row call is exactly the kind of round trip this
	batching is meant to avoid.

	Falls back to the ORM's own per-document naming for any doctype whose
	autoname is not this simple counter-series shape (not expected here —
	Visitor Event Log's autoname is "VEL-.YYYY.-.#####" — but kept so a future
	change to the doctype fails safe instead of naming things wrong).
	"""
	autoname = frappe.get_meta(doctype).autoname or ""
	parts = autoname.split(".")
	prefix = ""
	digits = None
	today = now_datetime()
	for part in parts:
		if not part:
			continue
		if part.startswith("#"):
			digits = len(part)
			break
		elif part == "YY":
			prefix += today.strftime("%y")
		elif part == "MM":
			prefix += today.strftime("%m")
		elif part == "DD":
			prefix += today.strftime("%d")
		elif part == "YYYY":
			prefix += today.strftime("%Y")
		else:
			prefix += part

	if digits is None:
		from frappe.model.naming import make_autoname

		return [make_autoname(autoname) for _ in range(count)]

	row = frappe.db.sql("select `current` from `tabSeries` where name=%s for update", (prefix,))
	if row and row[0][0] is not None:
		start = cint(row[0][0])
		frappe.db.sql("update `tabSeries` set `current` = `current` + %s where name=%s", (count, prefix))
	else:
		start = 0
		frappe.db.sql("insert into `tabSeries` (`name`, `current`) values (%s, %s)", (prefix, count))

	return [f"{prefix}{str(start + i + 1).zfill(digits)}" for i in range(count)]


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


def _bulk_write_event_logs(rows):
	"""Bulk-insert Visitor Event Log rows built as dicts with `_VEL_COLUMNS` keys.

	Equivalent to calling `log_visitor_event(...)` once per row when no
	matching log already exists (the common case for both jobs below), but as
	one `INSERT ... VALUES (...), (...), ...` per chunk instead of one
	`doc.insert()` per row.
	"""
	if not rows:
		return
	names = _reserve_block_names("Visitor Event Log", len(rows))
	values = [
		[name] + [row.get(col) for col in _VEL_COLUMNS if col != "name"]
		for name, row in zip(names, rows, strict=True)
	]
	frappe.db.bulk_insert("Visitor Event Log", _VEL_COLUMNS, values, chunk_size=_CHUNK_SIZE)


def flag_no_show_passes():
	"""Set no_show=1 on Visitor Passes that missed their visit window.

	A pass is a no-show when:
	  - status in (Approved, Items Verified)  -- never made it past the gate
	  - visit_date + expected_checkout + grace is in the past
	  - no_show is currently 0
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
		fields=["name", "visit_date", "expected_checkout"],
		# Order doesn't matter here — every candidate is evaluated regardless
		# of order — and the implicit "modified desc" default forces a
		# filesort alongside these unindexed filters. Skip it.
		order_by=None,
	)

	to_flag = []  # [(name, deadline)]
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
	for chunk in _chunked(names):
		placeholders = ", ".join(["%s"] * len(chunk))
		# nosemgrep: frappe-sql-format-injection - IN (...) placeholders only, values are parameters
		frappe.db.sql(
			f"""update `tabVisitor Pass`
			set no_show = 1, current_location = 'No Show'
			where name in ({placeholders})
			  and no_show = 0
			  and docstatus < 2
			  and status in ('Approved', 'Items Verified')""",
			tuple(chunk),
		)
		for name in chunk:
			frappe.clear_document_cache("Visitor Pass", name)


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
		fields=["name", "visitor_full_name", "actual_checkin", "expected_checkout", "host_name"],
		order_by=None,
	):
		if not row.actual_checkin:
			continue
		hours_in = time_diff_in_hours(now, get_datetime(row.actual_checkin))
		if hours_in > max_hours:
			row.hours_in = int(hours_in)
			overstaying.append(row)

	if not overstaying:
		return 0

	# Only the ones crossing the line for the first time. Without this the job
	# would re-mail the same standing list every hour until somebody checked the
	# visitor out, and an alert that arrives every hour is one nobody reads.
	# Previously this was one `frappe.db.exists()` per overstaying visitor
	# (type=ALL, ~3.1s each on this data) — replaced with one bulk lookup.
	already_logged = set()
	names = [row.name for row in overstaying]
	for chunk in _chunked(names):
		already_logged.update(
			frappe.get_all(
				"Visitor Event Log",
				filters={"visitor_pass": ("in", chunk), "event_type": "Overstay"},
				pluck="visitor_pass",
			)
		)

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
		# Keyword arguments: on Frappe 15 the first positional argument is the
		# 140-character Error Log title, so a long message passed first raised
		# CharacterLengthExceededError from inside this handler.
		frappe.log_error(title="VMS Overstay Alert", message=f"Overstay alert failed: {exc}")


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
# mobile, email, government ID number, vehicle number, and the ID-scan, visa,
# photo and QR Files — on Visitor Pass, its group member rows, and the Security
# Log, Hospitality Request, Conference Room Booking and Contact Trace Record
# rows of the same visit; plus what the record's timeline and alert mails
# repeat of them (`_purge_trail`). The exact columns are the `_..._PURGE_VALUES`
# maps below. Free text a member of staff typed (purpose of visit, meeting
# minutes, their own comments) is not parsed or cleared.
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

# What the purge clears, per DocType: {column: what it becomes}. None empties
# the column; a string replaces it (the purge marker where a blank would read
# as missing data, or the field's own "nothing" option for a Select). A column
# listed here that a site does not have (yet) is skipped, so the lists can name
# fields other parts of the app add.
#
# The same map decides whether a row still has anything to purge, which is what
# makes every pass idempotent and self-healing: a row already purged matches
# nothing and drops out, and a row purged by an earlier version that cleared
# less is picked up again for what that version left behind.
_PASS_PURGE_VALUES = {
	"visitor_full_name": _PURGE_MARKER_TEXT,
	"mobile_number": None,
	"mobile_digits": None,
	"email_id": None,
	# The stored ID number, what is shown of it, and the box it is typed into.
	"id_proof_number": None,
	"id_proof_number_masked": None,
	"id_proof_number_entry": None,
	"vehicle_number": None,
	"company__organisation": None,
	# What the visitor said they came for and carried, and the notes about a VIP:
	# free text about the person, which the gate log copies.
	"purpose_of_visit": _PURGE_MARKER_TEXT,
	"items_carried": None,
	"protocol_notes": None,
	# The sources of two things cleared on the Hospitality Request below (its
	# `special_diet` and `notes` are copied from here): a diet can show a religion
	# or a health condition, and the notes are free text about the visitor.
	# Clearing the copy and keeping the source would clear nothing.
	"special_diet": "None",
	"hospitality_notes": None,
	# "<name> | <mobile>", kept for the list search.
	"visitor_summary": None,
	# Links to a record about the same person in another app (a candidate, a
	# lead). The record there is that app's to keep or delete; the link from an
	# anonymised visit to it is what would still say who visited.
	"job_applicant_link": None,
	"crm_lead_opportunity": None,
	"id_proof_scan": None,
	"visitor_photo": None,
	"gate_verified_photo": None,
	"custom_visa_copy": None,
	# The QR encodes PASS|VISITOR:<name>|VISIT_DATE.
	"qr_code_image": None,
}
_PASS_IDENTITY_FILE_FIELDS = (
	"id_proof_scan",
	"visitor_photo",
	"gate_verified_photo",
	"custom_visa_copy",
	"qr_code_image",
)

# What the visitor declared they were carrying. The category, quantity and the
# verified flag stay; the description and serial number are theirs.
_VISITOR_ITEM_PURGE_VALUES = {
	"item_name": _PURGE_MARKER_TEXT,
	"description": None,
	"serial_number": None,
	"verification_remarks": None,
}

# The other people on a group pass.
_GROUP_MEMBER_PURGE_VALUES = {
	"visitor_name": _PURGE_MARKER_TEXT,
	"mobile_number": None,
	"id_proof_number": None,
	"id_proof_number_masked": None,
	"id_proof_number_entry": None,
	"remarks": None,
}

_SECURITY_LOG_PURGE_VALUES = {
	"visitor_name": _PURGE_MARKER_TEXT,
	"visitor_company": None,
	"mobile_number": None,
	"id_proof_number": None,
	"id_proof_number_masked": None,
	"id_proof_number_entry": None,
	"vehicle_number": None,
	# Copies of the pass's free text, and the guard's own notes about the visitor.
	"purpose_of_visit": None,
	"items_carried": None,
	"vip_protocol_notes": None,
	"verification_notes": None,
	"id_proof_scan": None,
	"visitor_photo": None,
	"photo_at_gate": None,
}
_SECURITY_LOG_IDENTITY_FILE_FIELDS = ("id_proof_scan", "visitor_photo", "photo_at_gate")

# The items the gate checked (Security Item Verify rows of a gate log): what
# they were, their serial numbers, the guard's remarks and the item photos.
_GATE_ITEM_PURGE_VALUES = {
	"item_name": _PURGE_MARKER_TEXT,
	"serial__asset_number": None,
	"remarks": None,
	"security_remarks": None,
	"item_image": None,
}
_GATE_ITEM_FILE_FIELDS = ("item_image",)

# Hospitality Request copies the visitor's name and mobile from the pass, and
# holds what was arranged for them: diet and allergies (health, and a diet can
# show a religion), accessibility needs, where they were picked up and stayed,
# and the driver's name and phone (a third person's data).
_HOSPITALITY_PURGE_VALUES = {
	"visitor_name_display": _PURGE_MARKER_TEXT,
	"visitor_mobile_display": None,
	"dietary_allergies": None,
	"special_diet": "None",
	"accessibility_requirements": "None",
	"notes": None,
	"hotel_special_requests": None,
	"cab_pickup_instructions": None,
	"pickup_location": None,
	"drop_location": None,
	"hotel_name": None,
	"booking_reference": None,
	"greeting_delivery_point": None,
	"buggy_pickup_point": None,
	"buggy_drop_point": None,
	"driver_name": None,
	"driver_phone": None,
}

# A booking made for a pass is titled "Visitor Meeting — <visitor name>"
# (lifecycle.ensure_conference_room_booking); a hand-made one linked to the pass
# may name the visitor in its title or instructions too.
_BOOKING_PURGE_VALUES = {
	"meeting_title": f"Visitor Meeting — {_PURGE_MARKER_TEXT}",
	"special_instructions": None,
}

# Free text about who the visitor was in contact with.
_CONTACT_TRACE_PURGE_VALUES = {
	"close_contacts": None,
	"notes": None,
}

_INVITATION_PURGE_VALUES = {
	"visitor_full_name": _PURGE_MARKER_TEXT,
	"visitor_mobile": None,
	"visitor_email": None,
	# A bearer secret, and the link that contains it.
	"invitation_token": None,
	"portal_submission_url": None,
	"purpose_of_visit": _PURGE_MARKER_TEXT,
}

# A pass a visitor started on the public form and nobody ever sent for approval
# is never "over" by status: it stays a Draft, and the no-show job only looks at
# approved passes. It still holds a name, a mobile number and usually an ID scan.
# Once its visit date is this many days past (or the retention period, when
# that is shorter) it is treated like a finished visit.
ABANDONED_PORTAL_DRAFT_DAYS = 30

# Timeline rows of a purged record that repeat what was just cleared: notes the
# app wrote about the visitor ("Visitor asked to meet: ..."), and the "attached
# <file name>" entries of files that no longer exist. A person's own typed
# comments are left alone, and so is the note that someone viewed the full ID
# number (id_masking.log_reveal), which is about the member of staff.
_PURGED_COMMENT_TYPES = ("Info", "Attachment", "Attachment Removed")


def _delete_file(file_url, doctype, name):
	"""Delete the File row a purged field pointed at, bytes on disk included.

	Clearing the field is not clearing the document: the image stays in the
	files directory until the File row itself is deleted.
	`frappe.delete_doc("File", ...)` runs `File.on_trash`, which removes the
	bytes from disk as part of the same call — so this is the one operation
	that actually gets rid of the photo, not just the pointer to it.

	Only the File rows attached to the purged record itself (`doctype`/`name`,
	one of this app's) are deleted. A row anything else attached under the same
	URL — another app, the site, or another visitor record — is not this purge's
	to remove; while one exists, File.on_trash also keeps the bytes on disk
	(File._delete_file_on_disk deletes them only when no other row shares them).
	"""
	if not file_url:
		return
	for file_name in frappe.get_all(
		"File",
		filters={"file_url": file_url, "attached_to_doctype": doctype, "attached_to_name": name},
		pluck="name",
	):
		try:
			frappe.delete_doc("File", file_name, ignore_permissions=True, force=True, delete_permanently=True)
		except Exception as exc:
			# A File that cannot be deleted (e.g. already gone from disk) must not
			# abort the rest of the batch — the field is about to be cleared either
			# way, and the next run's candidate query will pick the row up again if
			# the field clear itself is what failed.
			frappe.log_error(
				title="VMS Data Retention",
				message=f"Data retention: could not delete File {file_name} ({file_url}): {exc}",
			)


# Doctypes whose forms take uploads; a file attached to one of their unsaved
# ("new-...") names belongs to a form that was abandoned.
_UPLOAD_DOCTYPES = ("Visitor Pass", "Security Log", "Visitor Invitation", "Visitor Blacklist")


def purge_abandoned_uploads():
	"""Put strays back on their record, and delete uploads nothing ever used.

	Looks at desk uploads still on an unsaved form's "new-..." name once they are
	a day old. Frappe relinks those only within 60 minutes of the upload; a pass
	finished later still uses the file. Such a row is moved onto that record (or
	dropped if the record already has its own row for it). Only a row no saved
	record uses is deleted — the form really was abandoned. They are mostly ID
	scans and face photos, so the abandoned ones should not linger. A deleted
	row's bytes are removed only when no other File row shares them
	(File._delete_file_on_disk). See visitor_management/uploads.py.

	Guest files are deliberately NOT looked at. On Frappe 15 the visitor portal
	sends its files inside the submission (portal.submit_pre_registration), which
	stores and attaches them in the same request, or rolls them back with it, so
	it never leaves an unattached guest File behind. Unattached guest Files on a
	site therefore belong to some other app's guest form (v15 web forms leave
	uploads unattached) and are not this app's to delete.
	"""
	from visitormanagement.visitor_management.uploads import saved_record_using

	cutoff = add_to_date(now_datetime(), days=-1)
	relinked, deleted = 0, 0

	for f in frappe.get_all(
		"File",
		filters={
			"attached_to_doctype": ("in", _UPLOAD_DOCTYPES),
			"attached_to_name": ("like", "new-%"),
			"creation": ("<", cutoff),
		},
		fields=["name", "file_url", "attached_to_doctype", "owner"],
	):
		used_by = saved_record_using(f.attached_to_doctype, f.file_url)
		if not used_by:
			deleted += _delete_upload(f.name)
			continue
		record, fieldname = used_by
		if frappe.db.get_value(f.attached_to_doctype, record, "owner") != f.owner:
			# Someone else's record holds this URL: a stray is its creator's own upload,
			# so this one was pasted in. Moving it would hand the file to that record's
			# readers; it is dropped like any abandoned upload.
			deleted += _delete_upload(f.name)
			continue
		if frappe.db.exists(
			"File",
			{
				"file_url": f.file_url,
				"attached_to_doctype": f.attached_to_doctype,
				"attached_to_name": record,
			},
		):
			deleted += _delete_upload(f.name)  # a duplicate row; the record's own row keeps the bytes
		else:
			frappe.db.set_value(
				"File",
				f.name,
				{"attached_to_name": record, "attached_to_field": fieldname},
				update_modified=False,
			)
			relinked += 1

	return {"relinked": relinked, "deleted": deleted}


def _delete_upload(name):
	try:
		frappe.delete_doc("File", name, ignore_permissions=True, force=True, delete_permanently=True)
		return 1
	except Exception as exc:
		frappe.log_error(
			title="VMS Abandoned Uploads", message=f"Could not delete abandoned upload {name}: {exc}"
		)
		return 0


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
			title="VMS Data Retention",
			message=(
				"VMS Settings: Enable Data Retention Purge is on but Retention Period "
				"(days) is not a positive number. No records were purged this run."
			),
		)
		return None, None

	return retention_days, add_to_date(getdate(nowdate()), days=-retention_days)


def purge_expired_visitor_data():
	"""Anonymise identity data on visits old enough that the retention policy
	no longer needs it. See the module docstring above for what is and is not
	touched.

	Runs as independent passes — Visitor Pass, its group members, Security Log,
	Hospitality Request, Conference Room Booking, Contact Trace Record and
	Visitor Invitation — each re-deriving its own eligible rows from the database
	rather than working off names collected by another pass. A Security Log
	row is only ever purged because its own linked Visitor Pass is currently
	terminal and old enough, checked fresh every run; it does not depend on
	that pass having been purged in this same run. That makes each pass
	self-healing on its own across runs — a partial failure, an odd
	ordering, or a Security Log row created after its pass was already
	purged all get picked up on the next run, rather than silently never
	being revisited because whatever list they would have ridden along with
	has since gone empty.

	Batched (`_CHUNK_SIZE`, the same constant the no-show/overstay jobs use)
	and idempotent: every candidate query filters on identity fields still
	being set, so a row already purged has nothing left to match and drops
	out of the very next run's candidates.
	"""
	retention_days, cutoff = _retention_cutoff()
	if not cutoff:
		return 0

	purged_passes = _purge_expired_visitor_passes(cutoff, retention_days)
	purged_members = len(
		_purge_linked(
			"Visitor Group Member",
			"vp.name = t.parent and t.parenttype = 'Visitor Pass'",
			_GROUP_MEMBER_PURGE_VALUES,
			cutoff,
			retention_days,
		)
	)
	purged_items = len(
		_purge_linked(
			"Visitor Item",
			"vp.name = t.parent and t.parenttype = 'Visitor Pass'",
			_VISITOR_ITEM_PURGE_VALUES,
			cutoff,
			retention_days,
		)
	)
	# Before the gate logs: the item rows are found through their log's pass, and
	# their photos are attached to the log.
	purged_items += len(
		_purge_linked(
			"Security Item Verify",
			"t.parenttype = 'Security Log' and vp.name = "
			"(select sl.visitor_pass from `tabSecurity Log` sl where sl.name = t.parent)",
			_GATE_ITEM_PURGE_VALUES,
			cutoff,
			retention_days,
			file_fields=_GATE_ITEM_FILE_FIELDS,
		)
	)
	purged_logs = _purge_expired_security_logs(cutoff, retention_days)
	purged_requests = len(
		_purge_linked(
			"Hospitality Request",
			"vp.name = t.visitor_pass",
			_HOSPITALITY_PURGE_VALUES,
			cutoff,
			retention_days,
		)
	)
	purged_bookings = len(
		_purge_linked(
			"Conference Room Booking",
			"vp.name = t.visitor_pass",
			_BOOKING_PURGE_VALUES,
			cutoff,
			retention_days,
		)
	)
	purged_traces = len(
		_purge_linked(
			"Contact Trace Record",
			"vp.name = t.visitor_pass",
			_CONTACT_TRACE_PURGE_VALUES,
			cutoff,
			retention_days,
		)
	)
	purged_invitations = _purge_expired_invitations(cutoff, retention_days)

	total = (
		purged_passes
		+ purged_members
		+ purged_items
		+ purged_logs
		+ purged_requests
		+ purged_bookings
		+ purged_traces
		+ purged_invitations
	)
	if total:
		print(
			f"  data retention: anonymised {purged_passes} visitor pass(es), "
			f"{purged_members} group member row(s), {purged_items} item row(s), "
			f"{purged_logs} security log(s), "
			f"{purged_requests} hospitality request(s), {purged_bookings} room booking(s), "
			f"{purged_traces} contact trace record(s), {purged_invitations} invitation(s) "
			f"— visits over {retention_days}+ days ago (cutoff {cutoff})"
		)
	return total


def _eligible_pass_sql(alias, cutoff, retention_days):
	"""SQL condition (and its parameters) for "this Visitor Pass may be purged".

	One definition, used by every pass over a table that hangs off a Visitor
	Pass, so the pass and its Security Logs, group members, hospitality request
	and room booking can never disagree about whether the visit is over:

	* the visit date is on or before the cutoff, and the pass is in a terminal
	  status, or was flagged no-show while Approved / Items Verified (see
	  `_NO_SHOW_ELIGIBLE_STATUSES` for why the flag alone is not enough); or
	* it is an abandoned public-form draft (`ABANDONED_PORTAL_DRAFT_DAYS`).
	"""
	draft_days = min(cint(retention_days) or ABANDONED_PORTAL_DRAFT_DAYS, ABANDONED_PORTAL_DRAFT_DAYS)
	draft_cutoff = add_to_date(getdate(nowdate()), days=-draft_days)

	terminal = ", ".join(["%s"] * len(_TERMINAL_PASS_STATUSES))
	no_show = ", ".join(["%s"] * len(_NO_SHOW_ELIGIBLE_STATUSES))
	sql = f"""(
		(
			{alias}.visit_date <= %s
			and (
				{alias}.status in ({terminal})
				or ({alias}.no_show = 1 and {alias}.status in ({no_show}))
			)
		)
		or (
			{alias}.visit_date <= %s
			and {alias}.request_channel = 'Portal'
			and {alias}.docstatus = 0
			and ifnull({alias}.status, 'Draft') = 'Draft'
			and ifnull({alias}.workflow_state, 'Draft') in ('', 'Draft')
			and {alias}.actual_checkin is null
		)
	)"""
	return sql, [cutoff, *_TERMINAL_PASS_STATUSES, *_NO_SHOW_ELIGIBLE_STATUSES, draft_cutoff]


def _still_set_sql(alias, values):
	"""SQL condition (and parameters) for "a column the purge clears still holds something"."""
	parts, params = [], []
	for column, replacement in values.items():
		if replacement is None:
			parts.append(f"ifnull({alias}.`{column}`, '') != ''")
		else:
			parts.append(f"ifnull({alias}.`{column}`, '') not in ('', %s)")
			params.append(replacement)
	return " or ".join(parts), params


def _blank_purged_value(value):
	"""What an identity value becomes inside a purged record's Version rows."""
	return _PURGE_MARKER_TEXT if value else value


def _purge_trail(doctype, names, fieldnames):
	"""Remove what a purged record's timeline and mail history still say about the visitor.

	Clearing the columns is not enough: the old values are also in the record's
	Version rows, in notes and "attached <file>" entries on its timeline, and in
	the alert mails sent about it. All of these belong to this app's own
	records; nothing of any other DocType is looked at.

	Version rows are kept, with the identity fields blanked, so the approval
	history (who moved the record to which state, and when) survives.
	"""
	from visitormanagement.visitor_management.id_masking import REVEAL_COMMENT_SUBJECT, scrub_versions

	names = list(names)
	if not names:
		return
	scrub_versions(doctype, fieldnames, names=names, replace=_blank_purged_value)

	reference = {"reference_doctype": doctype, "reference_name": ("in", names)}
	comments = [
		row.name
		for row in frappe.get_all(
			"Comment",
			filters={**reference, "comment_type": ("in", _PURGED_COMMENT_TYPES)},
			fields=["name", "subject"],
		)
		if row.subject != REVEAL_COMMENT_SUBJECT
	]
	if comments:
		frappe.db.delete("Comment", {"name": ("in", comments)})

	messages = frappe.get_all(
		"Communication", filters={**reference, "communication_type": "Automated Message"}, pluck="name"
	)
	if messages:
		frappe.db.delete("Communication Link", {"parent": ("in", messages), "parenttype": "Communication"})
		frappe.db.delete("Communication", {"name": ("in", messages)})

	queued = frappe.get_all("Email Queue", filters=reference, pluck="name")
	if queued:
		frappe.db.delete("Email Queue Recipient", {"parent": ("in", queued), "parenttype": "Email Queue"})
		frappe.db.delete("Email Queue", {"name": ("in", queued)})

	frappe.db.delete("Notification Log", {"document_type": doctype, "document_name": ("in", names)})


def _purge_linked(
	doctype,
	link_condition,
	values,
	cutoff,
	retention_days,
	file_fields=(),
	child_row_fields=(),
	after_chunk=None,
):
	"""Anonymise the rows of `doctype` whose Visitor Pass is over and old enough.

	`link_condition` joins the row (alias `t`) to its pass (alias `vp`).
	`values` is one of the `_..._PURGE_VALUES` maps; `file_fields` are the
	columns whose File rows (and bytes) are deleted first. `child_row_fields`
	are fields of the record's child rows to blank in its Version rows as well
	(a change to a child row is recorded on the parent). Returns the names of
	the rows purged.

	Table and column names come from the constants in this module, never from
	input; every value is a query parameter.
	"""
	if not frappe.db.table_exists(doctype):
		return []
	values = {column: v for column, v in values.items() if frappe.db.has_column(doctype, column)}
	if not values:
		return []
	file_fields = [f for f in file_fields if f in values]

	eligible, eligible_params = _eligible_pass_sql("vp", cutoff, retention_days)
	still_set, still_set_params = _still_set_sql("t", values)
	is_child_table = bool(frappe.get_meta(doctype).istable)
	selected = "".join(f", t.`{f}`" for f in file_fields)
	if is_child_table:
		# A child row's file is attached to the record the row belongs to.
		selected += ", t.parent, t.parenttype"
	# nosemgrep: frappe-sql-format-injection - identifiers are module constants; values are parameters
	rows = frappe.db.sql(
		f"""
		select t.name{selected}
		from `tab{doctype}` t
		inner join `tabVisitor Pass` vp on {link_condition}
		where {eligible}
		  and ({still_set})
		order by t.name
		""",
		[*eligible_params, *still_set_params],
		as_dict=True,
	)
	if not rows:
		return []

	by_name = {row.name: row for row in rows}
	names = sorted(by_name)
	assignments = ", ".join(f"`{column}` = %s" for column in values)

	for chunk in _chunked(names):
		for name in chunk:
			row = by_name[name]
			holder = (row.parenttype, row.parent) if is_child_table else (doctype, name)
			for fieldname in file_fields:
				_delete_file(row.get(fieldname), *holder)

		placeholders = ", ".join(["%s"] * len(chunk))
		# nosemgrep: frappe-sql-format-injection - identifiers are module constants; values are parameters
		frappe.db.sql(
			f"update `tab{doctype}` set {assignments} where name in ({placeholders})",
			[*values.values(), *chunk],
		)
		if not is_child_table:
			for name in chunk:
				frappe.clear_document_cache(doctype, name)
			_purge_trail(doctype, chunk, [*values, *child_row_fields])
		if after_chunk:
			after_chunk(chunk)

	return names


def _purge_expired_visitor_passes(cutoff, retention_days):
	user = frappe.session.user
	now_ts = now_datetime()

	def after_chunk(chunk):
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
					"details": _format_event_details(
						{"retention_days": retention_days, "cutoff_date": str(cutoff)}
					),
				}
				for name in chunk
			]
		)

	return len(
		_purge_linked(
			"Visitor Pass",
			"vp.name = t.name",
			_PASS_PURGE_VALUES,
			cutoff,
			retention_days,
			file_fields=_PASS_IDENTITY_FILE_FIELDS,
			# Changes to group member and item rows are recorded in the pass's own Version rows.
			child_row_fields=(*_GROUP_MEMBER_PURGE_VALUES, *_VISITOR_ITEM_PURGE_VALUES),
			after_chunk=after_chunk,
		)
	)


def _purge_expired_security_logs(cutoff, retention_days):
	"""Purge Security Log rows whose OWN linked Visitor Pass is currently
	terminal and old enough — re-checked against the database every run,
	never inherited from `_purge_expired_visitor_passes`'s result. See the
	module-level docstring on why that independence matters."""
	return len(
		_purge_linked(
			"Security Log",
			"vp.name = t.visitor_pass",
			_SECURITY_LOG_PURGE_VALUES,
			cutoff,
			retention_days,
			file_fields=_SECURITY_LOG_IDENTITY_FILE_FIELDS,
			child_row_fields=tuple(_GATE_ITEM_PURGE_VALUES),
		)
	)


def _purge_expired_invitations(cutoff, retention_days):
	"""Anonymise Visitor Invitation rows that reached a terminal status and
	whose visit (or, absent one, their own creation) is old enough.

	`invitation_token` is cleared here too — it is a bearer secret with no
	further purpose once the invitation is Submitted/Expired/Cancelled
	(`get_valid_invitation_by_token` already refuses all three statuses), so
	there is nothing to lose and one fewer stale secret sitting in the table.
	`portal_submission_url` goes with it: it is the link that contains the token.
	"""
	values = {
		column: v
		for column, v in _INVITATION_PURGE_VALUES.items()
		if frappe.db.has_column("Visitor Invitation", column)
	}
	or_filters = [
		[column, "is", "set"] if replacement is None else [column, "not in", ["", replacement]]
		for column, replacement in values.items()
	]
	candidates = frappe.get_all(
		"Visitor Invitation",
		filters={"invitation_status": ("in", _TERMINAL_INVITATION_STATUSES)},
		or_filters=or_filters,
		fields=["name", "visit_date", "creation"],
		order_by=None,
	)
	eligible = sorted(c.name for c in candidates if getdate(c.visit_date or c.creation) <= cutoff)
	if not eligible:
		return 0

	assignments = ", ".join(f"`{column}` = %s" for column in values)
	purged = 0
	for chunk in _chunked(eligible):
		placeholders = ", ".join(["%s"] * len(chunk))
		# nosemgrep: frappe-sql-format-injection - identifiers are module constants; values are parameters
		frappe.db.sql(
			f"update `tabVisitor Invitation` set {assignments} where name in ({placeholders})",
			[*values.values(), *chunk],
		)
		for name in chunk:
			frappe.clear_document_cache("Visitor Invitation", name)
		_purge_trail("Visitor Invitation", chunk, list(values))
		purged += len(chunk)

	return purged
