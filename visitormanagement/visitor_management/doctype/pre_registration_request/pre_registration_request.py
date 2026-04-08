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

		# Portal submissions should directly enter approval queue.
		if self.is_new() and self.request_channel == "Portal" and self.status == "Draft":
			self.status = get_pending_approval_state(self.visitor_type)

		if self.visitor_type == "Supplier" and not self.supplier_visit_mode:
			self.supplier_visit_mode = "Meeting"

		apply_hospitality_meal_plan(self)

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
				"refreshments_required": self.refreshments_required,
				"conference_room": self.conference_room,
				"pre_registration_request": self.name,
			}
		)
		visitor_pass.insert(ignore_permissions=True)

		self.db_set(
			{
				"visitor_pass": visitor_pass.name,
				"status": "Converted",
			}
		)
		return visitor_pass.name
