# For license information, please see license.txt

import secrets

import re
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, get_datetime, get_time, get_url, now_datetime, getdate, today, date_diff

from visitormanagement.visitor_management import settings as vms_settings
from visitormanagement.visitor_management.lifecycle import derive_hospitality_meal_plan


def _coerce_datetime(value):
	if not value:
		return None

	return get_datetime(value)


def build_invitation_link(token):
	"""Build invitation URL using the current request's host so the link works
	dynamically across localhost / ngrok / production domains — no need to
	hard-code `host_name` in site_config.
	"""
	path = f"/visitor-pre-registration-form/new?token={token}"
	base = None
	# Prefer current request's host (request-context send)
	try:
		req = getattr(frappe.local, "request", None)
		if req is not None and getattr(req, "host_url", None):
			base = req.host_url.rstrip("/")
	except Exception:
		base = None
	# Fallback to Frappe's get_url (respects host_name, else infers)
	if not base:
		return get_url(path)
	return f"{base}{path}"


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

	expires_on = _coerce_datetime(doc.invitation_expires_on)
	if expires_on and expires_on < now_datetime():
		doc.db_set("invitation_status", "Expired", update_modified=False)
		return None

	return doc


def _format_time_for_web_form(value):
	if not value:
		return ""

	return get_time(value).strftime("%H:%M:%S")


def _format_datetime_for_web_form(value):
	if not value:
		return ""

	return get_datetime(value).strftime("%Y-%m-%d %H:%M:%S")


@frappe.whitelist(allow_guest=True)
def get_web_form_context(token):
	invitation = get_valid_invitation_by_token(token)
	if not invitation:
		return {
			"valid": False,
			"message": "This invitation link is invalid, expired, or already used.",
		}

	if invitation.invitation_status == "Sent":
		invitation.db_set(
			{
				"invitation_status": "Opened",
				"link_opened_on": now_datetime(),
			},
			update_modified=False,
		)

	meal_plan = derive_hospitality_meal_plan(invitation)
	existing_pass = None
	if invitation.visitor_pass and frappe.db.exists("Visitor Pass", invitation.visitor_pass):
		existing_pass = frappe.get_doc("Visitor Pass", invitation.visitor_pass)

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
		"person_to_visit": invitation.host_employee,
		"purpose_of_visit": invitation.purpose_of_visit,
		"meal_required": invitation.meal_required,
		"meal_type": meal_plan["meal_type"] if invitation.meal_required else "",
		"assigned_meal_slots": meal_plan["assigned_meal_slots"] if invitation.meal_required else "",
		"hospitality_type": meal_plan["hospitality_type"] if invitation.meal_required else "",
		"service_time": _format_datetime_for_web_form(meal_plan["service_time"]) if invitation.meal_required else "",
		"refreshments_required": invitation.refreshments_required,
		"conference_room": invitation.get("conference_room") or "",
	}

	if existing_pass:
		values.update(
			{
				"visitor_full_name": existing_pass.visitor_full_name,
				"mobile_number": existing_pass.mobile_number,
				"company__organisation": existing_pass.company__organisation,
				# NOTE: only fields the portal form actually shows are echoed
				# back. This response goes to whoever holds the invitation token,
				# so internal values staff added to the draft pass —
				# supplier_link / contractor_link / job_applicant_link,
				# work_order_ref, products_discussed, meeting_outcome,
				# followup_date, mdceo_notified — are deliberately left out.
				# They are staff notes and internal record IDs; the visitor has
				# no reason to see them and no way to have entered them.
				"supplier_visit_mode": existing_pass.supplier_visit_mode,
				"visit_category": existing_pass.visit_category,
				"tools_list": existing_pass.tools_list,
				"multi_day_pass": existing_pass.multi_day_pass,
				"pass_valid_until": str(existing_pass.pass_valid_until) if existing_pass.pass_valid_until else "",
				"position_applied": existing_pass.position_applied,
				"candidate_interview_type": existing_pass.candidate_interview_type,
				"interview_panel": existing_pass.interview_panel,
				"vip_category": existing_pass.vip_category,
				"interpreter_required": existing_pass.interpreter_required,
				"interpreter_language": existing_pass.interpreter_language,
				"protocol_notes": existing_pass.protocol_notes,
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

		if not self.invitation_expires_on and self.visit_date:
			self.invitation_expires_on = get_datetime(f"{self.visit_date} 23:59:59")

		self.invitation_expires_on = _coerce_datetime(self.invitation_expires_on)

		if self.invitation_expires_on and self.invitation_expires_on < now_datetime():
			frappe.throw(_("Invitation Expiry must be a future date and time."))

		self._apply_hospitality_defaults()

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
				_("Visit date {0} is in the past. Cannot send an invitation for a past date.").format(self.visit_date),
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
				_("Host employee {0} is not Active (status: {1}).").format(self.host_employee, status or "Unknown"),
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
			frappe.throw("Save the Visitor Invitation before sending the invitation mail.")

		if not self.visitor_email:
			frappe.throw("Visitor Email is required before sending invitation.")

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
			frappe.throw("Invitation Expiry must be later than the send time.")

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
		organisation = branding["organisation"] or "our company"

		message = [
			"Dear Visitor,",
			"",
			f"You have received a visitor pre-registration invitation from {organisation}.",
			f"Visitor Type: {self.visitor_type or '-'}",
			f"Host: {self.host_employee or '-'}",
			f"Visit Date: {self.visit_date or '-'}",
			f"Expected Check-In: {self.expected_checkin or '-'}",
			f"Expected Check-Out: {self.expected_checkout or '-'}",
			f"Purpose: {self.purpose_of_visit or '-'}",
			"",
			"Please use the secure link below to fill your information before arrival:",
			f"<a href=\"{link}\">{link}</a>",
			"",
			f"This invitation expires on {expires_on}.",
		]
		if branding["footer_note"]:
			message += ["", branding["footer_note"]]
		# `now=True` raises on a site with no outgoing Email Account, and that
		# rollback would take the token and the Sent status with it — leaving the
		# host with no link at all. Mint the link regardless and report delivery
		# separately, so it can still be copied and shared by hand.
		delivered, error = self._deliver_invitation_mail(message)
		if delivered:
			self.db_set({"invitation_sent_on": sent_on, "invitation_status": "Sent"})

		return {"link": link, "delivered": delivered, "error": error}

	def _deliver_invitation_mail(self, message):
		try:
			frappe.sendmail(
				recipients=[self.visitor_email],
				reference_doctype=self.doctype,
				reference_name=self.name,
				subject="Visitor Pre-Registration Invitation",
				message="<br>".join(message),
				now=True,
			)
			return True, None
		except Exception as exc:
			frappe.log_error(
				f"Invitation email failed for {self.name}: {exc}", "VMS Invitation Email"
			)
			# sendmail raises via frappe.throw, which also queues its own message
			# for the client. Drop it — catching the exception is only half the
			# job; otherwise the host sees a bare "setup Email Account" popup
			# instead of the link we went to the trouble of minting.
			frappe.clear_messages()
			return False, str(exc)
