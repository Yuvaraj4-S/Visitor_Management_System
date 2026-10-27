# For license information, please see license.txt

import hmac
import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import getseries
from frappe.utils import (
	add_to_date,
	cint,
	escape_html,
	flt,
	format_datetime,
	formatdate,
	get_datetime,
	getdate,
	now_datetime,
	time_diff_in_seconds,
	today,
)

from visitormanagement.visitor_management import gate_timing
from visitormanagement.visitor_management import settings as vms_settings
from visitormanagement.visitor_management.doctype.visitor_gate.visitor_gate import gates_assigned_to
from visitormanagement.visitor_management.lifecycle import (
	_get_active_contact_trace,
	log_visitor_event,
	sync_contact_trace,
)
from visitormanagement.visitor_management.mail import esc, send_after_commit
from visitormanagement.visitor_management.uploads import (
	adopt_stray_uploads,
	attach_existing_file,
	copy_file_row,
)
from visitormanagement.visitor_management.validators import mask_id_number
from visitormanagement.visitor_management.workflow_builder import APPROVED_STATES

# The movements that change where a visitor is, and so read and move the pass's status.
GATE_MOVEMENTS = ("Check-In", "Check-Out", "Gate Transfer")

# What the scanner puts in `scanned_qr_token` when the QR it read carries a PASS
# but no TOKEN — a pass emailed before passes had tokens.
QR_SCAN_WITHOUT_TOKEN = "NO-TOKEN"

# A check-in / check-out time a System Manager types within this many seconds of
# the server clock is "now", not a back-dated entry needing a reason.
EVENT_TIME_TOLERANCE_SECONDS = 300

# What waiting for another terminal's lock on a pass can end in.
_LOCK_ERRORS = (frappe.QueryDeadlockError, frappe.QueryTimeoutError)


def _get_default_gate(visitor_type_name):
	"""The gate a visitor of this type is routed to.

	Comes from the Visitor Type master. When a type has no gate configured we
	fall back to the first active Visitor Gate rather than a hardcoded name, so
	a site that renames or replaces its gates keeps working. A configured gate
	that has since been deactivated falls back the same way: the save refuses an
	inactive gate, so pre-selecting it only stopped the guard with an error.
	"""
	if visitor_type_name:
		try:
			vt = frappe.get_cached_doc("Visitor Type", visitor_type_name)
			if vt.default_gate and frappe.db.get_value("Visitor Gate", vt.default_gate, "is_active"):
				return vt.default_gate
		except frappe.DoesNotExistError:
			pass

	fallback = frappe.get_all(
		"Visitor Gate", filters={"is_active": 1}, pluck="name", order_by="creation asc", limit=1
	)
	return fallback[0] if fallback else None


def _setting(fieldname, default):
	"""A VMS Settings tick, or `default` until the field exists and has been saved once.

	`get_single_value` throws for a field the DocType does not have yet, and reads
	a tick nobody has saved as 0 — which for a default-on setting such as
	`track_badge_return` would switch it off on every site until someone opened
	the settings page.
	"""
	if not frappe.get_meta("VMS Settings").has_field(fieldname):
		return cint(default)
	singles = frappe.qb.DocType("Singles")
	value = (
		frappe.qb.from_(singles)
		.select(singles.value)
		.where((singles.doctype == "VMS Settings") & (singles.field == fieldname))
		.run(pluck=True)
	)
	if not value or value[0] in (None, ""):
		return cint(default)
	return cint(value[0])


def restricted_gates(user=None):
	"""The gates `user` may record at, or None when every active gate is open to them.

	Opt-in (VMS Settings → "Security can only log at their assigned gates"). A System Manager
	is never restricted, and neither is a guard listed on no gate at all: turning
	the setting on before every guard has been assigned must not lock a site's
	gates on its first morning — the form shows that guard a hint instead.
	"""
	user = user or frappe.session.user
	if not _setting("restrict_security_to_assigned_gate", 0):
		return None
	if user == "Administrator" or "System Manager" in frappe.get_roles(user):
		return None
	return gates_assigned_to(user) or None


def default_gate_for_user(visitor_type_name, user=None):
	"""The gate to pre-fill for this user: the type's gate if they may use it, else their own."""
	gate = _get_default_gate(visitor_type_name)
	allowed = restricted_gates(user)
	if allowed and gate not in allowed:
		return allowed[0]
	return gate


def _member_badge_prefix(visitor_pass):
	"""The badge series a group member's badge is numbered in, or None when the pass gets no badges.

	The lead visitor's badge comes from `VisitorPass.generate_badge_number`:
	`<type's badge prefix>-<visit date>-NNNN`, counted with Frappe's atomic
	series. A member's badge is taken from the same series, so no two people
	on site ever hold the same number. Whether this pass gets badges at all
	(VMS Settings → Enable Badge, the type's Requires Badge, the legacy list) is
	already answered by the lead's own badge: a member is badged when the lead is.
	"""
	if not visitor_pass.badge_number or not vms_settings.flag("enable_badge", 1):
		return None
	prefix = frappe.db.get_value("Visitor Type", visitor_pass.visitor_type, "badge_prefix") or "VIS"
	return f"{prefix}-{str(visitor_pass.visit_date or today()).replace('-', '')}-"


def members_to_move(visitor_pass, event_type, everyone=False):
	"""The pass's Visitor Group Member rows a Check-In or Check-Out can move, in pass order.

	A check-in admits the members not inside yet; a check-out lets out the ones
	who are. `everyone` is a check-in that starts the visit afresh — nobody can
	be inside on a pass that has not been checked in, and a stay closed for a
	missed check-out closes for the whole group.
	"""
	if not cint(visitor_pass.get("is_group_visit")):
		return []
	members = list(visitor_pass.get("group_members") or [])
	if event_type == "Check-In":
		return [m for m in members if everyone or not cint(m.checked_in)]
	if event_type == "Check-Out":
		return [m for m in members if cint(m.checked_in)]
	return []


def _get_employee_email(employee_name):
	if not employee_name:
		return None

	employee = frappe.db.get_value(
		"Employee",
		employee_name,
		["company_email", "personal_email", "user_id"],
		as_dict=True,
	)
	if not employee:
		return None

	return employee.company_email or employee.personal_email or employee.user_id


def _send_host_checkin_email(visitor_pass, security_log, messages_before=None):
	host_email = _get_employee_email(visitor_pass.person_to_visit)
	if not host_email:
		return

	items_summary = "No items declared."
	if visitor_pass.visitor_items:
		item_lines = []
		for item in visitor_pass.visitor_items:
			line = esc(item.item_name)
			if item.quantity:
				line = f"{line} | Qty: {esc(item.quantity)}"
			if item.serial_number:
				line = f"{line} | S/N: {esc(item.serial_number)}"
			item_lines.append(line)
		items_summary = "<br>".join(item_lines)

	try:
		# Sent once the gate event is committed, and never able to fail it — a
		# `now=True` send used to surface a mail-server error as a 500 on a
		# check-in that had already saved (see visitor_management/mail.py).
		send_after_commit(
			recipients=[host_email],
			reference_doctype="Visitor Pass",
			reference_name=visitor_pass.name,
			subject=f"Visitor Arrived: {visitor_pass.visitor_full_name}",
			message=(
				f"<p>Visitor <b>{esc(visitor_pass.visitor_full_name)}</b> has checked in.</p>"
				"<table style='border-collapse: collapse;'>"
				f"<tr><td style='padding:4px 8px;'><b>Pass ID</b></td><td style='padding:4px 8px;'>{esc(visitor_pass.name)}</td></tr>"
				f"<tr><td style='padding:4px 8px;'><b>Visitor Type</b></td><td style='padding:4px 8px;'>{esc(visitor_pass.visitor_type, '-')}</td></tr>"
				f"<tr><td style='padding:4px 8px;'><b>Purpose</b></td><td style='padding:4px 8px;'>{esc(visitor_pass.purpose_of_visit, '-')}</td></tr>"
				f"<tr><td style='padding:4px 8px;'><b>Check-In Time</b></td><td style='padding:4px 8px;'>{esc(security_log.check_in_date_time or now_datetime())}</td></tr>"
				f"<tr><td style='padding:4px 8px;'><b>Gate</b></td><td style='padding:4px 8px;'>{esc(security_log.gate_name, '-')}</td></tr>"
				f"<tr><td style='padding:4px 8px;'><b>Items Declared</b></td><td style='padding:4px 8px;'>{items_summary}</td></tr>"
				"</table>"
			),
		)
	except Exception as exc:
		# Queueing itself can fail (no Email Account at all) — never block check-in.
		frappe.log_error(
			f"Host check-in email failed for {security_log.name}: {exc}", "VMS Host Check-in Email"
		)
		# sendmail raises via frappe.throw, which also queues its own message
		# for the client. Drop it — catching the exception is only half the
		# job; otherwise the officer sees a bare "setup Email Account" popup
		# on every check-in, even though the gate event itself saved fine.
		#
		# Restore the snapshot rather than calling frappe.clear_messages():
		# a blanket clear also wiped messages queued EARLIER in the same save,
		# and on this doctype the earlier message is the blacklist warning
		# raised at the top of before_save. On any site without an outgoing
		# Email Account — which includes every fresh install — that meant a
		# guard was silently never shown "this visitor matches a blacklist
		# entry, verify their ID", because the host-email failure wiped it a
		# moment later. Proven: a weak blacklist match produced 0 client
		# messages at check-in before this change.
		frappe.local.message_log = list(messages_before or [])


def _quantity(value):
	"""A quantity as an officer would say it: 2, not 2.0."""
	value = flt(value)
	return str(int(value)) if value == int(value) else str(value)


def qr_token_matches(visitor_pass, scanned_token):
	"""Whether a QR scan counts for `visitor_pass`; throws for a copied or old QR.

	`visitor_pass` is the pass (a document or a dict carrying `name` and
	`qr_token`), `scanned_token` the TOKEN part of what the scanner read. Returns
	False when nothing was scanned (the pass was typed or picked), True when the
	scan proves the visitor holds this pass's current QR.

	The QR used to carry only the pass name, which is printed on the badge and in
	every email: anyone could make a QR for any pass, and the form simply believed
	the browser's own "scanned" tick. The token is a secret only the latest pass
	email carries.
	"""
	scanned_token = (scanned_token or "").strip()
	if not scanned_token:
		return False

	expected = (visitor_pass.get("qr_token") or "").strip()
	if not expected:
		# A pass issued before passes had tokens has nothing to check against.
		# Its QR is accepted on the PASS part alone, as it always was, so a
		# visitor holding an older pass email is not turned away; such passes
		# age out with their visit dates.
		return True

	if scanned_token == QR_SCAN_WITHOUT_TOKEN or not hmac.compare_digest(
		scanned_token.encode(), expected.encode()
	):
		frappe.throw(
			_(
				"This QR code is not valid for pass {0} — it may be a copy or an old pass. "
				"Ask the visitor for the latest pass email."
			).format(visitor_pass.get("name")),
			title=_("QR Code Not Valid"),
		)
	return True


def gate_blacklist_matches(visitor_pass):
	"""Everyone arriving on this pass who is on the active blacklist.

	The lead visitor and each accompanying visitor, as (label, blacklist entry
	name, group member row or None). Group members are screened when the pass
	leaves Draft, but a blacklisting can come after approval — the gate re-checked
	the lead visitor only, so a barred person listed on somebody else's group
	walked in with no block and no warning.
	"""
	from visitormanagement.visitor_management.doctype.visitor_blacklist.visitor_blacklist import (
		VisitorBlacklist,
	)

	matches = []
	for label, person, member in _people_on_pass(visitor_pass):
		entry = VisitorBlacklist.find_active_match(
			id_proof_number=person.id_proof_number,
			visitor_name=person.visitor_name,
			id_proof_type=person.id_proof_type,
			mobile_number=person.mobile_number,
		)
		if entry:
			matches.append((label, entry, member))
	return matches


def _people_on_pass(visitor_pass):
	"""(label, details, group member row or None) for the lead visitor and each member."""
	lead = frappe._dict(
		visitor_name=visitor_pass.visitor_full_name,
		id_proof_number=visitor_pass.id_proof_number,
		id_proof_type=visitor_pass.id_proof_type,
		mobile_number=visitor_pass.mobile_number,
	)
	people = [(visitor_pass.visitor_full_name, lead, None)]
	for row in visitor_pass.get("group_members") or []:
		label = _("{0} (accompanying visitor, row {1})").format(row.visitor_name, row.idx)
		people.append((label, row, row))
	return people


def record_blacklist_alert(visitor_pass, blacklist, label, outcome, gate_name=None, member_row=None):
	"""Record a blacklist match at the gate as an "Alert" Security Log, and tell security.

	A refused visitor used to leave nothing behind: the refusal is a
	frappe.throw, which rolls back whatever was written before it, and no code
	created an "Alert" log at all — so the two alert cards, which count them,
	could never show a hit and the Security Alert Roles never heard.

	Called directly when entry is allowed, and as a background job (pushed
	before the throw, outside the request's transaction) when it is refused.
	Repeated tries for the same person within ten minutes are one alert.
	"""
	vp = frappe.get_doc("Visitor Pass", visitor_pass)
	entry = frappe.get_doc("Visitor Blacklist", blacklist)
	# The leading "Blacklist match" stays untranslated: the Blacklist Alerts
	# Today card finds these alerts by that prefix on every site language.
	reason = "Blacklist match " + _("({0}): {1}. Reason on file: {2}. {3}").format(
		entry.name, label, entry.reason or _("Not specified"), outcome
	)
	if frappe.db.exists(
		"Security Log",
		{
			"visitor_pass": vp.name,
			"event_type": "Alert",
			"exception_reason": reason,
			"creation": (">", add_to_date(now_datetime(), minutes=-10)),
		},
	):
		return

	frappe.get_doc(
		{
			"doctype": "Security Log",
			"visitor_pass": vp.name,
			"event_type": "Alert",
			"gate_name": gate_name or _get_default_gate(vp.visitor_type),
			"exception_reason": reason,
		}
	).insert(ignore_permissions=True)

	member = next((row for row in vp.get("group_members") or [] if row.name == member_row), None)
	vp._alert_blacklist_match(entry, member=member, outcome=f"{outcome} Pass {vp.name}.")


def refuse_blacklisted_entry(visitor_pass, blacklist, label, gate_name=None, member_row=None):
	"""Queue the Alert for a refusal that is about to be thrown.

	The job goes to the queue now (enqueue_after_commit=False): the throw rolls
	this request back, and anything left for its commit would go with it.
	"""
	try:
		frappe.enqueue(
			"visitormanagement.visitor_management.doctype.security_log.security_log.record_blacklist_alert",
			queue="short",
			enqueue_after_commit=False,
			visitor_pass=visitor_pass,
			blacklist=blacklist,
			label=label,
			outcome=_("Entry was refused at the gate."),
			gate_name=gate_name,
			member_row=member_row,
		)
	except Exception:
		# The queue being down must not turn a refusal into an admission.
		frappe.log_error(title="VMS: could not queue the blacklist alert")


class SecurityLog(Document):
	def before_save(self):
		# Once a Security Log is recorded, it is an immutable gate-event audit record.
		# Block edits from non-admin users so a security officer can't quietly amend
		# what was logged at the gate. System Manager / Administrator may still amend
		# to correct a typo / mis-keyed gate.
		if not self.is_new() and "System Manager" not in (frappe.get_roles() or []):
			frappe.throw(
				_(
					"Security Log {0} has already been recorded and cannot be modified. "
					"Contact your administrator if a correction is needed."
				).format(self.name),
				title=_("Record Locked"),
			)

		# Everything that decides whether a gate event may happen, and everything
		# it sets in motion, belongs to the save that records it. A later save is
		# a System Manager correcting a field on a record of something that has
		# already happened: it stores the correction and nothing else. Re-running
		# the checks refused the correction outright ("already Checked-In"), and
		# replaying the event re-opened the visitor's contact trace.
		is_new = self.is_new()

		# Fetch Visitor Pass once
		vp = None
		if self.visitor_pass:
			vp = frappe.get_doc("Visitor Pass", self.visitor_pass)
			if is_new and self.event_type in GATE_MOVEMENTS:
				self._lock_pass(vp)

		if is_new:
			self._set_security_officer()
			self._keep_only_this_events_time()
			# Set by the server alone. The form used to tick it itself after
			# reading any QR, so a pass number typed into a home-made QR, or the
			# flag sent straight through the API, counted as a scan.
			self.qr_code_scanned = 1 if vp and qr_token_matches(vp, self.get("scanned_qr_token")) else 0
			# A bearer secret: checked, and not kept on a record many people read.
			self.scanned_qr_token = None
		else:
			self.qr_code_scanned = cint(frappe.db.get_value(self.doctype, self.name, "qr_code_scanned"))

		# Who on a group pass this event moves — decided before the checks below,
		# which ask whether it moves only members.
		if is_new:
			self._prepare_group_rows(vp)

		# 0. Re-check blacklist at gate — blacklisting could have happened AFTER pass approval.
		# Only block Check-In; let Check-Out proceed so a blacklisted visitor who is already
		# inside can still leave (the goal is to keep them out, not trap them in).
		# Members arriving after the lead are screened on their own: the lead and
		# the rest of the group are already inside.
		if is_new and vp and self.event_type == "Check-In":
			self._screen_blacklist(
				vp, only_members=self._moving_member_rows() if self.group_members_only else None
			)

		# 1. Auto-fetch visitor info and ID details
		if vp:
			# Some Visitor Types mint the badge at the gate rather than on approval.
			if (
				is_new
				and not vp.badge_number
				and self.event_type == "Check-In"
				and not self.group_members_only
				and frappe.db.get_value("Visitor Type", vp.visitor_type, "issue_badge_at_gate")
			):
				vp.generate_badge_number()
				vp.reload()

			if not self.badge_number:
				self.badge_number = vp.badge_number
			if not self.visitor_name:
				self.visitor_name = vp.visitor_full_name
			if not self.visitor_photo:
				self.visitor_photo = vp.visitor_photo
			if not self.id_proof_scan:
				self.id_proof_scan = vp.id_proof_scan

			# Mask ID proof number — gate security only needs last 4 digits
			if vp.id_proof_number:
				self.id_proof_number = mask_id_number(str(vp.id_proof_number).strip())

		# 2. Auto-assign gate. The form pre-selects the same gate (get_gate_policy),
		# so a log arrives with it already filled; "auto assigned" therefore means
		# the event was recorded at the gate the system chose, worked out here
		# rather than taken from the browser.
		if is_new:
			self._keep_to_assigned_gate(vp)

		if vp and (is_new or not self.gate_name):
			default_gate = (
				default_gate_for_user(vp.visitor_type) if is_new else _get_default_gate(vp.visitor_type)
			)
			if not self.gate_name:
				self.gate_name = default_gate
			if is_new:
				self.gate_auto_assigned = 1 if self.gate_name and self.gate_name == default_gate else 0

		# The gate_name link query already hides inactive gates from the picker, but
		# that is a client-side convenience, not enforcement — a stale form or a
		# direct API call can still submit one. Checked only on creation: a later
		# save correcting an unrelated field on an old, already-recorded log must not
		# start failing just because someone deactivated its gate afterwards.
		if (
			self.is_new()
			and self.gate_name
			and not frappe.db.get_value("Visitor Gate", self.gate_name, "is_active")
		):
			frappe.throw(
				_("Gate {0} is not active and cannot be used for a gate event.").format(self.gate_name),
				title=_("Inactive Gate"),
			)

		# The area a visitor is recorded in is the gate of the event. The field is
		# hidden and the form filled it once, from the first gate chosen — so a Gate
		# Transfer to another gate kept the visitor at the old one.
		if is_new and self.gate_name and (self.event_type == "Gate Transfer" or not self.visited_area):
			self.visited_area = self.gate_name

		# 3. Auto-stamp datetime and validate status sequence
		now = now_datetime()
		if not self.verification_started_on:
			self.verification_started_on = now

		if vp and is_new:
			current_status = vp.status
			if self.event_type == "Check-In":
				# Order matters. "Checked-In" and "Checked-Out" are both outside
				# {Approved, Items Verified}, so testing the general case first
				# meant it always won and the two precise messages below were
				# unreachable. A guard scanning an already-admitted visitor was
				# told the pass "must be approved" — which is both wrong and
				# unactionable, since it already is.
				# A multi-day visitor nobody checked out yesterday is still "Checked-In"
				# this morning. Refusing them pushed the guard into recording a
				# check-out nobody witnessed just to get the queue moving; instead the
				# old stay is closed and flagged (after_insert), and they come in.
				missed_checkout = current_status == "Checked-In" and self._missed_previous_checkout(vp, now)
				# Group members who arrive after the lead come in on a log of their
				# own (group_members_only, set by _prepare_group_rows).
				if current_status == "Checked-In" and not missed_checkout and not self.group_members_only:
					frappe.throw(
						f"Visitor {self.visitor_name or vp.visitor_full_name} is already Checked-In."
						+ self._group_hint(vp)
					)
				# A multi-day Contractor pass (visit_date .. pass_valid_until) is
				# meant to be checked in and out on each of several days — "Checked-Out"
				# only permanently retires a *single-day* pass. Without this carve-out
				# the visitor is refused re-entry on day 2 even though the pass is
				# still inside its declared window. Same window rule as
				# hospitality_request.py's `valid_until = vp.get("pass_valid_until")
				# or visit_date`, applied here to gate re-entry instead of hotel dates.
				# The server's own clock, always. This read the check-in time sent by
				# the form, which a browser (or an API call) could set to any day
				# inside the pass window to get a visitor in on an expired pass.
				effective_checkin_date = getdate(now)
				multi_day_reentry_open = bool(
					vp.multi_day_pass
					and vp.pass_valid_until
					and effective_checkin_date <= getdate(vp.pass_valid_until)
				)
				# Stepping out and coming back is the most ordinary thing a visitor
				# does — lunch, the car park, a forgotten laptop, a smoke break. The
				# rule above only re-opened a Contractor's multi-day pass, and
				# multi_day_pass exists on no other layout, so a Customer who tapped
				# out at 13:00 was locked out for the rest of the day and their host
				# had to raise and re-approve a whole new pass while they waited at
				# the barrier. A pass is valid for its own visit_date; checking out
				# is not what ends that. Re-entry on any LATER date still needs the
				# multi-day window, so this does not widen anything beyond the day
				# the pass was already good for.
				# An overnight visitor stepping out at 00:10 is still on the night
				# that began on the visit date (gate_timing.on_a_pass_day).
				same_day_reentry_open = gate_timing.on_a_pass_day(vp, now)
				if current_status == "Checked-Out" and not (multi_day_reentry_open or same_day_reentry_open):
					frappe.throw(
						f"Visitor {self.visitor_name or vp.visitor_full_name} has already Checked-Out "
						"and the pass is now inactive."
					)
				if (
					current_status not in {"Approved", "Items Verified", "Checked-Out"}
					and not missed_checkout
					and not self.group_members_only
				):
					frappe.throw(
						f"Visitor {self.visitor_name or vp.visitor_full_name} must be approved before check-in. (Current Status: {current_status})"
					)
				# `status` alone is not proof: a pass cancelled after it was checked
				# out still reads "Checked-Out". Same corroboration as
				# visitor_gate.visitor_checkin — submitted, and in an approved state.
				if not (vp.docstatus == 1 and vp.workflow_state in APPROVED_STATES):
					frappe.throw(
						f"Pass {vp.name} is no longer approved (workflow state: {vp.workflow_state}). Entry refused."
					)

				self._stamp_event_time("check_in_date_time", now)

				# Validate check-in is within reasonable window of expected visit.
				# Single-day passes keep the original strict rule (checkin_date must
				# equal visit_date exactly). Multi-day passes accept any date from
				# visit_date through pass_valid_until inclusive — "cannot check in
				# early" still applies to both; only the late side of the window
				# differs. Judged on the server's clock even when a System Manager
				# records an earlier time: the pass must be good on the day the
				# visitor is actually let in. An overnight visitor arriving after
				# midnight (00:30 on a 22:00-06:00 shift) is on the night that began
				# on a pass day, and is let in (gate_timing.on_a_pass_day).
				if vp.visit_date and not gate_timing.on_a_pass_day(vp, now):
					checkin_date = getdate(now)
					expected_date = getdate(vp.visit_date)
					if checkin_date < expected_date:
						frappe.throw(
							f"Check-in date ({checkin_date}) is before the scheduled visit date ({expected_date}). Cannot check in early."
						)
					if vp.multi_day_pass and vp.pass_valid_until:
						window_end = getdate(vp.pass_valid_until)
						if checkin_date > window_end:
							frappe.throw(
								f"Check-in date ({checkin_date}) is after this multi-day pass's validity "
								f"window, which ends {window_end}."
							)
					elif checkin_date > expected_date:
						frappe.throw(
							f"Check-in date ({checkin_date}) is after the scheduled visit date ({expected_date}). Pass is no longer valid for this date."
						)

				if not self.group_members_only:
					self._warn_inactive_host_or_type(vp)

				if missed_checkout:
					self.flags.missed_checkout_since = vp.actual_checkin
					frappe.msgprint(
						_(
							"{0} was not checked out after their visit on {1}. That visit has been "
							"closed and flagged for the supervisor; this check-in goes ahead."
						).format(
							escape_html(self.visitor_name or vp.visitor_full_name or vp.name),
							formatdate(vp.actual_checkin),
						),
						title=_("Missed Check-Out"),
						indicator="blue",
					)

				if self.verification_started_on:
					# How long the OFFICER took, measured to now — not to
					# check_in_date_time. That field can hold a future scheduled
					# time (a pass for a visit three days out), and measuring to
					# it turned a thirty-second check into "Verification Duration:
					# 3d 18h 8m 3s" on the guard's own screen. They reasonably
					# read that as the system being broken. Reported from a live
					# walkthrough. Clamped at zero so a back-dated entry cannot
					# show a negative duration.
					self.verification_duration = max(
						0, time_diff_in_seconds(now, self.verification_started_on)
					)

			elif self.event_type == "Check-Out":
				# Same reasoning as check-in: name the actual situation rather
				# than restating the precondition. A second check-out is a
				# different mistake from checking out someone who never entered.
				# Group members still inside after the lead has left go out on a log
				# of their own (group_members_only, set by _prepare_group_rows).
				if current_status == "Checked-Out" and not self.group_members_only:
					frappe.throw(
						f"Visitor {self.visitor_name or vp.visitor_full_name} has already Checked-Out."
					)
				if current_status not in ("Checked-In", "Checked-Out"):
					frappe.throw(
						f"Visitor {self.visitor_name} must be 'Checked-In' before they can 'Check-Out'. (Current Status: {current_status})"
					)

				self._stamp_event_time("check_out_date_time", now)

				# Check-out must be after the check-in it closes — the latest one.
				prior_checkin = frappe.db.get_value(
					"Security Log",
					{"visitor_pass": self.visitor_pass, "event_type": "Check-In", "docstatus": ["<", 2]},
					"check_in_date_time",
					order_by="check_in_date_time desc",
				)
				if prior_checkin and get_datetime(self.check_out_date_time) <= get_datetime(prior_checkin):
					frappe.throw(
						f"Check-out time ({self.check_out_date_time}) must be after check-in time ({prior_checkin})."
					)

				# Check-in refuses a date outside the pass window; check-out did not
				# look at the window at all, so a visitor could be signed out days
				# after their pass expired and the record read as ordinary.
				#
				# Deliberately a warning, never a throw. Refusing the check-out would
				# strand the visitor at "Checked-In" forever, which is the exact drift
				# `tasks.flag_overstaying_visitors` exists to chase — and its docstring
				# makes the same point from the other side: a checkout asserts somebody
				# watched them leave, so the system must not invent one, and must not
				# block the officer recording a real one either. The overstay job
				# already alerts while they are inside; this puts the fact in front of
				# the officer at the moment they close the record, so the remarks field
				# gets filled while somebody still remembers why.
				window_end = (
					vp.pass_valid_until if vp.multi_day_pass and vp.pass_valid_until else vp.visit_date
				)
				if window_end:
					checkout_date = getdate(self.check_out_date_time)
					expiry = getdate(window_end)
					if checkout_date > expiry:
						days_late = (checkout_date - expiry).days
						# Says it already; the timing note is kept without a second message.
						self.flags.late_checkout_warned = True
						frappe.msgprint(
							msg=_(
								"This pass expired on {0}. The check-out being recorded is {1} day(s) "
								"later, so the visitor was shown as inside the building that whole time. "
								"Check-out is allowed — add a note saying what happened."
							).format(expiry, days_late),
							title=_("Late Check-Out"),
							indicator="orange",
						)

				self._note_exit_gate()

			elif self.event_type == "Gate Transfer" and current_status != "Checked-In":
				frappe.throw(
					f"Visitor {self.visitor_name} must be 'Checked-In' before a gate transfer can be logged."
				)

		# Gate verification requirements are configurable — VMS Settings decides
		# which of these the officer must complete. Previously all four were
		# unconditional and the three matching settings were ignored entirely.
		if is_new and self.event_type in ("Check-In", "Check-Out"):
			movement = "check-in" if self.event_type == "Check-In" else "check-out"

			if vms_settings.flag("qr_scan_required_at_gate") and not self.qr_code_scanned:
				frappe.throw(_("Scan the visitor's QR code before saving the {0}.").format(_(movement)))

			if vms_settings.flag("require_visitor_photo") and not self.photo_at_gate:
				frappe.throw(
					_("Capture a live gate photo before saving the visitor {0}.").format(_(movement))
				)

			if vms_settings.flag("block_check_in_without_verification"):
				if not self.id_proof_match:
					frappe.throw(
						_(
							"Confirm that the visitor matches the ID proof before saving the visitor {0}."
						).format(_(movement))
					)
				if not self.pass_photo_match:
					frappe.throw(
						_(
							"Confirm that the visitor matches the pass creation photo before saving the visitor {0}."
						).format(_(movement))
					)

		# The rows are the pass's declared items, rebuilt here whatever the form
		# sent: one per item, in the pass's order, with what was declared copied
		# from the pass. Only the officer's own findings come from the form.
		# A log that moves only group members carries no item rows: the pass's
		# items came in and go out with the lead visitor, and a guard admitting a
		# latecomer must not be asked to tick the whole group's bags again.
		if is_new and vp and self.event_type in ("Check-In", "Check-Out"):
			if self.group_members_only:
				self.set("items_verification", [])
			else:
				self._reconcile_item_rows(vp)

		for row in self.items_verification or []:
			# A count below zero is a typing slip, not a finding. Saved, it read as
			# verified and became the count check-out compares against.
			if flt(row.quantity_found) < 0:
				frappe.throw(
					_(
						"Row {0}: Quantity Found for {1} cannot be negative. Enter how many were "
						"found, or 0 if it is missing."
					).format(row.idx, escape_html(row.item_name or _("Item"))),
					title=_("Invalid Quantity"),
				)
			if row.quantity_found is not None and row.quantity_declared is not None:
				row.discrepancy = 1 if flt(row.quantity_found) != flt(row.quantity_declared) else 0

		# 7. Check if all items confirmed. At check-out a row whose count is short
		# is not confirmed even when ticked — the tick says the officer looked.
		if self.items_verification:
			all_ok = all(
				r.item_verified and not (self.event_type == "Check-Out" and r.discrepancy)
				for r in self.items_verification
			)
			self.all_items_confirmed = 1 if all_ok else 0
		else:
			self.all_items_confirmed = 1

		# Deliberately last: the rows above are built during this same save, so
		# a check placed with the other gate validations would inspect an empty
		# table and pass every time.
		if is_new and self.event_type == "Check-In" and not self.group_members_only:
			self._assert_items_verified("check-in", vp)
		elif is_new and self.event_type == "Check-Out" and not self.group_members_only:
			self._note_items_not_going_out()

		# After every check that can refuse the movement: a refused save must
		# not also tell the guard the visitor was early.
		if is_new:
			self._note_timing(vp)

	# --------------------------------------------------

	def _set_security_officer(self):
		"""A gate event is recorded under the name of the officer who records it.

		The form locks this field, but that is the form: an insert through the
		API with another Employee in it was stored as sent, so one guard could
		log an event as another. Only a System Manager may name someone else.
		"""
		own = frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "name")
		if self.security_officer and self.security_officer != own:
			if "System Manager" in frappe.get_roles():
				return
		self.security_officer = own

	def _keep_only_this_events_time(self):
		"""Drop a check-in or check-out time that belongs to another kind of event.

		The form stamps one when a pass is picked. Switching the event afterwards —
		to a Gate Transfer, say — left that time on a record it does not describe.
		"""
		if self.event_type != "Check-In":
			self.check_in_date_time = None
		if self.event_type != "Check-Out":
			self.check_out_date_time = None

	# ---- group passes ----------------------------------

	def _prepare_group_rows(self, vp):
		"""Lay out the group members this Check-In / Check-Out can move, and say whether it moves only them.

		A group pass used to pass the gate as one person: the lead's log admitted
		and released everybody on it, and the members' own `checked_in` was never
		written — so nobody could tell a marshal which of the twelve names were
		actually inside. Each member now has a row on the log, ticked by default
		(the whole group arriving together is the ordinary case: one tap, or
		none). The guard unticks whoever is not there.

		The lead visitor's own movement is unchanged. A log for members only —
		latecomers after the lead is in, or members still inside after the lead
		has left — is flagged `group_members_only`, and moves nobody else.
		"""
		self.group_members_only = 0
		if not vp or self.event_type not in ("Check-In", "Check-Out") or not cint(vp.is_group_visit):
			self.set("group_attendance", [])
			return

		status = vp.status
		missed = (
			self.event_type == "Check-In"
			and status == "Checked-In"
			and self._missed_previous_checkout(vp, now_datetime())
		)
		# Nobody can be inside on a pass that has not been checked in yet (a tick
		# typed onto the pass is not a gate record), and a stay closed for a missed
		# check-out is closed for the whole group.
		everyone = self.event_type == "Check-In" and (status in ("Approved", "Items Verified") or missed)
		self.flags.restart_group = everyone
		self._reconcile_member_rows(vp, everyone)

		lead_already_there = status == ("Checked-In" if self.event_type == "Check-In" else "Checked-Out")
		if lead_already_there and not missed and self._moving_member_rows():
			self.group_members_only = 1

	def _reconcile_member_rows(self, vp, everyone=False):
		"""Make the member rows exactly the members this event can move, keeping the guard's ticks.

		Same rule as the item rows: built from the pass, whatever the form sent.
		A row the form did not send is a member nobody unticked, so present; a
		row naming somebody on another pass stops the save.
		"""
		on_pass = {m.name for m in vp.get("group_members") or []}
		sent = {}
		for row in self.get("group_attendance") or []:
			ref = row.group_member_row
			if not ref:
				continue
			if ref not in on_pass:
				frappe.throw(
					_("Group member row {0} is not on pass {1}.").format(escape_html(ref), vp.name),
					title=_("Not On This Pass"),
				)
			sent.setdefault(ref, row)

		rows = []
		for member in members_to_move(vp, self.event_type, everyone):
			row = sent.get(member.name)
			rows.append(
				{
					"group_member_row": member.name,
					"visitor_name": member.visitor_name,
					"present": 1 if row is None or row.present is None else cint(row.present),
					"badge_number": member.badge_number,
				}
			)
		self.set("group_attendance", [])
		for values in rows:
			self.append("group_attendance", values)

	def _moving_member_rows(self):
		"""The Visitor Group Member rows ticked present on this log."""
		return {row.group_member_row for row in self.get("group_attendance") or [] if cint(row.present)}

	def _group_hint(self, vp):
		"""A second sentence for "already Checked-In" on a group pass."""
		if not cint(vp.get("is_group_visit")):
			return ""
		if self.get("group_attendance"):
			return " " + _("To let in group members arriving now, tick them under Group Members.")
		return " " + _("Everyone on this group pass is already inside.")

	def _record_group_attendance(self):
		"""Write who came or went onto the pass's member rows, badging each member on arrival.

		Members not ticked are left as they were: still outside after a
		check-in, still inside after a check-out — either can be moved by a
		later log.
		"""
		if not self.get("group_attendance") and not self.flags.restart_group:
			return

		vp = frappe.get_doc("Visitor Pass", self.visitor_pass)
		moving = self._moving_member_rows()
		if self.flags.restart_group:
			for member in vp.get("group_members") or []:
				if cint(member.checked_in) and member.name not in moving:
					frappe.db.set_value("Visitor Group Member", member.name, "checked_in", 0)

		badge_prefix = _member_badge_prefix(vp) if self.event_type == "Check-In" else None
		members = {m.name: m for m in vp.get("group_members") or []}
		for row in self.get("group_attendance") or []:
			member = members.get(row.group_member_row)
			if not member or not cint(row.present):
				continue
			if self.event_type == "Check-Out":
				frappe.db.set_value("Visitor Group Member", member.name, "checked_in", 0)
				continue
			values = {
				"checked_in": 1,
				"checked_in_on": self.check_in_date_time or now_datetime(),
				"check_in_log": self.name,
			}
			badge = member.badge_number
			if not badge and badge_prefix:
				badge = values["badge_number"] = f"{badge_prefix}{getseries(badge_prefix, 4)}"
			frappe.db.set_value("Visitor Group Member", member.name, values)
			if badge and badge != row.badge_number:
				row.db_set("badge_number", badge, update_modified=False)

		# A pass form opened before this log would otherwise save its old ticks
		# back over these and be refused with "not allowed to change Checked In".
		frappe.db.set_value("Visitor Pass", vp.name, "modified", now_datetime(), update_modified=False)
		frappe.clear_document_cache("Visitor Pass", vp.name)

		if self.event_type == "Check-Out":
			still_inside = [
				m.visitor_name
				for m in vp.get("group_members") or []
				if cint(m.checked_in) and m.name not in moving
			]
			if still_inside:
				frappe.msgprint(
					_(
						"Still inside from this group: {0}. Check them out on another Check-Out log when they leave."
					).format(escape_html(", ".join(still_inside))),
					title=_("Group Members Still Inside"),
					indicator="blue",
				)

	# ---- gates ------------------------------------------

	def _keep_to_assigned_gate(self, vp):
		"""Where VMS Settings restricts Security to their gates, record only at one of the user's own.

		The gate is filled in with theirs when left empty. Alerts the system
		raises on a guard's behalf (`ignore_permissions`) are not the guard
		choosing a gate, and are never refused.
		"""
		if self.flags.ignore_permissions:
			return
		allowed = restricted_gates()
		if not allowed:
			return
		if not self.gate_name:
			self.gate_name = default_gate_for_user(vp.visitor_type if vp else None)
			return
		if self.gate_name not in allowed:
			frappe.throw(
				_(
					"You are assigned to {0}, so you cannot record a gate event at {1}. Pick your own "
					"gate, or ask an administrator to add you to {1} (Visitor Gate → Assigned Security Users)."
				).format(", ".join(allowed), self.gate_name),
				title=_("Not Your Gate"),
			)

	def _note_exit_gate(self):
		"""Record where this visitor came in, and whether they are leaving by another gate.

		Allowed — people leave by the car park, the canteen exit, the gate their
		cab is at — but worth a supervisor seeing, so it is on the log, on the
		visitor's event history and in the Daily Visitor Log.
		"""
		if self.group_members_only:
			logs = {
				frappe.db.get_value("Visitor Group Member", member, "check_in_log")
				for member in self._moving_member_rows()
			}
			gates = {frappe.db.get_value("Security Log", log, "gate_name") for log in logs if log}
		else:
			gates = {
				frappe.db.get_value(
					"Security Log",
					{"visitor_pass": self.visitor_pass, "event_type": "Check-In", "group_members_only": 0},
					"gate_name",
					order_by="check_in_date_time desc, creation desc",
				)
			}
		gates = sorted(gate for gate in gates if gate)
		self.check_in_gate = ", ".join(gates) or None
		self.left_by_other_gate = 1 if self.gate_name and any(g != self.gate_name for g in gates) else 0

	# ---- at check-in --------------------------------------

	def _warn_inactive_host_or_type(self, vp):
		"""Warn the guard — never refuse — when the host or the Visitor Type has gone inactive since approval.

		The visitor is standing at the barrier; turning them away for a record
		somebody else changed helps no one. The guard is told to check who will
		receive them, and the supervisor gets an alert (after_insert).
		"""
		problems = []
		if vp.person_to_visit:
			status = frappe.db.get_value("Employee", vp.person_to_visit, "status")
			if status != "Active":
				problems.append(
					_("The host, {0}, is no longer an active employee (status: {1}).").format(
						vp.host_name or vp.person_to_visit, _(status or "Unknown")
					)
				)
		if vp.visitor_type and not frappe.db.get_value("Visitor Type", vp.visitor_type, "is_active"):
			problems.append(
				_("Visitor Type {0} has been deactivated since this pass was approved.").format(
					vp.visitor_type
				)
			)
		if not problems:
			return
		self.flags.checkin_problems = problems
		frappe.msgprint(
			"<br>".join(escape_html(p) for p in problems)
			+ "<br>"
			+ _(
				"The check-in goes ahead and the security supervisor has been told. Ask reception who will receive this visitor."
			),
			title=_("Check With Reception"),
			indicator="orange",
		)

	def _lock_pass(self, vp):
		"""Hold the pass row until this save commits, and read its state as it is now.

		Two terminals scanning the same pass at the same moment both read
		"Approved", both passed every check, and both checked the visitor in. The
		second save now waits here for the first to commit, then sees "Checked-In".

		The locking read is the one that sees the other terminal's commit: a plain
		read later in this transaction can still return the snapshot it started
		with. So the state is taken from it and put on the pass loaded earlier.
		"""
		try:
			state = frappe.db.get_value(
				"Visitor Pass",
				vp.name,
				["status", "docstatus", "workflow_state", "actual_checkin", "actual_checkout"],
				as_dict=True,
				for_update=True,
			)
		except _LOCK_ERRORS:
			frappe.throw(
				_(
					"Pass {0} is being recorded at another gate terminal right now. "
					"Wait a moment and save again."
				).format(vp.name),
				title=_("Pass Busy"),
			)
		for fieldname, value in (state or {}).items():
			vp.set(fieldname, value)

	def _missed_previous_checkout(self, vp, now):
		"""True when this "Checked-In" pass is really a stay nobody closed on an earlier day.

		Only a multi-day pass can be back on a later day, and only inside its
		window. A visitor checked in earlier today is genuinely inside, and a
		second check-in for them stays refused. Days are shift days: a night
		visitor who came in at 22:00 is still on that night at 00:30.
		"""
		if not (cint(vp.multi_day_pass) and vp.pass_valid_until and vp.actual_checkin):
			return False
		today = gate_timing.shift_day(vp, now)
		return gate_timing.shift_day(vp, vp.actual_checkin) < today <= getdate(vp.pass_valid_until)

	def _note_timing(self, vp):
		"""Arrived or left outside the booked time: a short warning and a note on the log.

		Never a refusal (gate_timing). Judged on the time this save stamped
		(`_stamp_event_time`), never on one the form sent, and set here whatever
		the form sent, so the flag on a locked log can be trusted by a report.
		"""
		at = None
		if vp and self.event_type == "Check-In":
			at = self.check_in_date_time
		elif vp and self.event_type == "Check-Out":
			at = self.check_out_date_time
		note = gate_timing.timing_note(vp, self.event_type, at) if at else None
		self.outside_booked_time = 1 if note else 0
		self.timing_note = note
		if note and not self.flags.late_checkout_warned:
			# A toast, not a dialog: the guard has a queue.
			frappe.msgprint(
				_("Outside booked time: {0} Recorded as usual.").format(note),
				indicator="orange",
				alert=True,
			)

	def _stamp_event_time(self, fieldname, now):
		"""Record the check-in / check-out at the server's clock.

		The form used to fill these with the browser's clock, and the server kept
		whatever arrived, so a wrong PC clock — or anyone editing the request —
		decided when a visitor came and went. Only a System Manager may record a
		different time (a check-out written up after the fact), and then must say
		why in Exception Reason.
		"""
		supplied = self.get(fieldname)
		if supplied and "System Manager" in frappe.get_roles():
			supplied = get_datetime(supplied)
			drift = time_diff_in_seconds(supplied, now)
			if abs(drift) > EVENT_TIME_TOLERANCE_SECONDS:
				if drift > 0:
					frappe.throw(
						_("{0} cannot be in the future.").format(_(self.meta.get_label(fieldname))),
						title=_("Invalid Time"),
					)
				if not (self.exception_reason or "").strip():
					frappe.throw(
						_(
							"You are recording a time other than now ({0}). Say why in Exception Reason."
						).format(format_datetime(supplied)),
						title=_("Reason Needed"),
					)
				self.set(fieldname, supplied)
				return
		self.set(fieldname, now)

	def _reconcile_item_rows(self, vp):
		"""Make the item rows exactly the pass's declared items.

		The rows came from the form and were trusted as sent: deleting a row made
		its item vanish from the check, and a row could name another pass's item,
		whose verification flag was then written over. Now every declared item has
		its row (added if missing), a row for something not on the pass is dropped,
		and a row claiming another pass's item stops the save.

		At check-out a missing row is filled from what was found at check-in.
		"""
		declared = list(vp.get("visitor_items") or [])
		declared_names = {item.name for item in declared}

		sent = {}
		for row in self.get("items_verification") or []:
			ref = row.visitor_item_row_name
			if not ref:
				continue
			if ref not in declared_names:
				frappe.throw(
					_("Item row {0} is not one of the items declared on pass {1}.").format(
						escape_html(ref), vp.name
					),
					title=_("Item Not On This Pass"),
				)
			sent.setdefault(ref, row)

		found_at_check_in = self._quantities_found_at_check_in() if self.event_type == "Check-Out" else {}

		rows = []
		for item in declared:
			row = sent.get(item.name)
			quantity_found = row.quantity_found if row and row.quantity_found is not None else None
			if quantity_found is None:
				quantity_found = found_at_check_in.get(item.name, item.quantity)
			rows.append(
				{
					"visitor_item_row_name": item.name,
					"item_name": item.item_name,
					"item_category": item.item_category,
					# Seed the officer's classification from what the visitor declared.
					# Both fields now share one vocabulary, so this copies straight across.
					# Previously nothing set item_type at all, and its Select had no blank
					# first option, so every row rendered pre-selected on its first choice
					# and saved that way -- 77 of 87 rows read 'Electronic', including
					# fabric samples and a measuring tape.
					"item_type": (row.item_type if row else None) or item.item_category,
					"quantity_declared": item.quantity,
					"uom": item.unit_of_measure,
					"serial__asset_number": item.serial_number,
					"quantity_found": quantity_found,
					"item_verified": cint(row.item_verified) if row else 0,
					# item_image is captured by the gate officer at scan time, not copied from Visitor Item.
					"item_image": row.item_image if row else None,
					"remarks": row.remarks if row else None,
					"security_remarks": row.security_remarks if row else None,
				}
			)

		self.set("items_verification", [])
		for values in rows:
			self.append("items_verification", values)

	def _quantities_found_at_check_in(self):
		"""{Visitor Item row: quantity found} on this pass's latest check-in."""
		check_in = frappe.db.get_value(
			"Security Log",
			{"visitor_pass": self.visitor_pass, "event_type": "Check-In", "docstatus": ["<", 2]},
			"name",
			order_by="check_in_date_time desc, creation desc",
		)
		if not check_in:
			return {}
		return {
			row.visitor_item_row_name: row.quantity_found
			for row in frappe.get_all(
				"Security Item Verify",
				filters={
					"parent": check_in,
					"parenttype": "Security Log",
					"parentfield": "items_verification",
				},
				fields=["visitor_item_row_name", "quantity_found"],
			)
			if row.visitor_item_row_name and row.quantity_found is not None
		}

	def _note_items_not_going_out(self):
		"""At check-out, ask for a reason when a declared item is not going out — never refuse the exit.

		Check-out used to look only at whether the items had been ticked at
		check-in, so what the officer saw at the exit was never recorded at all.
		Now the rows say what is leaving. A row not ticked, or a count short of
		what was declared, needs a few words in Exception Reason; the visitor
		still leaves, and after the save the pass is marked and the supervisor is
		alerted (`_flag_items_not_going_out`). Keeping someone at the barrier
		over a missing pen helps nobody, and the alert is what gets it followed up.

		Where VMS Settings lets the gate skip item verification an unticked row
		only means nobody looked, so there only a short count counts.
		"""
		items_optional = vms_settings.flag("allow_gate_without_item_verification")
		missing = []
		for row in self.items_verification or []:
			name = row.item_name or _("Item")
			if row.discrepancy:
				missing.append(
					_("{0} (found {1} of {2})").format(
						name, _quantity(row.quantity_found), _quantity(row.quantity_declared)
					)
				)
			elif not row.item_verified and not items_optional:
				missing.append(_("{0} (not ticked)").format(name))

		if not missing:
			return

		if not (self.exception_reason or "").strip():
			frappe.throw(
				_(
					"Not every declared item is going out: {0}. Tick the items that are going out, "
					"or type a short reason in Exception Reason and save again. The visitor can "
					"still leave; the supervisor will be alerted."
				).format(escape_html(", ".join(missing))),
				title=_("Item Missing at Check-Out"),
			)
		self.flags.items_not_going_out = missing

	def _screen_blacklist(self, vp, only_members=None):
		"""Check the lead visitor and everyone arriving with them against the blacklist.

		`only_members` (Visitor Group Member row names) screens just those members:
		a log admitting latecomers to a group whose lead is already inside.
		"""

		def screened(member):
			return only_members is None or (member is not None and member.name in only_members)

		from visitormanagement.visitor_management.doctype.visitor_blacklist.visitor_blacklist import (
			VisitorBlacklist,
		)

		# VMS Settings decides whether a match stops entry, merely warns, or is
		# only recorded. This setting used to be ignored — the gate always blocked
		# regardless of what the admin chose.
		action = vms_settings.blacklist_action()
		matched = set()
		for label, blacklist_name, member in gate_blacklist_matches(vp):
			if not screened(member):
				continue
			matched.add(member.name if member else None)
			bl = frappe.get_doc("Visitor Blacklist", blacklist_name)
			# nosemgrep: frappe-translation-python-splitting - the backslashes are \n line breaks, the string is not split
			detail = _(
				"Visitor: {0}\nReason: {1}\nBlocked by: {2}\n\nID matches an active blacklist entry."
			).format(
				escape_html(label),
				escape_html(bl.reason or _("Not specified")),
				escape_html(bl.blocked_by or _("System")),
			)
			member_row = member.name if member else None
			if action == "Block Entry":
				refuse_blacklisted_entry(vp.name, bl.name, label, self.gate_name, member_row)
				frappe.throw(
					msg=detail + "\n" + _("Refuse entry and notify supervisor."),
					title=_("Access Denied at Gate — Blacklisted Visitor"),
				)
			if action == "Alert Only":
				frappe.msgprint(
					msg=detail + "\n" + _("Entry is allowed but flagged — notify supervisor."),
					title=_("Blacklist Warning"),
					indicator="orange",
				)
			record_blacklist_alert(
				vp.name,
				bl.name,
				label,
				_("Entry was allowed (VMS Settings: {0}).").format(_(action)),
				gate_name=self.gate_name,
				member_row=member_row,
			)
			frappe.log_error(detail, f"VMS Blacklist match at gate: {vp.name}")

		# A single weak identifier — name alone or mobile alone — is not enough to
		# bar someone (thousands share a name; mobile numbers get reassigned), so
		# find_active_match deliberately does not return it. But it must not vanish
		# either: an entry blacklisted by name only would otherwise never fire
		# anywhere, and the security team that created it would believe that person
		# is barred. The gate is where they physically turn up, so surface it here
		# as a non-blocking prompt and let the officer verify the ID and decide.
		# Same warning the host sees at pass creation
		# (visitor_pass.py::_warn_weak_blacklist_match) — kept in step so a guard
		# and a host are never told two different things.
		for label, person, member in _people_on_pass(vp):
			if not screened(member) or (member.name if member else None) in matched:
				continue
			weak = VisitorBlacklist.find_weak_match(
				visitor_name=person.visitor_name,
				mobile_number=person.mobile_number,
			)
			if not weak:
				continue
			bl = frappe.get_doc("Visitor Blacklist", weak["name"])
			frappe.msgprint(
				msg=_(
					"{0}: their {1} matches an active blacklist entry ({2}, reason: {3}), but not "
					"strongly enough to block automatically.<br>"
					"Verify their ID against the blacklist entry before allowing entry."
				).format(
					escape_html(label),
					_(weak["matched_on"]),
					bl.name,
					escape_html(bl.reason or _("Not specified")),
				),
				title=_("Possible Blacklist Match — Verify ID"),
				indicator="orange",
			)
			frappe.log_error(
				f"Weak blacklist match at gate on {weak['matched_on']}: {vp.name} ({label}) vs {bl.name}",
				"VMS Blacklist weak match at gate",
			)

	# --------------------------------------------------

	def after_insert(self):
		if not self.visitor_pass:
			return

		# A log that moves only group members leaves the lead's record alone: the
		# pass's status, gate photo, items, host mail and contact trace all belong
		# to the lead visitor's own movements.
		if self.event_type == "Check-In" and self.group_members_only:
			self._record_group_attendance()

		elif self.event_type == "Check-In":
			if self.flags.missed_checkout_since:
				self._close_missed_stay(self.flags.missed_checkout_since)
			self._sync_gate_verification()
			self._sync_item_verification()
			# The badge is minted by the lines above when this check-in is the
			# first thing to need one; this log is the record of it being issued.
			if not self.badge_number:
				badge_number = frappe.db.get_value("Visitor Pass", self.visitor_pass, "badge_number")
				if badge_number:
					self.db_set("badge_number", badge_number, update_modified=False)
			# Also update the Pass status to Checked-In
			self._advance_pass(self._checkin_times())
			# After the lead's badge exists: a member is badged from the same series.
			self._record_group_attendance()
			self._notify_host_arrival()
			if self.flags.checkin_problems:
				self._raise_supervisor_alert(
					_("Checked in although the pass needs attention ({0}): {1}").format(
						self.name, " ".join(self.flags.checkin_problems)
					)
				)

		elif self.event_type == "Check-Out":
			if not self.group_members_only:
				self._advance_pass(
					{
						"status": "Checked-Out",
						"actual_checkout": self.check_out_date_time or now_datetime(),
					}
				)
				if self.flags.items_not_going_out:
					self._flag_items_not_going_out(self.flags.items_not_going_out)
			self._record_group_attendance()
			self._flag_badge_not_returned()

		self._record_lifecycle_event()
		if self.outside_booked_time:
			log_visitor_event(
				self.visitor_pass,
				"Outside Booked Time",
				event_status="Warned",
				source_doctype=self.doctype,
				source_name=self.name,
				details={"movement": self.event_type, "timing_note": self.timing_note},
			)
		if not self.group_members_only:
			sync_contact_trace(self.visitor_pass, self)

	def _badges_leaving(self):
		"""The badge numbers that should be handed back on this check-out."""
		badges = []
		if not self.group_members_only:
			lead = frappe.db.get_value("Visitor Pass", self.visitor_pass, "badge_number")
			if lead:
				badges.append(lead)
		badges += [
			row.badge_number
			for row in self.get("group_attendance") or []
			if cint(row.present) and row.badge_number
		]
		return badges

	def _flag_badge_not_returned(self):
		"""Record and report a badge the guard says was not handed back — the visitor still leaves.

		The tick is on by default: handing the badge back is what nearly every
		visitor does, so the guard taps only for the exception. A badge that went
		home is a door pass in the wrong hands, so the pass's history gets a
		"Badge Not Returned" event and the security supervisor an alert. When it
		turns up, a "Badge Collected" log on the pass closes it.
		"""
		if cint(self.badge_returned) or not _setting("track_badge_return", 1):
			return
		badges = self._badges_leaving()
		if not badges:
			return
		log_visitor_event(
			self.visitor_pass,
			"Badge Not Returned",
			event_status="Flagged",
			source_doctype=self.doctype,
			source_name=self.name,
			details={"badges": ", ".join(badges), "gate_name": self.gate_name},
		)
		self._raise_supervisor_alert(
			_(
				"Badge not returned at check-out ({0}): {1}. When it is handed in, record a "
				"Badge Collected log on this pass."
			).format(self.name, ", ".join(badges))
		)
		frappe.msgprint(
			_("Badge {0} not returned. The supervisor has been told.").format(escape_html(", ".join(badges))),
			alert=True,
			indicator="orange",
		)

	def _close_missed_stay(self, last_check_in):
		"""Close the stay a multi-day visitor was never checked out of, without inventing an exit.

		The pass keeps no check-out time for it and the open contact trace is
		closed with its Time Out left empty — nobody saw them leave, so the
		record does not say when they did. The stay is flagged twice over: a
		"Missed Check-Out" event on the pass's history, and an Alert log the
		security supervisor sees with the other alerts.
		"""
		active_trace = _get_active_contact_trace(self.visitor_pass)
		if active_trace:
			trace = frappe.get_doc("Contact Trace Record", active_trace)
			trace.status = "Closed"
			trace.notes = "\n".join(
				part for part in [trace.notes, _("Missed check-out: exit time was never recorded.")] if part
			)
			trace.save(ignore_permissions=True)

		log_visitor_event(
			self.visitor_pass,
			"Missed Check-Out",
			event_status="Flagged",
			source_doctype=self.doctype,
			source_name=self.name,
			details={
				"last_check_in": str(last_check_in),
				"closed_by_check_in": self.name,
				"gate_name": self.gate_name,
			},
		)
		self._raise_supervisor_alert(
			_(
				"Missed check-out: the visitor was never checked out after checking in on {0}. "
				"That stay was closed without an exit time when they checked in again ({1})."
			).format(formatdate(last_check_in), self.name)
		)

	def _flag_items_not_going_out(self, missing):
		"""Mark the pass and alert the supervisor about items that did not go out.

		Called after a check-out that `_note_items_not_going_out` let through with
		a reason. The pass's own item rows say what happened, so its host and the
		next officer see it too.
		"""
		reason = (self.exception_reason or "").strip()
		declared = {item.name for item in frappe.get_doc("Visitor Pass", self.visitor_pass).visitor_items}
		remark = _("Not accounted for at check-out {0} ({1}): {2}").format(
			self.name, formatdate(self.check_out_date_time), reason
		)
		for row in self.items_verification or []:
			if row.visitor_item_row_name not in declared:
				continue
			if row.discrepancy or not row.item_verified:
				frappe.db.set_value(
					"Visitor Item",
					row.visitor_item_row_name,
					{"verified_by_security": 0, "verification_remarks": remark},
				)

		# "Discrepancy" is what this is; until the pass offers that status the
		# nearest one it has is "Partial" — not every item is accounted for.
		statuses = frappe.get_meta("Visitor Pass").get_options("item_verification_status") or ""
		status = "Discrepancy" if "Discrepancy" in statuses.split("\n") else "Partial"
		frappe.db.set_value(
			"Visitor Pass",
			self.visitor_pass,
			{"item_verification_status": status, "items_verified": 0},
			update_modified=False,
		)

		self._raise_supervisor_alert(
			_("Items not accounted for at check-out ({0}): {1}. Reason given: {2}").format(
				self.name, "; ".join(missing), reason
			)
		)

	def _raise_supervisor_alert(self, reason):
		"""An "Alert" Security Log for this pass, and a mail to the Security Alert Roles.

		The same record a blacklist match leaves (`record_blacklist_alert`), so it
		is counted on the Security Alerts card and listed with the other alerts.
		A mail that cannot be sent never undoes the gate event that raised it.
		"""
		frappe.get_doc(
			{
				"doctype": "Security Log",
				"visitor_pass": self.visitor_pass,
				"event_type": "Alert",
				"gate_name": self.gate_name,
				"exception_reason": reason,
			}
		).insert(ignore_permissions=True)

		messages_before = list(frappe.message_log)
		try:
			vp = frappe.get_doc("Visitor Pass", self.visitor_pass)
			recipients = vp._security_alert_recipients()
			if recipients:
				send_after_commit(
					recipients=recipients,
					reference_doctype="Visitor Pass",
					reference_name=vp.name,
					subject=_("Gate alert: {0}").format(vp.visitor_full_name or vp.name),
					message=(
						f"<p><b>{esc(vp.visitor_full_name or vp.name)}</b> — {esc(vp.name)}</p>"
						f"<p>{esc(reason)}</p>"
						f"<p>{esc(_('Gate'))}: {esc(self.gate_name, '-')}</p>"
					),
				)
		except Exception as exc:
			frappe.log_error(f"Gate alert email failed for {self.name}: {exc}", "VMS Gate Alert")
			frappe.local.message_log = messages_before

	# --------------------------------------------------

	def on_update(self):
		adopt_stray_uploads(self)
		self._attach_pass_files()
		# The gate event itself — the pass's status, the visitor event, the contact
		# trace — is recorded once, by after_insert. It used to be replayed here as
		# well: on the insert that closed the contact trace just opened and
		# re-activated it with its time-out still set, and on a later correction it
		# re-opened a finished trace and moved the visitor back to the old gate.
		if self.flags.in_insert and self.event_type == "Check-In":
			# The gate photo's File row has only now been moved onto this log.
			self._share_gate_photo_with_pass()

	# --------------------------------------------------

	def _attach_pass_files(self):
		"""Give this log its own File rows for the pass's photo and ID scan.

		before_save copies both URLs from the pass, and Frappe's attach hook then
		inserts a File row for each. That insert re-checks access against ONE
		existing row for the URL (File.validate_private_file_access, limit=1) —
		for a returning visitor or a second gate event, a record this officer
		cannot read. Core swallows the refusal but its message stays queued as an
		error, and the desk then shows only "You do not have permission to access
		this file", dropping the blacklist and late check-out warnings queued by
		the same save.

		The officer can read the pass and so already sees both files; the rows are
		copied from the pass's own (uploads.copy_file_row), and the hook, which
		runs after on_update, finds them attached.
		"""
		if not self.visitor_pass or not frappe.has_permission("Visitor Pass", "read", self.visitor_pass):
			return
		for fieldname in ("visitor_photo", "id_proof_scan"):
			url = self.get(fieldname)
			if not url or not url.startswith("/private/"):
				continue
			original = frappe.db.get_value(
				"File",
				{
					"file_url": url,
					"attached_to_doctype": "Visitor Pass",
					"attached_to_name": self.visitor_pass,
				},
				"name",
			)
			if original:
				copy_file_row(original, self, fieldname)

	def _sync_gate_verification(self):
		# `photo_at_gate` used to fall back to the visitor's own pre-registration
		# photo whenever the officer had not captured one. That photo was then
		# stamped onto the pass as `gate_verified_photo` with a time and an
		# officer's name, so the record asserted a gate verification that never
		# happened — and the "does the visitor match their pass photo?" check
		# compared an image against itself, which it can never fail. A gate photo
		# now exists only if somebody took one; the badge already falls back to the
		# pass photo for display, and its "gate verified" marker is keyed on this
		# field, so it now means what it says.
		if not self.visitor_pass or not self.photo_at_gate:
			return

		frappe.db.set_value("Visitor Pass", self.visitor_pass, self._gate_verification_values())

	def _gate_verification_values(self):
		values = {
			"gate_verified_photo": self.photo_at_gate,
			"gate_verified_on": self.check_in_date_time or now_datetime(),
		}

		if self.security_officer:
			values["gate_verified_by"] = self.security_officer

		visitor_type = frappe.db.get_value("Visitor Pass", self.visitor_pass, "visitor_type")
		if visitor_type and frappe.db.get_value("Visitor Type", visitor_type, "issue_badge_at_gate"):
			# Types photographed at the gate have no pre-approval photo to keep.
			values["visitor_photo"] = self.photo_at_gate
		return values

	def _share_gate_photo_with_pass(self):
		if not self.visitor_pass or not self.photo_at_gate:
			return
		values = self._gate_verification_values()

		# The photo is a private file attached to this log. Copying its URL onto
		# the pass is not enough for the pass's own readers (host, approvers) to
		# open it: Frappe grants a private file through the record it is attached
		# to. Give the pass its own File row for each field it now shows — only
		# for a photo actually taken for this log: a URL typed into
		# photo_at_gate (another pass's ID scan, say) would otherwise be handed
		# to this pass's host.
		taken_here = frappe.db.exists(
			"File",
			{
				"file_url": self.photo_at_gate,
				"attached_to_doctype": self.doctype,
				"attached_to_name": self.name,
				"attached_to_field": "photo_at_gate",
			},
		)
		for fieldname in ("gate_verified_photo", "visitor_photo"):
			if taken_here and values.get(fieldname):
				attach_existing_file(values[fieldname], "Visitor Pass", self.visitor_pass, fieldname)

	# --------------------------------------------------

	def _sync_item_verification(self):
		vp = frappe.get_doc("Visitor Pass", self.visitor_pass)

		total_items = len(self.items_verification or [])
		verified_count = sum(1 for r in (self.items_verification or []) if r.item_verified)

		if total_items == 0 or verified_count == total_items:
			new_status = "All Verified"
			items_verified_flag = 1
		elif verified_count > 0:
			new_status = "Partial"
			items_verified_flag = 0
		else:
			new_status = "Pending"
			items_verified_flag = 0

		# Update Visitor Item rows — this pass's own only. before_save already
		# refuses a row naming another pass's item; this keeps a write to one
		# impossible however the rows got here.
		declared = {item.name for item in vp.get("visitor_items") or []}
		for row in self.items_verification or []:
			if row.visitor_item_row_name in declared:
				verification_remarks = row.security_remarks
				if not verification_remarks:
					verification_remarks = (
						"Verified at gate" if row.item_verified else "Pending security verification"
					)

				frappe.db.set_value(
					"Visitor Item",
					row.visitor_item_row_name,
					{
						"verified_by_security": row.item_verified,
						"verification_remarks": verification_remarks,
					},
				)

		# Update Visitor Pass
		frappe.db.set_value(
			"Visitor Pass",
			self.visitor_pass,
			{
				"item_verification_status": new_status,
				"items_verified": items_verified_flag,
			},
		)

		# Generate badge only if fully verified
		if items_verified_flag and not vp.badge_number:
			vp.reload()
			vp.generate_badge_number()
			frappe.msgprint(
				f"All items verified! Badge issued: {vp.badge_number}", alert=True, indicator="green"
			)

	def _assert_items_verified(self, movement, visitor_pass=None):
		"""Refuse a check-in while a declared item is still unverified.

		Declaring items exists to answer one question — did what came in also go
		out. The system already computed the answer (`all_items_confirmed`, and
		`item_verification_status` left at "Pending") and simply never acted on
		it, so a visitor could be checked in and back out with tools nobody
		looked at, and the record only said so afterwards.

		`block_check_in_without_verification` is not this check despite the
		name — it gates `id_proof_match` and `pass_photo_match`, which are
		identity, not items. There was no item equivalent at all.

		Check-in only. The rows are rebuilt on this very save from the pass's
		declared items (`_reconcile_item_rows`), so they are the source of truth.
		Check-out has rows of its own now too, and is never refused over them:
		`_note_items_not_going_out` asks for a reason and alerts instead.

		Off by default. Turning it on makes the gate stricter, and a queue of
		visitors waiting while an officer ticks boxes is a real cost only the
		site can weigh.
		"""
		# Enforced by default. The setting is an opt-out, not an opt-in: a site
		# that declares items and then admits the visitor without checking them
		# has recorded a promise it never kept, and the gate officer gets one
		# tick-box for however many physical objects the visitor is carrying.
		if vms_settings.flag("allow_gate_without_item_verification"):
			return

		outstanding = [
			(row.item_name or _("Item")) for row in (self.items_verification or []) if not row.item_verified
		]

		if not outstanding:
			return

		frappe.throw(
			_(
				"Verify every declared item before saving the visitor {0}. Still "
				"unverified: {1}. Tick <b>Item Verified</b> on each row of Items "
				"Verification."
			).format(movement, ", ".join(outstanding)),
			title=_("Items Not Verified"),
		)

	def _checkin_times(self):
		"""Pass fields for a check-in, re-entry included.

		A visitor who steps out and comes back the same day has not newly
		arrived: `actual_checkin` keeps the first arrival of the day (the
		overstay alert counts hours on site from it) — overwriting it with the
		return time made a visitor in since 09:00 look like they came at 14:00.
		On another day of a multi-day pass it is a new arrival. Either way the
		visitor is inside again, so the last `actual_checkout` no longer applies.
		Every movement is still on its own Security Log.
		"""
		arrived = self.check_in_date_time or now_datetime()
		updates = {"status": "Checked-In", "no_show": 0, "actual_checkout": None}
		first_arrival = frappe.db.get_value("Visitor Pass", self.visitor_pass, "actual_checkin")
		if not (first_arrival and getdate(first_arrival) == getdate(arrived)):
			updates["actual_checkin"] = arrived
		return updates

	def _advance_pass(self, updates):
		"""Move the pass to its next state and let the alerts see the transition.

		`status` is not allow_on_submit, so a submitted pass cannot be advanced
		with a save — the write has to go through db.set_value. That is plain
		SQL: it never loads the document, so on_change never runs and every
		Notification watching `status` is skipped. Check-out was therefore
		silently sending nothing, and the visitor's thank-you mail had never
		gone out on any real visit.

		Re-running the value-change pass by hand needs a before-image, since
		that is what evaluate_alert diffs against to decide a field changed.

		A failing alert must never strand a visitor at the gate, so delivery
		problems are logged rather than raised — the state change is already
		committed by then, and blocking check-out on a broken email template
		would be worse than a missing email.
		"""
		before = frappe.get_doc("Visitor Pass", self.visitor_pass)
		frappe.db.set_value("Visitor Pass", self.visitor_pass, updates)

		after = frappe.get_doc("Visitor Pass", self.visitor_pass)
		after._doc_before_save = before

		# A failed Notification does not always raise up to us: core's
		# Notification.send_notification_by_channel() already catches its own
		# sendmail failure and logs it without re-raising — but frappe.sendmail
		# queues its "setup Email Account" message for the client via
		# frappe.throw() *before* raising, so the message survives even though
		# the exception never does. Snapshot the queue first and restore it
		# after, rather than a blanket frappe.clear_messages(): this trims only
		# what run_notifications added, so an earlier, legitimate alert in the
		# same save (e.g. _sync_item_verification's "badge issued" message)
		# still reaches the officer.
		messages_before_alert = list(frappe.message_log)
		try:
			after.run_notifications("on_change")
		except Exception:
			frappe.log_error(
				title=f"Visitor Pass alert failed after {self.event_type}",
				message=frappe.get_traceback(with_context=True),
			)
		finally:
			frappe.local.message_log = messages_before_alert

	def _notify_host_arrival(self):
		if self.event_type != "Check-In" or not self.visitor_pass:
			return

		visitor_pass = frappe.get_doc("Visitor Pass", self.visitor_pass)
		# Snapshot taken HERE, not inside the helper, so anything already queued
		# for the officer this save — above all the blacklist warning — is what
		# gets restored if the mail fails.
		_send_host_checkin_email(visitor_pass, self, messages_before=list(frappe.message_log))

	def _record_lifecycle_event(self):
		if not self.visitor_pass:
			return

		details = {
			"gate_name": self.gate_name,
			"visited_area": self.visited_area,
			"security_officer": self.security_officer,
			"exception_reason": self.exception_reason,
		}
		if self.group_members_only:
			details["group_members_only"] = _("Yes")
		present = [row.visitor_name for row in self.get("group_attendance") or [] if cint(row.present)]
		absent = [row.visitor_name for row in self.get("group_attendance") or [] if not cint(row.present)]
		if present:
			details["group_members"] = ", ".join(present)
		if absent:
			details["group_members_not_present"] = ", ".join(absent)
		if self.event_type == "Check-Out":
			details["check_in_gate"] = self.check_in_gate
			if self.left_by_other_gate:
				details["left_by_other_gate"] = _("Yes")
			if _setting("track_badge_return", 1) and self._badges_leaving():
				details["badge_returned"] = _("Yes") if cint(self.badge_returned) else _("No")

		log_visitor_event(
			self.visitor_pass,
			self.event_type,
			event_status="Recorded",
			source_doctype=self.doctype,
			source_name=self.name,
			details=details,
		)


@frappe.whitelist()
def get_gate_policy(visitor_pass: str | None = None, event_type: str | None = None):
	"""What this site actually requires before a gate event may be saved.

	The badge checklist used to hardcode its own list, which demanded a photo and
	both identity confirmations regardless of configuration. On a site that
	requires none of them the officer could save the check-in and then be told to
	go back and complete steps — on a record that locks itself on save, so the
	instruction could never be carried out.

	The form marks its fields mandatory from this too, so the desk asks for
	exactly what `before_save` enforces: a gate PC on a plain-HTTP network has no
	camera at all, and a photo the site does not require must not stop it.

	With `visitor_pass`, also the gate the server would assign that pass, for the
	form to pre-select (it used to guess from a hardcoded map of type names). With
	`event_type` as well, `timing_warning`: the note the save would record if the
	movement were recorded now (gate_timing), so the guard is told when the pass
	is picked — on the server's clock, as the save will judge it.
	"""
	frappe.has_permission("Security Log", "read", throw=True)
	policy = {
		"qr_scan_required": bool(vms_settings.flag("qr_scan_required_at_gate")),
		"photo_required": bool(vms_settings.flag("require_visitor_photo")),
		"identity_match_required": bool(vms_settings.flag("block_check_in_without_verification")),
		"items_optional": bool(vms_settings.flag("allow_gate_without_item_verification")),
		"track_badge_return": bool(_setting("track_badge_return", 1)),
	}
	# Gate restriction: the gates this user may pick, and whether to tell a
	# Security user who is on no gate yet that an administrator should add them.
	allowed = restricted_gates()
	policy["allowed_gates"] = allowed or []
	policy["no_gate_assigned"] = bool(
		not allowed
		and _setting("restrict_security_to_assigned_gate", 0)
		and "System Manager" not in frappe.get_roles()
		and frappe.session.user != "Administrator"
	)
	if visitor_pass and frappe.has_permission("Visitor Pass", "read", visitor_pass):
		vp = frappe.db.get_value(
			"Visitor Pass", visitor_pass, ["visitor_type", *gate_timing.PASS_FIELDS], as_dict=True
		)
		policy["default_gate"] = default_gate_for_user(vp.visitor_type) if vp else None
		if vp and event_type in ("Check-In", "Check-Out"):
			policy["timing_warning"] = gate_timing.timing_note(vp, event_type, now_datetime())
	elif allowed:
		policy["default_gate"] = allowed[0]
	return policy


@frappe.whitelist()
def get_member_rows(visitor_pass: str, event_type: str):
	"""The group member rows a new gate log for `visitor_pass` starts with, and who is where.

	Built by the code that rebuilds them on save. Everyone is ticked: the group
	arriving or leaving together is the common case, so the guard only unticks.
	"""
	frappe.has_permission("Security Log", "create", throw=True)
	vp = frappe.get_doc("Visitor Pass", visitor_pass)
	vp.check_permission("read")
	log = frappe.new_doc("Security Log")
	log.visitor_pass = vp.name
	log.event_type = event_type
	log._prepare_group_rows(vp)
	members = (vp.get("group_members") or []) if cint(vp.is_group_visit) else []
	return {
		"rows": [
			{
				fieldname: row.get(fieldname)
				for fieldname in ("group_member_row", "visitor_name", "present", "badge_number")
			}
			for row in log.group_attendance
		],
		"group_members_only": cint(log.group_members_only),
		"members_inside": sum(1 for m in members if cint(m.checked_in)),
		"members_outside": sum(1 for m in members if not cint(m.checked_in)),
	}


def backfill_group_attendance():
	"""Mark the members of group passes that went in before per-member check-in as inside.

	Before it, the lead's check-in admitted the whole group and wrote nothing on
	the member rows; the head counts now count members from those rows. A group
	inside at upgrade time would otherwise vanish from the evacuation roll call
	until they left. Only passes inside now with no gate log carrying member rows
	are touched, so it is safe to run on every migrate. Returns how many members
	it marked.
	"""
	member = frappe.qb.DocType("Visitor Group Member")
	visitor_pass = frappe.qb.DocType("Visitor Pass")
	attendance = frappe.qb.DocType("Security Log Member")
	log = frappe.qb.DocType("Security Log")
	recorded = (
		frappe.qb.from_(attendance)
		.inner_join(log)
		.on(log.name == attendance.parent)
		.select(log.visitor_pass)
		.where(attendance.parenttype == "Security Log")
	)
	rows = (
		frappe.qb.from_(member)
		.inner_join(visitor_pass)
		.on(visitor_pass.name == member.parent)
		.select(member.name, visitor_pass.actual_checkin)
		.where(member.parenttype == "Visitor Pass")
		.where(visitor_pass.is_group_visit == 1)
		.where(visitor_pass.status == "Checked-In")
		.where(visitor_pass.docstatus == 1)
		.where(member.checked_in == 0)
		.where(visitor_pass.name.notin(recorded))
		.run(as_dict=True)
	)
	for row in rows:
		frappe.db.set_value(
			"Visitor Group Member",
			row.name,
			{"checked_in": 1, "checked_in_on": row.actual_checkin},
			update_modified=False,
		)
	return len(rows)


@frappe.whitelist()
def get_item_rows(visitor_pass: str, event_type: str):
	"""The item rows a new gate log for `visitor_pass` starts with.

	Built by the same code that rebuilds them on save, so what the officer sees
	is what will be recorded: every declared item, and at check-out the count
	found at check-in.
	"""
	frappe.has_permission("Security Log", "create", throw=True)
	vp = frappe.get_doc("Visitor Pass", visitor_pass)
	vp.check_permission("read")
	log = frappe.new_doc("Security Log")
	log.visitor_pass = vp.name
	log.event_type = event_type
	log._reconcile_item_rows(vp)
	fields = [
		"visitor_item_row_name",
		"item_name",
		"item_category",
		"item_type",
		"quantity_declared",
		"uom",
		"serial__asset_number",
		"quantity_found",
		"item_verified",
	]
	return [{fieldname: row.get(fieldname) for fieldname in fields} for row in log.items_verification]


@frappe.whitelist()
def get_approved_vip_queue(visit_date: str | None = None):
	target_date = getdate(visit_date) if visit_date else getdate()
	# get_list (not get_all) applies the Visitor Pass row-level permission model,
	# so only users entitled to VIP passes (HOD/CEO, or Security for approved/
	# checked-in passes) receive this roster — not every authenticated user.
	# "VIP" here means any Visitor Type configured with the VIP layout — not a
	# type literally named VIP — so a site's own executive type shows up too.
	vip_types = frappe.get_all("Visitor Type", filters={"detail_layout": "VIP", "is_active": 1}, pluck="name")
	if not vip_types:
		return []

	return frappe.get_list(
		"Visitor Pass",
		filters={
			"visitor_type": ["in", vip_types],
			"visit_date": target_date,
			"status": ["in", ["Approved", "Items Verified", "Checked-In"]],
		},
		fields=[
			"name",
			"visitor_full_name",
			"company__organisation",
			"visit_date",
			"expected_checkin",
			"expected_checkout",
			"person_to_visit",
			"host_name",
			"purpose_of_visit",
			"status",
			"workflow_state",
			"mdceo_notified",
			"conference_room",
			"meal_type",
			"number_of_people",
			"protocol_notes",
		],
		order_by="expected_checkin asc, modified asc",
		limit_page_length=0,  # return the full day's queue (get_all had no limit)
	)
