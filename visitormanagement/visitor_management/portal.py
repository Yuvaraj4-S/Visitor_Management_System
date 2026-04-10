import base64
import json

import frappe
from frappe.utils import now_datetime

from visitormanagement.visitor_management.doctype.pre_registration_request.pre_registration_request import (
	get_pending_approval_state,
)
from visitormanagement.visitor_management.doctype.visitor_invitation.visitor_invitation import (
	get_valid_invitation_by_token,
)


def _extract_file_payload(payload, fallback_filename=None):
	if not payload:
		return fallback_filename, None

	filename = fallback_filename
	data = payload

	if "," in payload:
		prefix, remainder = payload.split(",", 1)
		if remainder.startswith("data:"):
			filename = prefix or fallback_filename
			data = remainder
		elif prefix.startswith("data:"):
			data = remainder
		else:
			data = remainder

	if "," in data and data.split(",", 1)[0].startswith("data:"):
		data = data.split(",", 1)[1]

	return filename, base64.b64decode(data)


def _attach_file(doctype, docname, fieldname, filename, content):
	if not content:
		return None

	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": filename,
			"attached_to_doctype": doctype,
			"attached_to_name": docname,
			"attached_to_field": fieldname,
			"is_private": 0,
			"content": content,
		}
	)
	file_doc.insert(ignore_permissions=True)
	return file_doc.file_url


def _store_file(filename, content):
	if not content:
		return None

	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": filename,
			"is_private": 0,
			"content": content,
		}
	)
	file_doc.insert(ignore_permissions=True)
	return file_doc.file_url


def _normalize_mobile_number(number, country_code=None):
	number = (number or "").strip()
	country_code = (country_code or "").strip()
	if not number:
		return number

	digits = "".join(char for char in number if char.isdigit())
	if not digits:
		return number

	if country_code:
		country_code_digits = "".join(char for char in country_code if char.isdigit())
		if country_code_digits:
			if len(digits) == 10:
				return f"+{country_code_digits}{digits}"

	# The public portal currently targets an India-first visitor workflow.
	if len(digits) == 10:
		return f"+91{digits}"

	if 11 <= len(digits) <= 15:
		return f"+{digits}"

	frappe.throw("Mobile Number must be 10 digits local or 11-15 digits with country code.")


def _resolve_employee_link(value):
	value = (value or "").strip()
	if not value:
		return value

	if frappe.db.exists("Employee", value):
		return value

	by_name = frappe.db.get_value("Employee", {"employee_name": value}, "name")
	if by_name:
		return by_name

	by_user = frappe.db.get_value("Employee", {"user_id": value}, "name")
	if by_user:
		return by_user

	by_email = frappe.db.get_value("Employee", {"company_email": value}, "name")
	if by_email:
		return by_email

	return value


def _map_prr_id_proof_type(id_proof_type):
	value = (id_proof_type or "").strip()
	mapper = {
		"PAN": "PAN Card",
	}
	return mapper.get(value, value)


def _update_invitation_status(invitation, status, time_field=None):
	if not invitation:
		return

	updates = {"invitation_status": status}
	if time_field:
		updates[time_field] = now_datetime()
	invitation.db_set(updates, update_modified=False)


@frappe.whitelist(allow_guest=True)
def submit_pre_registration(payload=None):
	data = payload or frappe.form_dict
	if isinstance(data, str):
		data = json.loads(data)

	invitation = get_valid_invitation_by_token(data.get("invitation_token"))
	if data.get("invitation_token") and not invitation:
		frappe.throw("The invitation link is invalid, expired, or already used.")

	submission_action = (data.get("submission_action") or "submit").strip().lower()
	if submission_action not in {"save", "submit"}:
		submission_action = "submit"

	require_full_submission = submission_action == "submit" or not invitation

	invitation_field_values = {}
	if invitation:
		invitation_field_values = {
			"email_id": invitation.visitor_email,
			"visit_date": invitation.visit_date,
			"expected_checkin": invitation.expected_checkin,
			"expected_checkout": invitation.expected_checkout,
			"person_to_visit": invitation.host_employee,
			"purpose_of_visit": invitation.purpose_of_visit,
			"visitor_type": invitation.visitor_type,
			"meal_required": invitation.meal_required,
			"meal_type": invitation.meal_type,
			"assigned_meal_slots": invitation.assigned_meal_slots,
			"hospitality_type": invitation.hospitality_type,
			"refreshments_required": invitation.refreshments_required,
			"conference_room": invitation.conference_room,
		}

	required_fields = [
		"visitor_name",
		"mobile_number",
		"email_id",
		"visit_date",
		"expected_checkin",
		"expected_checkout",
		"person_to_visit",
		"purpose_of_visit",
		"visitor_type",
		"id_proof_type",
		"id_proof_number",
	]

	for fieldname in required_fields:
		field_value = invitation_field_values.get(fieldname) if invitation else None
		if require_full_submission and not (field_value or data.get(fieldname)):
			frappe.throw(f"{frappe.unscrub(fieldname).title()} is required.")

	if require_full_submission and not data.get("id_proof_scan"):
		frappe.throw("ID Proof Scan is required.")

	if require_full_submission and not data.get("visitor_photo"):
		frappe.throw("Visitor Photo is required.")

	id_proof_filename, id_proof_content = _extract_file_payload(
		data.get("id_proof_scan"),
		data.get("id_proof_scan_filename") or "visitor-id-proof.png",
	)
	visitor_photo_filename, visitor_photo_content = _extract_file_payload(
		data.get("visitor_photo"),
		data.get("visitor_photo_filename") or "visitor-photo.png",
	)
	person_to_visit = _resolve_employee_link(invitation.host_employee if invitation else data.get("person_to_visit"))

	if person_to_visit and not frappe.db.exists("Employee", person_to_visit):
		frappe.throw("Person to Visit must be a valid Employee (Employee ID or exact Employee Name).")

	id_proof_url = _store_file(
		id_proof_filename,
		id_proof_content,
	)
	visitor_photo_url = _store_file(
		visitor_photo_filename,
		visitor_photo_content,
	)

	if invitation:
		prr_name = invitation.pre_registration_request
		if prr_name:
			prr_doc = frappe.get_doc("Pre-Registration Request", prr_name)
		else:
			prr_doc = frappe.new_doc("Pre-Registration Request")

		mobile_number = data.get("mobile_number")
		existing_mobile = prr_doc.mobile_number
		normalized_mobile = (
			_normalize_mobile_number(mobile_number, data.get("mobile_country_code"))
			if mobile_number
			else existing_mobile
		)
		id_proof_scan_url = id_proof_url or prr_doc.id_proof_scan
		visitor_photo_file_url = visitor_photo_url or prr_doc.visitor_photo

		prr_doc.update(
			{
				"visitor_name": data.get("visitor_name"),
				"mobile_number": normalized_mobile,
				"email_id": invitation.visitor_email,
				"company__organisation": data.get("company__organisation"),
				"visit_date": invitation.visit_date,
				"expected_checkin": invitation.expected_checkin,
				"expected_checkout": invitation.expected_checkout,
				"person_to_visit": person_to_visit,
				"purpose_of_visit": invitation.purpose_of_visit,
				"visitor_type": invitation.visitor_type,
				"supplier_visit_mode": data.get("supplier_visit_mode"),
				"meeting_subject": data.get("meeting_subject"),
				"purchase_order": data.get("purchase_order"),
				"delivery_note": data.get("delivery_note"),
				"goods_description": data.get("goods_description"),
				"meal_required": invitation.meal_required,
				"meal_type": invitation.meal_type,
				"assigned_meal_slots": invitation.assigned_meal_slots,
				"hospitality_type": invitation.hospitality_type,
				"refreshments_required": invitation.refreshments_required,
				"conference_room": invitation.conference_room,
				"id_proof_type": _map_prr_id_proof_type(data.get("id_proof_type")),
				"id_proof_number": data.get("id_proof_number"),
				"id_proof_scan": id_proof_scan_url,
				"visitor_photo": visitor_photo_file_url,
				"visitor_invitation": invitation.name,
				"request_channel": "Portal",
				# Public invitation submissions are held as desk-reviewable PRRs.
				# Internal users can then pick them up from Web Submissions.
				"status": "Draft",
			}
		)

		if prr_doc.is_new():
			prr_doc.insert(ignore_permissions=True, ignore_mandatory=not require_full_submission)
		else:
			prr_doc.flags.ignore_mandatory = not require_full_submission
			prr_doc.save(ignore_permissions=True)

		invitation_updates = {
			"pre_registration_request": prr_doc.name,
			"invitation_status": "Saved" if submission_action == "save" else "Submitted",
		}
		if not invitation.link_opened_on:
			invitation_updates["link_opened_on"] = now_datetime()
		if submission_action == "save":
			invitation_updates["form_saved_on"] = now_datetime()
		else:
			invitation_updates["form_submitted_on"] = now_datetime()
		invitation.db_set(invitation_updates, update_modified=False)

		return {
			"name": prr_doc.name,
			"status": prr_doc.status,
			"pre_registration_request": prr_doc.name,
			"action": submission_action,
		}

	doc = frappe.get_doc(
		{
			"doctype": "Visitor Pass",
			"entry_type": "New",
			"visitor_full_name": data.get("visitor_name"),
			"mobile_number": _normalize_mobile_number(
				data.get("mobile_number"), data.get("mobile_country_code")
			),
			"email_id": data.get("email_id"),
			"company__organisation": data.get("company__organisation"),
			"visit_date": data.get("visit_date"),
			"expected_checkin": data.get("expected_checkin"),
			"expected_checkout": data.get("expected_checkout"),
			"person_to_visit": person_to_visit,
			"purpose_of_visit": data.get("purpose_of_visit"),
			"visitor_type": data.get("visitor_type"),
			"supplier_visit_mode": data.get("supplier_visit_mode"),
			"meeting_subject": data.get("meeting_subject"),
			"purchase_order": data.get("purchase_order"),
			"delivery_note": data.get("delivery_note"),
			"goods_description": data.get("goods_description"),
			"meal_required": data.get("meal_required"),
			"meal_type": data.get("meal_type"),
			"refreshments_required": data.get("refreshments_required"),
			"conference_room": data.get("conference_room"),
			"id_proof_type": data.get("id_proof_type"),
			"id_proof_number": data.get("id_proof_number"),
			"id_proof_scan": id_proof_url,
			"visitor_photo": visitor_photo_url,
			"status": "Draft",
			"request_channel": "Portal",
		}
	)
	doc.insert(ignore_permissions=True)

	prr_doc = frappe.get_doc(
		{
			"doctype": "Pre-Registration Request",
			"visitor_name": data.get("visitor_name"),
			"mobile_number": doc.mobile_number,
			"email_id": data.get("email_id"),
			"company__organisation": data.get("company__organisation"),
			"visit_date": data.get("visit_date"),
			"expected_checkin": data.get("expected_checkin"),
			"expected_checkout": data.get("expected_checkout"),
			"person_to_visit": person_to_visit,
			"purpose_of_visit": data.get("purpose_of_visit"),
			"visitor_type": data.get("visitor_type"),
			"supplier_visit_mode": data.get("supplier_visit_mode"),
			"meeting_subject": data.get("meeting_subject"),
			"purchase_order": data.get("purchase_order"),
			"delivery_note": data.get("delivery_note"),
			"goods_description": data.get("goods_description"),
			"meal_required": data.get("meal_required"),
			"meal_type": data.get("meal_type"),
			"refreshments_required": data.get("refreshments_required"),
			"conference_room": data.get("conference_room"),
			"id_proof_type": _map_prr_id_proof_type(data.get("id_proof_type")),
			"id_proof_number": data.get("id_proof_number"),
			"id_proof_scan": id_proof_url,
			"visitor_photo": visitor_photo_url,
			"status": "Draft" if submission_action == "save" else "Converted",
			"request_channel": "Portal",
			"visitor_pass": doc.name if submission_action == "submit" else None,
		}
	)
	prr_doc.insert(ignore_permissions=True)
	if submission_action == "submit":
		doc.db_set("pre_registration_request", prr_doc.name, update_modified=False)

	doc.reload()
	return {
		"name": prr_doc.name if submission_action == "save" else doc.name,
		"status": prr_doc.status if submission_action == "save" else doc.status,
		"workflow_state": doc.workflow_state if submission_action == "submit" else None,
		"pre_registration_request": prr_doc.name,
		"action": submission_action,
	}
