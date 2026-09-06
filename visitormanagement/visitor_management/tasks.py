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
		frappe.db.sql(
			"update `tabSeries` set `current` = `current` + %s where name=%s", (count, prefix)
		)
	else:
		start = 0
		frappe.db.sql(
			"insert into `tabSeries` (`name`, `current`) values (%s, %s)", (prefix, count)
		)

	return [f"{prefix}{str(start + i + 1).zfill(digits)}" for i in range(count)]


_VEL_COLUMNS = [
	"name", "creation", "modified", "modified_by", "owner", "docstatus", "idx",
	"visitor_pass", "event_type", "event_status", "event_time", "source_doctype",
	"source_name", "details",
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
		frappe.db.sql(
			f"""update `tabVisitor Pass`
			set no_show = 1, current_location = 'No Show'
			where name in ({placeholders})
			  and no_show = 0
			  and docstatus < 2
			  and status in ('Approved', 'Items Verified')""",  # nosemgrep: frappe-sql-format-injection - placeholders only
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
	out, and stays "Checked-In" forever. On this site that left 17 people shown
	as on the premises, the oldest for 25 days — so the honest answer to "who is
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
			# docstring above), so without a bound this scan only ever grows —
			# on this site 17 of 187 passes are already stuck, one for 25 days.
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
		f"<p style='color:#1f2933;'>{len(rows)} visitor(s) have been on site longer than "
		f"the {max_hours}-hour limit and have not been checked out.</p>",
		"<table role='presentation' width='100%' style='border-collapse:collapse;table-layout:fixed;'>",
	]
	for row in rows:
		lines.append(
			"<tr>"
			f"<td style='padding:4px 2px;color:#1f2933;word-break:break-word;'>{row.visitor_full_name or row.name}</td>"
			f"<td style='padding:4px 2px;color:#5b6b7b;word-break:break-word;'>host {row.host_name or '-'}</td>"
			f"<td style='padding:4px 2px;color:#b42318;'>{row.hours_in} h on site</td>"
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
# mobile, email, government ID number, vehicle number, and the ID-scan/photo
# Files — on Visitor Pass and on the Security Log rows for the same visit.
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
# Found the hard way: a live proof run on a shared site purged
# VP-2026-00221 (host HR-EMP-00057) — a Visitor Pass sitting in "Pending
# Approval" / "Pending System Manager", i.e. still actively awaiting
# approval right now — solely because it carried a stale no_show=1 that
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
	["vehicle_number", "is", "set"],
	["company__organisation", "is", "set"],
	["id_proof_scan", "is", "set"],
	["visitor_photo", "is", "set"],
	["gate_verified_photo", "is", "set"],
]
_PASS_IDENTITY_FILE_FIELDS = ("id_proof_scan", "visitor_photo", "gate_verified_photo")

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


def _delete_file(file_url):
	"""Delete the File row a purged field pointed at, bytes on disk included.

	Clearing the field is not clearing the document: the image stays in the
	files directory until the File row itself is deleted.
	`frappe.delete_doc("File", ...)` runs `File.on_trash`, which removes the
	bytes from disk as part of the same call — so this is the one operation
	that actually gets rid of the photo, not just the pointer to it.
	"""
	if not file_url:
		return
	name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if not name:
		return
	try:
		frappe.delete_doc("File", name, ignore_permissions=True, force=True, delete_permanently=True)
	except Exception as exc:
		# A File that cannot be deleted (e.g. already gone from disk) must not
		# abort the rest of the batch — the field is about to be cleared either
		# way, and the next run's candidate query will pick the row up again if
		# the field clear itself is what failed.
		frappe.log_error(
			f"Data retention: could not delete File {name} ({file_url}): {exc}", "VMS Data Retention"
		)


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


def purge_expired_visitor_data():
	"""Anonymise identity data on visits old enough that the retention policy
	no longer needs it. See the module docstring above for what is and is not
	touched.

	Runs as three independent passes — Visitor Pass, Security Log, Visitor
	Invitation — each re-deriving its own eligible rows from the database
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
	purged_logs = _purge_expired_security_logs(cutoff, retention_days)
	purged_invitations = _purge_expired_invitations(cutoff, retention_days)

	total = purged_passes + purged_logs + purged_invitations
	if total:
		print(
			f"  data retention: anonymised {purged_passes} visitor pass(es), "
			f"{purged_logs} security log(s), {purged_invitations} invitation(s) "
			f"— visits over {retention_days}+ days ago (cutoff {cutoff})"
		)
	return total


def _purge_expired_visitor_passes(cutoff, retention_days):
	names = set()
	names.update(
		frappe.get_all(
			"Visitor Pass",
			filters={"visit_date": ("<=", cutoff), "status": ("in", _TERMINAL_PASS_STATUSES)},
			or_filters=_PASS_IDENTITY_OR_FILTERS,
			pluck="name",
			order_by=None,
		)
	)
	names.update(
		frappe.get_all(
			"Visitor Pass",
			filters={
				"visit_date": ("<=", cutoff),
				"no_show": 1,
				"status": ("in", _NO_SHOW_ELIGIBLE_STATUSES),
			},
			or_filters=_PASS_IDENTITY_OR_FILTERS,
			pluck="name",
			order_by=None,
		)
	)
	if not names:
		return 0

	names = sorted(names)
	user = frappe.session.user
	now_ts = now_datetime()
	purged = 0

	for chunk in _chunked(names):
		rows = frappe.get_all(
			"Visitor Pass",
			filters={"name": ("in", chunk)},
			fields=["name", *_PASS_IDENTITY_FILE_FIELDS],
		)
		for row in rows:
			for fieldname in _PASS_IDENTITY_FILE_FIELDS:
				_delete_file(row.get(fieldname))

		placeholders = ", ".join(["%s"] * len(chunk))
		frappe.db.sql(
			f"""
			update `tabVisitor Pass`
			set visitor_full_name = %s, mobile_number = NULL, mobile_digits = NULL,
			    email_id = NULL, id_proof_number = NULL, vehicle_number = NULL,
			    company__organisation = NULL,
			    id_proof_scan = NULL, visitor_photo = NULL, gate_verified_photo = NULL
			where name in ({placeholders})
			""",  # nosemgrep: frappe-sql-format-injection - placeholders only
			[_PURGE_MARKER_TEXT, *chunk],
		)
		for name in chunk:
			frappe.clear_document_cache("Visitor Pass", name)

		insert_rows = [
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
		_bulk_write_event_logs(insert_rows)
		purged += len(chunk)

	return purged


def _purge_expired_security_logs(cutoff, retention_days):
	"""Purge Security Log rows whose OWN linked Visitor Pass is currently
	terminal and old enough — re-checked against the database every run,
	never inherited from `_purge_expired_visitor_passes`'s result. See the
	module-level docstring on why that independence matters."""
	# The IN-list is spelled out as its own placeholders rather than handed to
	# the driver as a single tuple parameter — pymysql's tuple-to-IN-list
	# expansion is not something this codebase relies on anywhere else, so this
	# does not either.
	status_placeholders = ", ".join(["%s"] * len(_TERMINAL_PASS_STATUSES))
	rows = frappe.db.sql(
		f"""
		select sl.name, sl.id_proof_scan, sl.visitor_photo, sl.photo_at_gate
		from `tabSecurity Log` sl
		inner join `tabVisitor Pass` vp on vp.name = sl.visitor_pass
		where vp.visit_date <= %s
		  and (vp.status in ({status_placeholders}) or vp.no_show = 1)
		  and (
		        ifnull(sl.visitor_name, '') not in ('', %s)
		     or ifnull(sl.visitor_company, '') != ''
		     or ifnull(sl.mobile_number, '') != ''
		     or ifnull(sl.id_proof_number, '') != ''
		     or ifnull(sl.vehicle_number, '') != ''
		     or ifnull(sl.id_proof_scan, '') != ''
		     or ifnull(sl.visitor_photo, '') != ''
		     or ifnull(sl.photo_at_gate, '') != ''
		  )
		order by sl.name
		""",  # nosemgrep: frappe-sql-format-injection - placeholders only, no user input
		[cutoff, *_TERMINAL_PASS_STATUSES, _PURGE_MARKER_TEXT],
		as_dict=True,
	)
	if not rows:
		return 0

	by_name = {r.name: r for r in rows}
	names = sorted(by_name)
	purged = 0

	for chunk in _chunked(names):
		for name in chunk:
			row = by_name[name]
			for fieldname in _SECURITY_LOG_IDENTITY_FILE_FIELDS:
				_delete_file(row.get(fieldname))

		placeholders = ", ".join(["%s"] * len(chunk))
		frappe.db.sql(
			f"""
			update `tabSecurity Log`
			set visitor_name = %s, visitor_company = NULL, mobile_number = NULL,
			    id_proof_number = NULL, vehicle_number = NULL,
			    id_proof_scan = NULL, visitor_photo = NULL, photo_at_gate = NULL
			where name in ({placeholders})
			""",  # nosemgrep: frappe-sql-format-injection - placeholders only
			[_PURGE_MARKER_TEXT, *chunk],
		)
		for name in chunk:
			frappe.clear_document_cache("Security Log", name)
		purged += len(chunk)

	return purged


def _purge_expired_invitations(cutoff, retention_days):
	"""Anonymise Visitor Invitation rows that reached a terminal status and
	whose visit (or, absent one, their own creation) is old enough.

	`invitation_token` is cleared here too — it is a bearer secret with no
	further purpose once the invitation is Submitted/Expired/Cancelled
	(`get_valid_invitation_by_token` already refuses all three statuses), so
	there is nothing to lose and one fewer stale secret sitting in the table.
	"""
	candidates = frappe.get_all(
		"Visitor Invitation",
		filters={"invitation_status": ("in", _TERMINAL_INVITATION_STATUSES)},
		or_filters=_INVITATION_IDENTITY_OR_FILTERS,
		fields=["name", "visit_date", "creation"],
		order_by=None,
	)
	eligible = sorted(
		c.name for c in candidates if getdate(c.visit_date or c.creation) <= cutoff
	)
	if not eligible:
		return 0

	purged = 0
	for chunk in _chunked(eligible):
		placeholders = ", ".join(["%s"] * len(chunk))
		frappe.db.sql(
			f"""
			update `tabVisitor Invitation`
			set visitor_full_name = %s, visitor_mobile = NULL, visitor_email = NULL,
			    invitation_token = NULL
			where name in ({placeholders})
			""",  # nosemgrep: frappe-sql-format-injection - placeholders only
			[_PURGE_MARKER_TEXT, *chunk],
		)
		for name in chunk:
			frappe.clear_document_cache("Visitor Invitation", name)
		purged += len(chunk)

	return purged
