# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, get_datetime, getdate, now_datetime, time_diff_in_seconds

from visitormanagement.visitor_management import settings as vms_settings
from visitormanagement.visitor_management.id_masking import mask_id
from visitormanagement.visitor_management.lifecycle import (
	log_visitor_event,
	sync_contact_trace,
)
from visitormanagement.visitor_management.mail import esc, send_after_commit
from visitormanagement.visitor_management.uploads import adopt_stray_uploads, attach_existing_file
from visitormanagement.visitor_management.workflow_builder import APPROVED_STATES

# The gate movements. Only these advance the pass, and only these carry the
# site's verification requirements (QR scan, live photo, identity ticks, items).
MOVEMENTS = ("Check-In", "Check-Out")


def mask_id_number(raw, id_type=None):
	"""Masked form of an ID number (see visitor_management/id_masking.py).

	Kept under its old name for callers outside this module; the rules live in
	one place now, so the gate, the pass and every report mask the same way.
	"""
	return mask_id(id_type, raw)


def _get_default_gate(visitor_type_name):
	"""The gate a visitor of this type is routed to.

	Comes from the Visitor Type master. When a type has no gate configured we
	fall back to the first active Visitor Gate rather than a hardcoded name, so
	a site that renames or replaces its gates keeps working.
	"""
	if visitor_type_name:
		try:
			vt = frappe.get_cached_doc("Visitor Type", visitor_type_name)
			if vt.default_gate:
				return vt.default_gate
		except frappe.DoesNotExistError:
			pass

	fallback = frappe.get_all(
		"Visitor Gate", filters={"is_active": 1}, pluck="name", order_by="creation asc", limit=1
	)
	return fallback[0] if fallback else None


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


def _employee_display_name(employee):
	"""An Employee's name for display, read server-side.

	Security holds no permission on Employee (an HRMS DocType) and the app
	grants none, so a Link to it shows "HR-EMP-00012" on the guard's screen.
	The log stores the name next to the link instead.
	"""
	if not employee:
		return None
	return frappe.db.get_value("Employee", employee, "employee_name") or None


def _own_employee():
	if frappe.session.user in ("Administrator", "Guest"):
		return None
	return frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "name")


def gate_blacklist_verdict(vp):
	"""What the blacklist says about a visitor arriving at the gate, and what
	VMS Settings "Blacklist Action" says to do about it.

	`vp` is a Visitor Pass document or a dict of its fields. Returns None when
	nothing matches, otherwise a dict:

	    outcome      "block" | "warn" | "log"
	    title        heading for the officer (None for "log")
	    message      text for the officer (None for "log")
	    log_title    title of the Error Log row
	    log_message  body of the Error Log row

	One evaluation, used by the Security Log on save and by every gate endpoint
	(api/visitor_gate.py). The QR-scan endpoint used to refuse every match
	outright whatever the setting said, so a site on "Alert Only" got an alert on
	the form and a hard refusal from its scanner for the same visitor.

	A strong match (ID number, or name and mobile together) follows the setting:
	Block Entry refuses, Alert Only warns and lets the officer continue, Log Only
	records it and shows nothing. A weak match (name alone, or mobile alone) is
	never enough to bar someone — thousands share a name and mobile numbers are
	reassigned — so it is always a prompt to check the ID by hand, the same
	warning the host sees when the pass is raised.
	"""
	from visitormanagement.visitor_management.doctype.visitor_blacklist.visitor_blacklist import (
		VisitorBlacklist,
	)

	pass_name = vp.get("name")
	visitor = vp.get("visitor_full_name")

	strong = VisitorBlacklist.find_active_match(
		id_proof_number=vp.get("id_proof_number"),
		visitor_name=visitor,
		id_proof_type=vp.get("id_proof_type"),
		mobile_number=vp.get("mobile_number"),
	)
	if strong:
		bl = frappe.db.get_value("Visitor Blacklist", strong, ["reason", "blocked_by"], as_dict=True) or {}
		detail = _(
			"Visitor: {0}\nReason: {1}\nBlocked by: {2}\n\nID matches an active blacklist entry."
		).format(visitor, bl.get("reason") or _("Not specified"), bl.get("blocked_by") or _("System"))
		verdict = {
			"log_title": f"VMS Blacklist match at gate: {pass_name}"[:140],
			"log_message": f"{detail}\n\nBlacklist entry: {strong}",
		}
		action = vms_settings.blacklist_action()
		if action == "Block Entry":
			verdict.update(
				outcome="block",
				title=_("Access Denied at Gate — Blacklisted Visitor"),
				message=detail + "\n" + _("Refuse entry and notify supervisor."),
			)
		elif action == "Alert Only":
			verdict.update(
				outcome="warn",
				title=_("Blacklist Warning"),
				message=detail + "\n" + _("Entry is allowed but flagged — notify supervisor."),
			)
		else:
			verdict.update(outcome="log", title=None, message=None)
		return verdict

	weak = VisitorBlacklist.find_weak_match(visitor_name=visitor, mobile_number=vp.get("mobile_number"))
	if not weak:
		return None
	reason = frappe.db.get_value("Visitor Blacklist", weak["name"], "reason")
	return {
		"outcome": "warn",
		"title": _("Possible Blacklist Match — Verify ID"),
		"message": _(
			"This visitor's {0} matches an active blacklist "
			"entry ({1}, reason: {2}), but not "
			"strongly enough to block automatically.\n"
			"Verify their ID against the blacklist entry before allowing entry."
		).format(weak["matched_on"], weak["name"], reason or _("Not specified")),
		"log_title": "VMS Blacklist weak match at gate",
		"log_message": f"Weak blacklist match at gate on {weak['matched_on']}: {pass_name} vs {weak['name']}",
	}


def record_gate_blacklist(verdict, refused=False):
	"""Write a blacklist verdict to the Error Log.

	A refusal is followed by `frappe.throw`, which rolls the request back and
	would take an ordinary insert with it — the one event a site most wants a
	trace of left none. `defer_insert` queues the row outside the transaction.
	"""
	if not verdict:
		return
	try:
		frappe.log_error(
			title=verdict["log_title"],
			message=verdict["log_message"],
			defer_insert=bool(refused),
		)
	except Exception:
		# Logging must never be the reason a gate decision fails.
		pass


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
	except Exception:
		# Queueing itself can fail (no Email Account at all) — never block check-in.
		# Keyword arguments: on Frappe 15 the first positional argument of
		# log_error is the 140-character title, and an SMTP error text is longer.
		frappe.log_error(
			title="VMS Host Check-in Email",
			message=f"Host check-in email failed for {security_log.name}\n\n{frappe.get_traceback()}",
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


class SecurityLog(Document):
	def before_save(self):
		"""Validate and complete a gate event.

		A Security Log IS the gate event: inserting a Check-In moves the pass to
		"Checked-In", and the log then locks. So everything that decides whether
		the movement may happen — the pass's state and dates, the blacklist, and
		the verification the site requires — is checked here, on insert, on the
		server. The form's own required marks are a convenience for the officer
		and nothing more.

		A later save can only be a System Manager correcting a recorded log. It
		does not re-run the movement checks: the pass has moved on since (a
		recorded check-in would always be refused as "already Checked-In"), and
		a requirement switched on afterwards must not make an old record
		uneditable.
		"""
		recording = self.is_new()
		is_admin = "System Manager" in (frappe.get_roles() or [])

		# Once a Security Log is recorded, it is an immutable gate-event audit record.
		# Block edits from non-admin users so a security officer can't quietly amend
		# what was logged at the gate. System Manager / Administrator may still amend
		# to correct a typo / mis-keyed gate.
		if not recording and not is_admin:
			frappe.throw(
				_(
					"Security Log {0} has already been recorded and cannot be modified. "
					"Contact your administrator if a correction is needed."
				).format(self.name),
				title=_("Record Locked"),
			)

		# Which visitor moved, and which way, is the record itself. Changing either
		# afterwards would leave the pass in the state the original event put it in.
		if not recording and (self.has_value_changed("visitor_pass") or self.has_value_changed("event_type")):
			frappe.throw(
				_(
					"The Visitor Pass and Event Type of a recorded Security Log cannot be changed. "
					"Record a new gate event instead."
				),
				title=_("Record Locked"),
			)

		# A log copies the visitor's name, mobile number, purpose and photo from the
		# pass it names (fetch_from, and _copy_pass_snapshot below). The gate
		# endpoints refuse a pass the officer may not read — one that is not
		# approved yet, say (api/visitor_gate._load_gate_pass). A log typed into the
		# form or posted to the API directly gets the same answer for the same
		# reason, whatever its event type.
		if recording and self.visitor_pass and not self.flags.ignore_permissions:
			self._assert_may_use_pass()

		# A movement being recorded locks its pass until this request ends. Two
		# officers recording the same visitor at two gates in the same second
		# would otherwise both read "Approved" and both check the visitor in.
		vp = None
		if self.visitor_pass:
			vp = frappe.get_doc(
				"Visitor Pass", self.visitor_pass, for_update=recording and self.event_type in MOVEMENTS
			)

		# 0. Re-check blacklist at gate — blacklisting could have happened AFTER pass approval.
		# Only block Check-In; let Check-Out proceed so a blacklisted visitor who is already
		# inside can still leave (the goal is to keep them out, not trap them in).
		if vp and recording and self.event_type == "Check-In":
			self._apply_blacklist_action(vp)

		# 1. Auto-fetch visitor info and ID details
		if vp:
			self._copy_pass_snapshot(vp)

		# 2. Auto-assign gate
		if vp and not self.gate_name:
			self.gate_name = _get_default_gate(vp.visitor_type)
			self.gate_auto_assigned = 1

		# The gate_name link query already hides inactive gates from the picker, but
		# that is a client-side convenience, not enforcement — a stale form or a
		# direct API call can still submit one. Checked only on creation: a later
		# save correcting an unrelated field on an old, already-recorded log must not
		# start failing just because someone deactivated its gate afterwards.
		if (
			recording
			and self.gate_name
			and not frappe.db.get_value("Visitor Gate", self.gate_name, "is_active")
		):
			frappe.throw(
				_("Gate {0} is not active and cannot be used for a gate event.").format(self.gate_name),
				title=_("Inactive Gate"),
			)

		# 3. Auto-stamp datetime and validate status sequence
		now = now_datetime()
		if not self.verification_started_on:
			self.verification_started_on = now

		if vp and recording:
			self._validate_movement(vp, now)

		# 4. The officer. Security staff log on their own behalf: the form shows the
		# field read-only to them, but that is the browser's word, so a posted value
		# is replaced here. A System Manager may record on an officer's behalf.
		if recording and (not is_admin or not self.security_officer):
			self.security_officer = _own_employee()
		self.security_officer_name = _employee_display_name(self.security_officer)

		# 5. What the site requires before a movement may be recorded.
		if recording and self.event_type in MOVEMENTS:
			self._assert_gate_requirements(vp)

		if self.event_type == "Gate Transfer" and not self.visited_area:
			frappe.throw(_("Visited Area is required for gate transfer tracking."))

		# 6. Declared items
		if recording and self.event_type == "Check-In" and vp:
			self._add_missing_declared_items(vp)

		for row in self.items_verification or []:
			if row.quantity_found is not None and row.quantity_declared is not None:
				row.discrepancy = 1 if row.quantity_found != row.quantity_declared else 0

		# 7. Check if all items confirmed
		if self.items_verification:
			all_ok = all(r.item_verified for r in self.items_verification)
			self.all_items_confirmed = 1 if all_ok else 0
		else:
			self.all_items_confirmed = 1

		# Deliberately last: the rows above are built during this same save, so
		# a check placed with the other gate validations would inspect an empty
		# table and pass every time.
		if recording and self.event_type in MOVEMENTS:
			self._assert_items_verified("check-in" if self.event_type == "Check-In" else "check-out", vp)

	# --------------------------------------------------

	def _assert_may_use_pass(self):
		"""Refuse a pass that does not exist or that the caller may not read — one answer for both."""
		# Imported here: the gate API module imports this one.
		from visitormanagement.visitor_management.api.visitor_gate import _refuse_unusable_pass

		if frappe.db.exists("Visitor Pass", self.visitor_pass) and frappe.has_permission(
			"Visitor Pass", "read", doc=self.visitor_pass
		):
			return
		_refuse_unusable_pass()

	def _apply_blacklist_action(self, vp):
		verdict = gate_blacklist_verdict(vp)
		if not verdict:
			return
		if verdict["outcome"] == "block":
			record_gate_blacklist(verdict, refused=True)
			frappe.throw(msg=verdict["message"], title=verdict["title"])
		record_gate_blacklist(verdict)
		if verdict["outcome"] == "warn":
			frappe.msgprint(msg=verdict["message"], title=verdict["title"], indicator="orange")

	def _copy_pass_snapshot(self, vp):
		# Nothing here changes the pass. A badge that is issued at the gate is
		# minted once the check-in is recorded (after_insert), not while it is
		# still being validated and may yet be refused.
		if not self.badge_number:
			self.badge_number = vp.badge_number
		if not self.visitor_name:
			self.visitor_name = vp.visitor_full_name
		if not self.visitor_photo:
			self.visitor_photo = vp.visitor_photo
		if not self.id_proof_scan:
			self.id_proof_scan = vp.id_proof_scan

		# The log never holds the full ID number. Whatever the browser posted in
		# this field is replaced with the masked form of the pass's own number.
		if vp.id_proof_number:
			self.id_proof_number = mask_id(vp.id_proof_type, vp.id_proof_number)
		elif self.is_new():
			self.id_proof_number = None

		# The host by name: the pass keeps it (copied from the Employee on save).
		self.host_name = vp.get("host_name") or _employee_display_name(vp.person_to_visit)

	def _validate_movement(self, vp, now):
		"""Is this movement legal for the pass, right now? Insert only."""
		current_status = vp.status
		if self.event_type == "Check-In":
			# Order matters. "Checked-In" and "Checked-Out" are both outside
			# {Approved, Items Verified}, so testing the general case first
			# meant it always won and the two precise messages below were
			# unreachable. A guard scanning an already-admitted visitor was
			# told the pass "must be approved" — which is both wrong and
			# unactionable, since it already is.
			if current_status == "Checked-In":
				frappe.throw(
					_("Visitor {0} is already Checked-In.").format(self.visitor_name or vp.visitor_full_name)
				)
			# A multi-day Contractor pass (visit_date .. pass_valid_until) is
			# meant to be checked in and out on each of several days — "Checked-Out"
			# only permanently retires a *single-day* pass. Without this carve-out
			# the visitor is refused re-entry on day 2 even though the pass is
			# still inside its declared window. Same window rule as
			# hospitality_request.py's `valid_until = vp.get("pass_valid_until")
			# or visit_date`, applied here to gate re-entry instead of hotel dates.
			effective_checkin_date = (
				getdate(self.check_in_date_time) if self.check_in_date_time else getdate(now)
			)
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
			same_day_reentry_open = effective_checkin_date == getdate(vp.visit_date)
			if current_status == "Checked-Out" and not (multi_day_reentry_open or same_day_reentry_open):
				frappe.throw(
					_("Visitor {0} has already Checked-Out and the pass is now inactive.").format(
						self.visitor_name or vp.visitor_full_name
					)
				)
			if current_status not in {"Approved", "Items Verified", "Checked-Out"}:
				frappe.throw(
					_("Visitor {0} must be approved before check-in. (Current Status: {1})").format(
						self.visitor_name or vp.visitor_full_name, current_status
					)
				)
			# `status` alone is not proof: a pass cancelled after it was checked
			# out still reads "Checked-Out". Same corroboration as
			# visitor_gate.visitor_checkin — submitted, and in an approved state.
			if not (vp.docstatus == 1 and vp.workflow_state in APPROVED_STATES):
				frappe.throw(
					_("Pass {0} is no longer approved (workflow state: {1}). Entry refused.").format(
						vp.name, vp.workflow_state
					)
				)

			if not self.check_in_date_time:
				self.check_in_date_time = now

			# Validate check-in is within reasonable window of expected visit.
			# Single-day passes keep the original strict rule (checkin_date must
			# equal visit_date exactly). Multi-day passes accept any date from
			# visit_date through pass_valid_until inclusive — "cannot check in
			# early" still applies to both; only the late side of the window
			# differs.
			if vp.visit_date and self.check_in_date_time:
				checkin_date = getdate(self.check_in_date_time)
				expected_date = getdate(vp.visit_date)
				if checkin_date < expected_date:
					frappe.throw(
						_(
							"Check-in date ({0}) is before the scheduled visit date ({1}). Cannot check in early."
						).format(checkin_date, expected_date)
					)
				if vp.multi_day_pass and vp.pass_valid_until:
					window_end = getdate(vp.pass_valid_until)
					if checkin_date > window_end:
						frappe.throw(
							_(
								"Check-in date ({0}) is after this multi-day pass's validity window, which ends {1}."
							).format(checkin_date, window_end)
						)
				elif checkin_date > expected_date:
					frappe.throw(
						_(
							"Check-in date ({0}) is after the scheduled visit date ({1}). "
							"Pass is no longer valid for this date."
						).format(checkin_date, expected_date)
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
				self.verification_duration = max(0, time_diff_in_seconds(now, self.verification_started_on))

		elif self.event_type == "Check-Out":
			# Same reasoning as check-in: name the actual situation rather
			# than restating the precondition. A second check-out is a
			# different mistake from checking out someone who never entered.
			if current_status == "Checked-Out":
				frappe.throw(
					_("Visitor {0} has already Checked-Out.").format(
						self.visitor_name or vp.visitor_full_name
					)
				)
			if current_status != "Checked-In":
				frappe.throw(
					_(
						"Visitor {0} must be 'Checked-In' before they can 'Check-Out'. (Current Status: {1})"
					).format(self.visitor_name, current_status)
				)

			if not self.check_out_date_time:
				self.check_out_date_time = now

			# Check-out must be after check-in
			prior_checkin = frappe.db.get_value(
				"Security Log",
				{"visitor_pass": self.visitor_pass, "event_type": "Check-In", "docstatus": ["<", 2]},
				"check_in_date_time",
			)
			if prior_checkin and get_datetime(self.check_out_date_time) <= get_datetime(prior_checkin):
				frappe.throw(
					_("Check-out time ({0}) must be after check-in time ({1}).").format(
						self.check_out_date_time, prior_checkin
					)
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
			window_end = vp.pass_valid_until if vp.multi_day_pass and vp.pass_valid_until else vp.visit_date
			if window_end:
				checkout_date = getdate(self.check_out_date_time)
				expiry = getdate(window_end)
				if checkout_date > expiry:
					days_late = (checkout_date - expiry).days
					frappe.msgprint(
						msg=_(
							"This pass expired on {0}. The check-out being recorded is {1} day(s) "
							"later, so the visitor was shown as inside the building that whole time. "
							"Check-out is allowed — add a note saying what happened."
						).format(expiry, days_late),
						title=_("Late Check-Out"),
						indicator="orange",
					)

		elif self.event_type == "Gate Transfer" and current_status != "Checked-In":
			frappe.throw(
				_("Visitor {0} must be 'Checked-In' before a gate transfer can be logged.").format(
					self.visitor_name
				)
			)

	def _assert_gate_requirements(self, vp):
		"""The three VMS Settings gate switches, enforced where the event is recorded.

		- QR Scan Required at Gate            -> `qr_code_scanned`
		- Require Visitor Photo               -> a live `photo_at_gate`
		- Block Check-In Without Verification -> `id_proof_match` and `pass_photo_match`

		Each applies to a Check-In and to a Check-Out, and only when its switch
		is on. They are checked on the server because the form cannot be relied
		on: a Check field always "has a value" to the desk's mandatory check, so
		an unticked box never stopped a save, and anything can post to the API.
		"""
		movement = "check-in" if self.event_type == "Check-In" else "check-out"

		if vms_settings.flag("qr_scan_required_at_gate") and not cint(self.qr_code_scanned):
			frappe.throw(_("Scan the visitor's QR code before saving the {0}.").format(_(movement)))

		if vms_settings.flag("require_visitor_photo"):
			if not self.photo_at_gate:
				frappe.throw(
					_("Capture a live gate photo before saving the visitor {0}.").format(_(movement))
				)
			if not self._gate_photo_taken_here(vp):
				frappe.throw(
					_(
						"The gate photo must be a photo captured or attached on this Security Log. "
						"Capture it again before saving the visitor {0}."
					).format(_(movement)),
					title=_("Gate Photo Not Accepted"),
				)

		if vms_settings.flag("block_check_in_without_verification"):
			if not cint(self.id_proof_match):
				frappe.throw(
					_("Confirm that the visitor matches the ID proof before saving the visitor {0}.").format(
						_(movement)
					)
				)
			if not cint(self.pass_photo_match):
				frappe.throw(
					_(
						"Confirm that the visitor matches the pass creation photo before saving the visitor {0}."
					).format(_(movement))
				)

	def _gate_photo_taken_here(self, vp):
		"""Is `photo_at_gate` a file this officer uploaded for this log?

		The field is an Attach, so its value is only a URL, and a URL can be
		typed. "A live photo was taken" must not be satisfiable by pasting the
		visitor's own pre-registration photo or ID scan (the image the officer is
		meant to compare the live one against), or any other file on the site.

		Accepted: a File row for this URL that is
		  - attached to this log's `photo_at_gate` — under its real name, or under
		    the unsaved form's "new-..." name it was uploaded to (Frappe moves it
		    onto the record after insert: core/doctype/file/utils.py
		    relink_mismatched_files); or
		  - an upload of the officer's own that is not attached to anything yet
		    (an integration that uploads first and posts the URL).
		"""
		url = self.photo_at_gate
		if vp and url in {vp.get("visitor_photo"), vp.get("id_proof_scan")}:
			return False

		user = frappe.session.user
		temporary_name = self.get("__temporary_name")
		for row in frappe.get_all(
			"File",
			filters={"file_url": url},
			fields=["attached_to_doctype", "attached_to_name", "attached_to_field", "owner"],
		):
			if not row.attached_to_doctype and not row.attached_to_name:
				if row.owner == user:
					return True
				continue
			if row.attached_to_doctype != self.doctype or row.attached_to_field != "photo_at_gate":
				continue
			attached_to = row.attached_to_name or ""
			if attached_to == self.name or (temporary_name and attached_to == temporary_name):
				return True
			if attached_to.startswith("new-") and row.owner == user:
				return True
		return False

	def _add_missing_declared_items(self, vp):
		"""Every item declared on the pass gets a verification row on its check-in.

		The form builds these rows in the browser and the officer ticks them. A
		row that was removed there used to be simply absent here, and the "every
		item verified" check only looked at the rows it was given — so deleting
		the row of an item that could not be found was a way past it. Declared
		items the posted table does not cover are added back, unverified.
		"""
		covered = {row.visitor_item_row_name for row in (self.items_verification or [])}
		for vi in vp.get("visitor_items") or []:
			if vi.name in covered:
				continue
			self.append(
				"items_verification",
				{
					"visitor_item_row_name": vi.name,
					"item_name": vi.item_name,
					"item_category": vi.item_category,
					# Seed the officer's classification from what the visitor declared.
					# Both fields now share one vocabulary, so this copies straight across.
					# Previously nothing set item_type at all, and its Select had no blank
					# first option, so every row rendered pre-selected on its first choice
					# and saved that way -- 77 of 87 rows read 'Electronic', including
					# fabric samples and a measuring tape.
					"item_type": vi.item_category,
					"quantity_declared": vi.quantity,
					"uom": vi.unit_of_measure,
					"serial__asset_number": vi.serial_number,
					"quantity_found": vi.quantity,
					"item_verified": 0,
					# item_image is captured by the gate officer at scan time, not copied from Visitor Item.
				},
			)

	# --------------------------------------------------

	def after_insert(self):
		if not self.visitor_pass:
			return

		if self.event_type == "Check-In":
			self._issue_badge_at_gate()
			self._sync_gate_verification()
			self._sync_item_verification()
			self._keep_issued_badge_number()
			# Also update the Pass status to Checked-In
			self._advance_pass(self._checkin_times())
			self._notify_host_arrival()

		elif self.event_type == "Check-Out":
			self._advance_pass(
				{
					"status": "Checked-Out",
					"actual_checkout": self.check_out_date_time or now_datetime(),
				}
			)

		self._record_lifecycle_event()
		sync_contact_trace(self.visitor_pass, self)

	# --------------------------------------------------

	def on_update(self):
		adopt_stray_uploads(self)
		if self.event_type == "Check-In" and self.visitor_pass:
			# `photo_at_gate` used to fall back to the visitor's own
			# pre-registration photo whenever the officer had not captured one.
			# That photo was then stamped onto the pass as `gate_verified_photo`
			# with a time and an officer's name, so the record asserted a gate
			# verification that never happened — and the "does the visitor match
			# their pass photo?" check compared an image against itself, which it
			# can never fail. A gate photo now exists only if somebody took one;
			# the badge already falls back to the pass photo for display, and its
			# "gate verified" marker is keyed on this field, so it now means what
			# it says.
			self._sync_gate_verification()
			self._sync_item_verification()

		if self.visitor_pass:
			self._record_lifecycle_event()
			sync_contact_trace(self.visitor_pass, self)

	# --------------------------------------------------

	def _sync_gate_verification(self):
		if not self.visitor_pass or not self.photo_at_gate:
			return

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

		frappe.db.set_value("Visitor Pass", self.visitor_pass, values)

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

		# Update Visitor Item rows
		for row in self.items_verification or []:
			if row.visitor_item_row_name:
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
				_("All items verified! Badge issued: {0}").format(vp.badge_number),
				alert=True,
				indicator="green",
			)

	def _issue_badge_at_gate(self):
		"""Mint the badge for a Visitor Type that issues it at the gate.

		Done here, once the check-in exists. It used to happen while the log was
		still being validated, and minting also moves the pass to "Items
		Verified" — so a check-in that was then refused had already changed the
		pass.
		"""
		vp = frappe.get_doc("Visitor Pass", self.visitor_pass)
		if vp.badge_number:
			return
		if frappe.db.get_value("Visitor Type", vp.visitor_type, "issue_badge_at_gate"):
			vp.generate_badge_number()

	def _keep_issued_badge_number(self):
		"""Copy a badge minted during this check-in onto the log.

		A pass can reach the gate without a badge: its type issues one at the
		gate, or it was approved before badges were switched on and gets one when
		its items are verified here (`_sync_item_verification`). The form used to
		mint it the moment the pass was picked, before anything was recorded; now
		it is minted with the event, and the log records which badge was handed
		over.
		"""
		if self.badge_number:
			return
		badge = frappe.db.get_value("Visitor Pass", self.visitor_pass, "badge_number")
		if badge:
			self.db_set("badge_number", badge, update_modified=False)

	def _assert_items_verified(self, movement, visitor_pass=None):
		"""Refuse a gate event while a declared item is still unverified.

		Declaring items exists to answer one question — did what came in also go
		out. The system already computed the answer (`all_items_confirmed`, and
		`item_verification_status` left at "Pending") and simply never acted on
		it, so a visitor could be checked in and back out with tools nobody
		looked at, and the record only said so afterwards.

		`block_check_in_without_verification` is not this check despite the
		name — it gates `id_proof_match` and `pass_photo_match`, which are
		identity, not items. There was no item equivalent at all.

		The two events have to be checked differently. Check-In owns the
		verification rows: they are built on this very save from the pass's
		declared items, so the rows are the source of truth. Check-Out has no
		rows of its own — nothing populates `items_verification` for it — so
		asking the log would always find nothing. It has to ask the pass, which
		carries the outcome of the check-in verification.

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

		if self.event_type == "Check-In":
			outstanding = [
				(row.item_name or _("Item"))
				for row in (self.items_verification or [])
				if not row.item_verified
			]
		else:
			# Ask the pass: were the declared items ever confirmed at entry?
			if not visitor_pass:
				return
			outstanding = [
				(item.item_name or _("Item"))
				for item in (visitor_pass.get("visitor_items") or [])
				if not item.get("verified_by_security")
			]

		if not outstanding:
			return

		frappe.throw(
			_(
				"Verify every declared item before saving the visitor {0}. Still "
				"unverified: {1}. Tick <b>Item Verified</b> on each row of Items "
				"Verification."
			).format(movement, ", ".join(frappe.utils.escape_html(name) for name in outstanding)),
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

		log_visitor_event(
			self.visitor_pass,
			self.event_type,
			event_status="Recorded",
			source_doctype=self.doctype,
			source_name=self.name,
			details={
				"gate_name": self.gate_name,
				"visited_area": self.visited_area,
				"security_officer": self.security_officer,
				"exception_reason": self.exception_reason,
			},
		)


def gate_policy():
	"""What this site requires before a Check-In / Check-Out may be recorded."""
	return {
		"qr_scan_required": bool(vms_settings.flag("qr_scan_required_at_gate")),
		"photo_required": bool(vms_settings.flag("require_visitor_photo")),
		"identity_match_required": bool(vms_settings.flag("block_check_in_without_verification")),
		"items_verification_required": not vms_settings.flag("allow_gate_without_item_verification"),
		"badge_enabled": bool(vms_settings.flag("enable_badge", 1)),
	}


@frappe.whitelist()
def get_gate_policy():
	"""What this site actually requires before a gate event may be saved.

	The badge checklist used to hardcode its own list, which demanded a photo and
	both identity confirmations regardless of configuration. On a site that
	requires none of them the officer could save the check-in and then be told to
	go back and complete steps — on a record that locks itself on save, so the
	instruction could never be carried out.

	The form reads this once and uses it for its required marks and for the
	checklist shown before a gate event is recorded. The server enforces the
	same switches on insert (SecurityLog._assert_gate_requirements).
	"""
	frappe.has_permission("Security Log", "read", throw=True)
	return gate_policy()


@frappe.whitelist()
def get_approved_vip_queue(visit_date: str | None = None):
	frappe.has_permission("Security Log", "read", throw=True)
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
			# The host by name; the Employee ID means nothing to the officer.
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
