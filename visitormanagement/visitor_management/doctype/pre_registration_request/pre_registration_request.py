# Copyright (c) 2026, Harthesh and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from visitormanagement.visitor_management.lifecycle import apply_hospitality_meal_plan


PENDING_APPROVAL_BY_TYPE = {
	"Contractor": "Pending System Manager",
	"Supplier": "Pending System Manager",
	"Candidate": "Pending HR Manager",
	"Customer": "Pending Sales Manager",
	"VIP": "Pending HOD",
}


def get_pending_approval_state(visitor_type):
	return PENDING_APPROVAL_BY_TYPE.get(visitor_type, "Pending System Manager")


class PreRegistrationRequest(Document):
	def validate(self):
		if not self.status:
			self.status = "Draft"

		# Invitation-backed drafts should remain drafts until the visitor submits.
		if self.is_new() and self.request_channel == "Portal" and self.status == "Draft" and not self.visitor_invitation:
			self.status = get_pending_approval_state(self.visitor_type)

		if self.visitor_type == "Supplier" and not self.supplier_visit_mode:
			self.supplier_visit_mode = "Meeting"

		preserve_hospitality_choices = bool(
			self.request_channel == "Portal" or self.visitor_invitation
		)
		apply_hospitality_meal_plan(self, preserve_existing=preserve_hospitality_choices)

	@frappe.whitelist()
	def create_visitor_pass(self):
		if self.visitor_pass:
			return self.visitor_pass

		if self.status not in {"Approved", "Converted"}:
			frappe.throw("Approve the pre-registration request before creating a Visitor Pass.")

		if not self.id_proof_scan or not self.visitor_photo:
			frappe.throw("ID proof scan and visitor photo are required before conversion.")

		visitor_pass = frappe.get_doc(
			{
				"doctype": "Visitor Pass",
				"visitor_type": self.visitor_type,
				"visitor_full_name": self.visitor_name,
				"mobile_number": self.mobile_number,
				"email_id": self.email_id,
				"company__organisation": self.company__organisation,
				"id_proof_type": self.id_proof_type,
				"id_proof_number": self.id_proof_number,
				"id_proof_scan": self.id_proof_scan,
				"visitor_photo": self.visitor_photo,
				"purpose_of_visit": self.purpose_of_visit,
				"person_to_visit": self.person_to_visit,
				"visit_date": self.visit_date,
				"expected_checkin": self.expected_checkin,
				"expected_checkout": self.expected_checkout,
				"request_channel": self.request_channel or "Portal",
				"supplier_visit_mode": self.supplier_visit_mode,
				"meeting_subject": self.meeting_subject,
				"purchase_order": self.purchase_order,
				"delivery_note": self.delivery_note,
				"goods_description": self.goods_description,
				"meal_required": self.meal_required,
				"meal_type": self.meal_type,
				"assigned_meal_slots": self.assigned_meal_slots,
				"hospitality_type": self.hospitality_type,
				"special_diet": self.special_diet,
				"service_time": self.service_time,
				"refreshments_required": self.refreshments_required,
				"conference_room": self.conference_room,
				"items_carried": self.items_carried,
				"pre_registration_request": self.name,
			}
		)
		for item in self.get("visitor_items") or []:
			visitor_pass.append(
				"visitor_items",
				{
					"item_code": item.item_code,
					"item_name": item.item_name,
					"item_category": item.item_category,
					"quantity": item.quantity,
					"unit_of_measure": item.unit_of_measure,
					"description": item.description,
					"is_new_item": item.is_new_item,
					"serial_number": item.serial_number,
					"estimated_value": item.estimated_value,
					"verification_remarks": item.verification_remarks,
				},
			)
		visitor_pass.insert(ignore_permissions=True)

		self.db_set(
			{
				"visitor_pass": visitor_pass.name,
				"status": "Converted",
			}
		)
		return visitor_pass.name


def sync_invitation_context(doc, method=None):
	if not doc.visitor_invitation:
		return

	if not (doc.is_new() or frappe.flags.in_web_form):
		return

	invitation = frappe.get_doc("Visitor Invitation", doc.visitor_invitation)
	if invitation.invitation_status == "Expired":
		frappe.throw("This invitation has expired.")

	if invitation.pre_registration_request and invitation.pre_registration_request != doc.name:
		frappe.throw("This invitation has already been used for another pre-registration request.")

	doc.request_channel = "Portal"
	doc.status = "Draft"
	doc.visitor_type = invitation.visitor_type
	doc.email_id = invitation.visitor_email
	doc.visit_date = invitation.visit_date
	doc.expected_checkin = invitation.expected_checkin
	doc.expected_checkout = invitation.expected_checkout
	doc.person_to_visit = invitation.host_employee
	doc.purpose_of_visit = invitation.purpose_of_visit
