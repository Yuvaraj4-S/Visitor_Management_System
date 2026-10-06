# For license information, please see license.txt

import contextlib
import functools
import re
import secrets

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.rate_limiter import rate_limit
from frappe.utils import (
	add_days,
	date_diff,
	format_date,
	format_datetime,
	format_time,
	get_datetime,
	get_time,
	get_url,
	getdate,
	now_datetime,
	today,
)

from visitormanagement.visitor_management import settings as vms_settings
from visitormanagement.visitor_management.id_masking import mask_id
from visitormanagement.visitor_management.lifecycle import derive_hospitality_meal_plan, log_failure
from visitormanagement.visitor_management.link_details import fill_from_link
from visitormanagement.visitor_management.mail import send_checked
from visitormanagement.visitor_management.portal_upload import (
	count_event,
	events_counted,
	rate_limit_identity,
)

# An invitation in one of these can still be used; any other status is final.
OPEN_STATUSES = ("Draft", "Sent", "Opened", "Saved")

# Wrong tokens one caller may present per hour before every token it presents is
# treated as wrong. Only misses are counted, so a visitor opening their own link
# is never slowed down, however often. See lookup_invitation.
TOKEN_MISSES_PER_HOUR = 20


def _coerce_datetime(value):
	if not value:
		return None

	return get_datetime(value)


def build_invitation_link(token):
	"""Absolute link to the visitor's pre-registration form.

	`frappe.utils.get_url` uses `host_name` from the site config when it is set,
	and otherwise the request's host with the scheme the proxy reported
	(X-Forwarded-Proto). This used to be built from `request.host_url`, which
	behind a TLS-terminating proxy on another host came out as `http://` — on a
	link that is a bearer credential and is sent by email.
	"""
	return get_url(f"/visitor-pre-registration-form/new?token={token}")


def _is_past(value):
	value = _coerce_datetime(value)
	return bool(value and value < now_datetime())


def _keep(doc, values):
	"""Write tracking fields so that they survive the request they happen in.

	The invitation link is opened with a GET, and Frappe rolls back whatever a
	GET wrote (frappe/app.py sync_database) unless the request says it has
	something to keep — the same flag core sets when it records that a document
	was seen (Document.add_seen). Without it "Opened" and "Expired" were written
	and silently undone on every page view.
	"""
	doc.db_set(values, update_modified=False)
	frappe.local.flags.commit = True


def get_valid_invitation_by_token(token):
	token = (token or "").strip()
	if not token:
		return None

	name = frappe.db.get_value("Visitor Invitation", {"invitation_token": token}, "name")
	if not name:
		return None

	doc = frappe.get_doc("Visitor Invitation", name)
	# "Cancelled" is a revocation: a host who mailed the link to the wrong address
	# needs it dead now, not at natural expiry. The status option is useless
	# unless the token check honours it — without this line the invitation would
	# read "Cancelled" in the list while its link kept working.
	if doc.invitation_status in {"Submitted", "Expired", "Cancelled"}:
		return None

	if _is_past(doc.invitation_expires_on):
		# The scheduler does this for every invitation (expire_due_invitations);
		# here it is only brought forward for the one somebody just tried.
		_keep(doc, {"invitation_status": "Expired"})
		return None

	return doc


def _token_miss_key():
	return f"vms:portal-token-miss:{rate_limit_identity()}"


def lookup_invitation(token):
	"""`get_valid_invitation_by_token` for callers on the public side.

	The token is a 192-bit bearer secret, so guessing one is not a realistic
	attack; this makes it a bounded one as well. Frappe's `@rate_limit` on the
	endpoint keys on a header the client may control and is not applied to page
	views at all, so wrong tokens are counted here, per caller (see
	portal_upload.rate_limit_identity). Past the limit every token from that
	caller is answered as "not valid" — the same answer as a wrong one.

	A counter that cannot be reached (Redis down) must not take the form down
	with it: the lookup then simply proceeds.
	"""
	token = (token or "").strip()
	if not token:
		return None

	limited = bool(frappe.request)
	if limited:
		try:
			if events_counted(_token_miss_key()) >= TOKEN_MISSES_PER_HOUR:
				return None
		except Exception:
			limited = False

	invitation = get_valid_invitation_by_token(token)
	if not invitation and limited:
		with contextlib.suppress(Exception):
			count_event(_token_miss_key())
	return invitation


def expire_due_invitations():
	"""Mark every invitation whose expiry has passed as Expired. Hourly job.

	Expiry is a fact about time, so it cannot depend on somebody opening the
	link: an invitation nobody used stayed "Sent" for ever, read as live in the
	list and in the invitation funnel, and was never picked up by the retention
	purge, which only takes invitations in a final status.

	Safe to run at any time and any number of times. Returns the number changed.
	"""
	names = frappe.get_all(
		"Visitor Invitation",
		filters={
			"invitation_status": ("in", OPEN_STATUSES),
			"invitation_expires_on": ("<", now_datetime()),
		},
		pluck="name",
	)
	for name in names:
		frappe.db.set_value("Visitor Invitation", name, "invitation_status", "Expired", update_modified=False)
	return len(names)


def _format_time_for_web_form(value):
	if not value:
		return ""

	return get_time(value).strftime("%H:%M:%S")


def _format_datetime_for_web_form(value):
	if not value:
		return ""

	return get_datetime(value).strftime("%Y-%m-%d %H:%M:%S")


def host_display_name(employee):
	"""The host's name as a visitor may see it — never the Employee ID."""
	if not employee:
		return ""
	return frappe.db.get_value("Employee", employee, "employee_name") or ""


# nosemgrep: guest-whitelisted-method - invitation link lookup; rate-limited, token is 192-bit
@frappe.whitelist(allow_guest=True)
# `token` is looked up directly against the database, and the caller is
# anonymous, so this endpoint is as much a token-guessing oracle as it is a
# page-load helper. Two limits apply: Frappe's per-address ceiling on calls
# below (30/hour, comfortably above a real visitor reopening their own link),
# and the wrong-token counter inside lookup_invitation, which also covers the
# page render (the web form calls this function unwrapped) and does not depend
# on a header the caller can set.
@rate_limit(limit=30, seconds=60 * 60)
def get_web_form_context(token: str | None):
	invitation = lookup_invitation(token)
	if not invitation:
		return {
			"valid": False,
			"message": _("This invitation link is invalid, expired, or already used."),
		}

	if invitation.invitation_status == "Sent":
		_keep(
			invitation,
			{
				"invitation_status": "Opened",
				"link_opened_on": now_datetime(),
			},
		)

	meal_plan = derive_hospitality_meal_plan(invitation)
	existing_pass = None
	if invitation.visitor_pass and frappe.db.exists("Visitor Pass", invitation.visitor_pass):
		existing_pass = frappe.get_doc("Visitor Pass", invitation.visitor_pass)

	# What goes back is read by whoever holds the link, in their browser. The
	# host is named, never identified: the Employee ID stays on the server
	# (portal.submit_pre_registration takes the host from the invitation, not
	# from the form), and "HR-EMP-00057" told the visitor nothing anyway.
	host_name = host_display_name(invitation.host_employee)
	values = {
		"entry_type": "New",
		"request_channel": "Portal",
		"visitor_invitation": invitation.name,
		"visitor_type": invitation.visitor_type,
		"email_id": invitation.visitor_email,
		"mobile_number": invitation.get("visitor_mobile") or "",
		"visitor_full_name": invitation.get("visitor_full_name") or "",
		"visit_date": str(invitation.visit_date) if invitation.visit_date else "",
		"expected_checkin": _format_time_for_web_form(invitation.expected_checkin),
		"expected_checkout": _format_time_for_web_form(invitation.expected_checkout),
		"person_to_visit": host_name,
		"person_to_visit_display": host_name,
		"purpose_of_visit": invitation.purpose_of_visit,
		"meal_required": invitation.meal_required,
		"meal_type": meal_plan["meal_type"] if invitation.meal_required else "",
		"assigned_meal_slots": meal_plan["assigned_meal_slots"] if invitation.meal_required else "",
		"hospitality_type": meal_plan["hospitality_type"] if invitation.meal_required else "",
		"service_time": _format_datetime_for_web_form(meal_plan["service_time"])
		if invitation.meal_required
		else "",
		"refreshments_required": invitation.refreshments_required,
		"conference_room": invitation.get("conference_room") or "",
	}

	if existing_pass:
		values.update(
			{
				"visitor_full_name": existing_pass.visitor_full_name,
				"mobile_number": existing_pass.mobile_number,
				"company__organisation": existing_pass.company__organisation,
				"custom_nationality": existing_pass.custom_nationality,
				# The visitor's own draft, re-opened with their link. The ID number
				# they typed is never sent back: only the masked form, to show
				# which document is on file. The same for the files — whether one
				# is there, not where it is.
				"id_proof_type": existing_pass.id_proof_type,
				"id_proof_number_masked": existing_pass.get("id_proof_number_masked")
				or mask_id(existing_pass.id_proof_type, existing_pass.get("id_proof_number")),
				"id_proof_scan_on_file": bool(existing_pass.id_proof_scan),
				"visitor_photo_on_file": bool(existing_pass.visitor_photo),
				"custom_visa_copy_on_file": bool(existing_pass.custom_visa_copy),
				# NOTE: only fields the portal form actually shows are echoed
				# back. This response goes to whoever holds the invitation token,
				# so internal values staff added to the draft pass —
				# supplier_link / contractor_link / job_applicant_link,
				# work_order_ref, products_discussed, meeting_outcome,
				# followup_date, mdceo_notified, and the staff-set interview_panel /
				# vip_category / protocol_notes (escort and security instructions;
				# portal._build_visitor_pass_values refuses them from the visitor
				# too) — are deliberately left out. They are staff notes and
				# internal record IDs; the visitor has no reason to see them.
				"supplier_visit_mode": existing_pass.supplier_visit_mode,
				"visit_category": existing_pass.visit_category,
				"tools_list": existing_pass.tools_list,
				"multi_day_pass": existing_pass.multi_day_pass,
				"pass_valid_until": str(existing_pass.pass_valid_until)
				if existing_pass.pass_valid_until
				else "",
				"position_applied": existing_pass.position_applied,
				"candidate_interview_type": existing_pass.candidate_interview_type,
				"interpreter_required": existing_pass.interpreter_required,
				"interpreter_language": existing_pass.interpreter_language,
				"meal_required": existing_pass.meal_required,
				"meal_type": existing_pass.meal_type or values.get("meal_type"),
				"assigned_meal_slots": existing_pass.assigned_meal_slots or values.get("assigned_meal_slots"),
				"hospitality_type": existing_pass.hospitality_type or values.get("hospitality_type"),
				"special_diet": existing_pass.special_diet,
				"service_time": _format_datetime_for_web_form(existing_pass.service_time)
				if existing_pass.service_time
				else values.get("service_time"),
				"refreshments_required": existing_pass.refreshments_required,
				"items_carried": existing_pass.items_carried,
				"visitor_items": [
					{
						"item_code": row.item_code,
						"item_name": row.item_name,
						"item_category": row.item_category,
						"quantity": row.quantity,
						"unit_of_measure": row.unit_of_measure,
						"description": row.description,
						"is_new_item": row.is_new_item,
						"serial_number": row.serial_number,
						"estimated_value": row.estimated_value,
					}
					for row in (existing_pass.get("visitor_items") or [])
				],
			}
		)

	return {
		"valid": True,
		"invitation": invitation.name,
		"values": values,
	}


def _report_undelivered_invitation(invitation, visitor_email, user, error):
	"""The host was told the invitation went; the send after commit then failed.

	Left alone they would wait for a visitor who never got the link. Say so on the
	invitation's timeline and in the sender's notifications. The mail stays queued
	and is retried automatically; the link can be copied from the invitation.
	"""
	# `error` is the mail server's own last line. It is in the Error Log and on the
	# Email Queue row for whoever administers the mail account; the host gets
	# words they can act on.
	message = _(
		"The invitation email to {0} has not been delivered (the mail server did not accept it). It "
		"will be retried automatically — to be sure the visitor gets it, use Copy Invitation Link and "
		"send it directly."
	).format(frappe.utils.escape_html(visitor_email or ""))
	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Info",
			"reference_doctype": "Visitor Invitation",
			"reference_name": invitation,
			"content": message,
		}
	).insert(ignore_permissions=True)
	if user and user != "Guest":
		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"for_user": user,
				"type": "Alert",
				"document_type": "Visitor Invitation",
				"document_name": invitation,
				"subject": message,
			}
		).insert(ignore_permissions=True)


def _delivery_problem(exc):
	"""Why the invitation mail did not go, in words a host can act on.

	The raw text ("Please setup default outgoing Email Account from Settings >
	Email Account", or an SMTP server's reply with its host name in it) is for
	the administrator and is in the Error Log. A host cannot open Email Account
	and should not be shown the mail server's address.
	"""
	if isinstance(exc, frappe.OutgoingEmailError):
		return _(
			"This site does not have an outgoing email account yet. Ask your system "
			"administrator to set one up."
		)
	return _(
		"The mail server could not be reached or did not accept the message. Ask your system "
		"administrator to check the outgoing email account."
	)


class VisitorInvitation(Document):
	def onload(self):
		# Default Host Employee to the current user's Employee record (if any)
		if self.is_new() and not self.host_employee and frappe.session.user not in ("Administrator", "Guest"):
			emp = frappe.db.get_value(
				"Employee",
				{"user_id": frappe.session.user, "status": "Active"},
				"name",
			)
			if emp:
				self.host_employee = emp

		# Between the moment an invitation runs out and the hourly job that
		# records it, the form says what is true.
		if (
			not self.is_new()
			and self.invitation_status in OPEN_STATUSES
			and _is_past(self.invitation_expires_on)
		):
			self.invitation_status = "Expired"

	def validate(self):
		if not self.created_by_user:
			self.created_by_user = frappe.session.user

		if not self.invitation_status:
			self.invitation_status = "Draft"

		self._validate_visitor_type()
		self._validate_visit_date()
		self._validate_email()
		self._validate_mobile()
		self._validate_time_range()
		self._validate_host_active()
		# Shown in the list and on the form instead of the Employee ID. Filled
		# here rather than with fetch_from, which reads the Employee with the
		# saver's own rights (see link_details.fill_from_link).
		fill_from_link(self, "host_employee", "Employee", {"host_name": "employee_name"})

		if not self.invitation_expires_on and self.visit_date:
			self.invitation_expires_on = get_datetime(f"{self.visit_date} 23:59:59")

		self.invitation_expires_on = _coerce_datetime(self.invitation_expires_on)
		self._settle_expiry()

		self._apply_hospitality_defaults()

	def _settle_expiry(self):
		"""Keep the status and the expiry date telling the same story.

		An invitation that has run out can be brought back by giving it a later
		expiry: it returns to the stage it had reached, and Resend works again.
		One that is still open cannot be given an expiry in the past. One that is
		finished (Submitted, Cancelled, or Expired and left that way) is history
		and saves as it is — this used to refuse every later save of it.
		"""
		expired = _is_past(self.invitation_expires_on)
		if self.invitation_status == "Expired" and not expired:
			if self.visitor_pass:
				self.invitation_status = "Saved"
			elif self.link_opened_on:
				self.invitation_status = "Opened"
			elif self.invitation_sent_on:
				self.invitation_status = "Sent"
			else:
				self.invitation_status = "Draft"
		elif expired and self.invitation_status in OPEN_STATUSES:
			frappe.throw(_("Invitation Expiry must be a future date and time."))

	def _validate_visitor_type(self):
		"""Reject a Visitor Type that has been deactivated. `is_active` was
		previously never enforced anywhere, so retired types stayed selectable."""
		if not self.visitor_type:
			return
		if not frappe.db.get_value("Visitor Type", self.visitor_type, "is_active"):
			frappe.throw(
				_("Visitor Type {0} is not active.").format(self.visitor_type),
				title=_("Inactive Visitor Type"),
			)

	def _validate_visit_date(self):
		if not self.visit_date:
			return
		today_date = getdate(today())
		visit_date = getdate(self.visit_date)
		if visit_date < today_date:
			frappe.throw(
				_("Visit date {0} is in the past. Cannot send an invitation for a past date.").format(
					self.visit_date
				),
				title=_("Invalid Visit Date"),
			)
		max_days = vms_settings.max_advance_booking_days()
		if max_days and date_diff(visit_date, today_date) > max_days:
			frappe.throw(
				_("Visit date cannot be more than {0} days in the future.").format(max_days),
				title=_("Invalid Visit Date"),
			)

	def _validate_email(self):
		if self.visitor_email and not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", self.visitor_email):
			frappe.throw(
				_("Visitor Email '{0}' is not a valid email address.").format(self.visitor_email),
				title=_("Invalid Email"),
			)

	def _validate_mobile(self):
		"""Check the visitor's number is real before the invitation goes out.

		The number typed here is what reaches the Visitor Pass and, from there,
		the gate. Nothing validated it, so a string of digits could travel the
		whole way and only be discovered when security tried to phone the visitor.
		"""
		from visitormanagement.visitor_management import phone

		self.visitor_mobile = phone.validate_mobile(self.visitor_mobile, _("Visitor Mobile"))

	def _validate_time_range(self):
		if self.expected_checkin and self.expected_checkout:
			if get_time(self.expected_checkin) >= get_time(self.expected_checkout):
				frappe.throw(
					_("Expected Check-In must be before Expected Check-Out."),
					title=_("Invalid Time Range"),
				)

	def _validate_host_active(self):
		if not self.host_employee:
			return
		status = frappe.db.get_value("Employee", self.host_employee, "status")
		if status != "Active":
			frappe.throw(
				_("Host employee {0} is not Active (status: {1}).").format(
					self.host_employee, status or "Unknown"
				),
				title=_("Invalid Host"),
			)

	def _apply_hospitality_defaults(self):
		self.meal_required = frappe.utils.cint(self.meal_required)
		self.refreshments_required = frappe.utils.cint(self.refreshments_required)

		meal_plan = derive_hospitality_meal_plan(self)
		self.meal_type = meal_plan["meal_type"] if self.meal_required else None
		self.assigned_meal_slots = meal_plan["assigned_meal_slots"] if self.meal_required else None
		self.hospitality_type = meal_plan["hospitality_type"] if self.meal_required else None

	@frappe.whitelist()
	def send_invitation(self):
		if self.is_new():
			frappe.throw(_("Save the Visitor Invitation before sending the invitation mail."))

		# run_doc_method builds this doc from the JSON the client posted and only
		# checks read permission. Sending mints a token, writes to the record and
		# emails whatever address it holds, so require write access and work from
		# the saved record — never from client-supplied field values.
		self.check_permission("write")
		self.reload()

		if not self.visitor_email:
			frappe.throw(_("Visitor Email is required before sending invitation."))

		# Reuse a token that is already live. Minting a fresh one on every send
		# silently invalidates the link the visitor may already be holding — and
		# because the record stays Draft when delivery fails, the button keeps
		# reading "Send Invitation", so a second click is the natural thing for a
		# host to do. An expired invitation cannot reach here anyway: the expiry
		# check below throws before any token is used.
		token = self.invitation_token or secrets.token_urlsafe(24)
		sent_on = now_datetime()
		expires_on = _coerce_datetime(self.invitation_expires_on) or add_days(
			sent_on, vms_settings.invitation_expiry_days()
		)

		if expires_on < sent_on:
			frappe.throw(
				_("This invitation has expired. Set a later Invitation Expires On, save, and send it again.")
				if self.invitation_status == "Expired"
				else _("Invitation Expiry must be later than the send time.")
			)

		link = build_invitation_link(token)

		self.db_set(
			{
				"invitation_token": token,
				"invitation_expires_on": expires_on,
				"portal_submission_url": link,
			}
		)

		# Same branding VMS Settings already supplies to the public web form
		# (visitor_pre_registration_form.py: _brand_header / _brand_footer) —
		# the invitation email is the visitor's first touchpoint and should not
		# read as generic where the customer has already configured an identity.
		branding = vms_settings.portal_branding()
		organisation = branding["organisation"] or _("our company")

		# The body is HTML (joined with <br>); every interpolated value is escaped
		# so record fields such as Purpose cannot inject markup into the mail.
		def esc(value):
			return frappe.utils.escape_html(str(value if value not in (None, "") else "-"))

		safe_link = esc(link)
		greeting = (
			_("Dear {0},").format(esc(self.visitor_full_name))
			if self.visitor_full_name
			else _("Dear Visitor,")
		)
		message = [
			greeting,
			"",
			_("You have received a visitor pre-registration invitation from {0}.").format(esc(organisation)),
			_("Visitor Type: {0}").format(esc(self.visitor_type)),
			# The person's name. This line used to print the Employee ID, which is
			# an internal record number and told the visitor nothing.
			_("Host: {0}").format(esc(host_display_name(self.host_employee))),
			_("Visit Date: {0}").format(esc(format_date(self.visit_date))),
			_("Expected Check-In: {0}").format(esc(format_time(self.expected_checkin))),
			_("Expected Check-Out: {0}").format(esc(format_time(self.expected_checkout))),
			_("Purpose: {0}").format(esc(self.purpose_of_visit)),
			"",
			_("Please use the secure link below to fill in your details before you arrive:"),
			f'<a href="{safe_link}">{safe_link}</a>',
			"",
			_("This invitation expires on {0}.").format(esc(format_datetime(expires_on))),
		]
		if branding["footer_note"]:
			message += ["", esc(branding["footer_note"])]
		# A failed send must not roll back the token and the Sent status — the host
		# would be left with no link at all. Mint the link regardless and report
		# delivery separately, so it can still be copied and shared by hand.
		delivered, error = self._deliver_invitation_mail(message)
		if delivered:
			self.db_set({"invitation_sent_on": sent_on, "invitation_status": "Sent"})

		return {"link": link, "delivered": delivered, "error": error}

	def _deliver_invitation_mail(self, message):
		try:
			# Checks the mail server takes our login before telling the host it was
			# sent. `now=True` only sent after commit, so a mail-server failure was
			# never reported here — it surfaced as a 500 after the invitation saved.
			send_checked(
				on_failure=functools.partial(
					_report_undelivered_invitation, self.name, self.visitor_email, frappe.session.user
				),
				recipients=[self.visitor_email],
				reference_doctype=self.doctype,
				reference_name=self.name,
				subject=_("Visitor Pre-Registration Invitation"),
				message="<br>".join(message),
			)
			return True, None
		except Exception as exc:
			log_failure(
				"VMS Invitation Email",
				f"Invitation email failed for {self.name}: {exc}\n\n{frappe.get_traceback()}",
			)
			# sendmail raises via frappe.throw, which also queues its own message
			# for the client. Drop it — catching the exception is only half the
			# job; otherwise the host sees a bare "setup Email Account" popup
			# instead of the link we went to the trouble of minting.
			frappe.clear_messages()
			return False, _delivery_problem(exc)
