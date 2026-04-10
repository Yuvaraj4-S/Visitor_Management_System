# Copyright (c) 2026, Harthesh and contributors
# For license information, please see license.txt

import secrets

import frappe
from frappe.model.document import Document
from frappe.utils import add_days, get_datetime, get_time, get_url, now_datetime

from visitormanagement.visitor_management.lifecycle import derive_hospitality_meal_plan


INVITATION_EXPIRY_DAYS = 7


def _coerce_datetime(value):
	if not value:
		return None

	return get_datetime(value)


def build_invitation_link(token):
	return get_url(f"/visitor-pre-registration-form/new?token={token}")


def get_valid_invitation_by_token(token):
	token = (token or "").strip()
	if not token:
		return None

	name = frappe.db.get_value("Visitor Invitation", {"invitation_token": token}, "name")
	if not name:
		return None

	doc = frappe.get_doc("Visitor Invitation", name)
	if doc.invitation_status in {"Submitted", "Expired"}:
		return None

	expires_on = _coerce_datetime(doc.invitation_expires_on)
	if expires_on and expires_on < now_datetime():
		doc.db_set("invitation_status", "Expired", update_modified=False)
		return None

	return doc


def _format_time_for_web_form(value):
	if not value:
		return ""

	return get_time(value).strftime("%H:%M")


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

	return {
		"valid": True,
		"invitation": invitation.name,
		"values": {
			"visitor_invitation": invitation.name,
			"visitor_type": invitation.visitor_type,
			"email_id": invitation.visitor_email,
			"visit_date": str(invitation.visit_date) if invitation.visit_date else "",
			"expected_checkin": _format_time_for_web_form(invitation.expected_checkin),
			"expected_checkout": _format_time_for_web_form(invitation.expected_checkout),
			"person_to_visit": invitation.host_employee,
			"purpose_of_visit": invitation.purpose_of_visit,
			"meal_required": invitation.meal_required,
			"meal_type": meal_plan["meal_type"] if invitation.meal_required else "",
			"assigned_meal_slots": meal_plan["assigned_meal_slots"] if invitation.meal_required else "",
			"hospitality_type": meal_plan["hospitality_type"] if invitation.meal_required else "",
			"refreshments_required": invitation.refreshments_required,
			"conference_room": invitation.conference_room,
		},
	}


class VisitorInvitation(Document):
	def validate(self):
		if not self.created_by_user:
			self.created_by_user = frappe.session.user

		if not self.invitation_status:
			self.invitation_status = "Draft"

		if not self.invitation_expires_on and self.visit_date:
			self.invitation_expires_on = get_datetime(f"{self.visit_date} 23:59:59")

		self.invitation_expires_on = _coerce_datetime(self.invitation_expires_on)

		if self.invitation_expires_on and self.invitation_expires_on < now_datetime():
			frappe.throw("Invitation Expiry must be a future date and time.")

		self._apply_hospitality_defaults()

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

		token = secrets.token_urlsafe(24)
		sent_on = now_datetime()
		expires_on = _coerce_datetime(self.invitation_expires_on) or add_days(
			sent_on, INVITATION_EXPIRY_DAYS
		)

		if expires_on < sent_on:
			frappe.throw("Invitation Expiry must be later than the send time.")

		link = build_invitation_link(token)

		self.db_set(
			{
				"invitation_token": token,
				"invitation_sent_on": sent_on,
				"invitation_expires_on": expires_on,
				"portal_submission_url": link,
				"invitation_status": "Sent",
			}
		)

		message = [
			"Dear Visitor,",
			"",
			"You have received a visitor pre-registration invitation from our company.",
			f"Visitor Type: {self.visitor_type or '-'}",
			f"Host: {self.host_employee or '-'}",
			f"Visit Date: {self.visit_date or '-'}",
			f"Expected Check-In: {self.expected_checkin or '-'}",
			f"Expected Check-Out: {self.expected_checkout or '-'}",
			f"Purpose: {self.purpose_of_visit or '-'}",
			f"Meal Required: {'Yes' if self.meal_required else 'No'}",
			f"Meal Type: {self.meal_type or '-'}",
			f"Refreshments Required: {'Yes' if self.refreshments_required else 'No'}",
			f"Conference Room: {self.conference_room or '-'}",
			"",
			"Please use the secure link below to fill your information before arrival:",
			f"<a href=\"{link}\">{link}</a>",
			"",
			f"This invitation expires on {expires_on}.",
		]
		frappe.sendmail(
			recipients=[self.visitor_email],
			subject="Visitor Pre-Registration Invitation",
			message="<br>".join(message),
			now=True,
		)
		return link
