import base64
import binascii
import json

import frappe
from frappe.utils import now_datetime

from visitormanagement.visitor_management.doctype.visitor_invitation.visitor_invitation import (
	get_valid_invitation_by_token,
)

PENDING_APPROVAL_BY_TYPE = {
	"Contractor": "Pending System Manager",
	"Supplier": "Pending System Manager",
	"Candidate": "Pending HR Manager",
	"Customer": "Pending Sales Manager",
	"VIP": "Pending HOD",
}


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

	try:
		return filename, base64.b64decode(data)
	except (binascii.Error, ValueError):
		frappe.throw("Uploaded file is corrupted or in an unsupported format. Please re-upload.")


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
		if country_code_digits and len(digits) == 10:
			return f"+{country_code_digits}{digits}"

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


def _normalize_id_proof_type(id_proof_type):
	value = (id_proof_type or "").strip()
	mapper = {
		"PAN": "PAN Card",
	}
	return mapper.get(value, value)


def _parse_visitor_items(items):
	if not items:
		return []

	if isinstance(items, str):
		items = json.loads(items)

	parsed_items = []
	for row in items:
		if not isinstance(row, dict):
			continue

		item_name = (row.get("item_name") or "").strip()
		if not item_name:
			continue

		parsed_items.append(
			{
				"item_code": row.get("item_code"),
				"item_name": item_name,
				"item_category": row.get("item_category"),
				"quantity": row.get("quantity") or 1,
				"unit_of_measure": row.get("unit_of_measure"),
				"description": row.get("description"),
				"is_new_item": row.get("is_new_item") or 0,
				"serial_number": row.get("serial_number"),
				"estimated_value": row.get("estimated_value"),
				"verification_remarks": row.get("verification_remarks"),
			}
		)

	return parsed_items


def _normalize_time(value):
	value = (value or "").strip()
	if not value:
		return value
	# "13:30" → "13:30:00"
	if len(value) == 5 and value[2] == ":":
		return value + ":00"
	return value


def _get_portal_submission_state(visitor_type, submission_action):
	# Both Save Draft and Submit produce Draft state. Staff reviews every
	# pre-registered Visitor Pass in the desk UI before advancing it into the
	# approval workflow. The `visitor_type` / `submission_action` args are kept
	# for signature compatibility; they no longer change the target state.
	return "Draft"


def _build_visitor_pass_values(data, person_to_visit, id_proof_url, visitor_photo_url, invitation=None):
	visitor_type = invitation.visitor_type if invitation else data.get("visitor_type")
	submission_action = (data.get("submission_action") or "submit").strip().lower()
	target_state = _get_portal_submission_state(visitor_type, submission_action)

	return {
		"entry_type": "New",
		"visitor_full_name": (
			data.get("visitor_full_name")
			or data.get("visitor_name")
			or (invitation.get("visitor_full_name") if invitation else None)
		),
		"mobile_number": _normalize_mobile_number(
			data.get("mobile_number")
			or (invitation.get("visitor_mobile") if invitation else None),
			data.get("mobile_country_code"),
		),
		"email_id": invitation.visitor_email if invitation else data.get("email_id"),
		"company__organisation": data.get("company__organisation"),
		"visit_date": invitation.visit_date if invitation else data.get("visit_date"),
		"expected_checkin": _normalize_time(str(invitation.expected_checkin) if invitation else data.get("expected_checkin")),
		"expected_checkout": _normalize_time(str(invitation.expected_checkout) if invitation else data.get("expected_checkout")),
		"person_to_visit": person_to_visit,
		"purpose_of_visit": invitation.purpose_of_visit if invitation else data.get("purpose_of_visit"),
		"visitor_type": visitor_type,
		"supplier_link": data.get("supplier_link"),
		"supplier_visit_mode": data.get("supplier_visit_mode"),
		"purchase_order": data.get("purchase_order"),
		"delivery_note": data.get("delivery_note"),
		"goods_description": data.get("goods_description"),
		"visit_category": data.get("visit_category"),
		"contractor_link": data.get("contractor_link"),
		"work_order_ref": data.get("work_order_ref"),
		"tools_list": data.get("tools_list"),
		"multi_day_pass": data.get("multi_day_pass"),
		"pass_valid_until": data.get("pass_valid_until"),
		"job_applicant_link": data.get("job_applicant_link"),
		"position_applied": data.get("position_applied"),
		"candidate_interview_type": data.get("candidate_interview_type"),
		"interview_panel": data.get("interview_panel"),
		"vip_category": data.get("vip_category"),
		"interpreter_required": data.get("interpreter_required"),
		"interpreter_language": data.get("interpreter_language"),
		"protocol_notes": data.get("protocol_notes"),
		"vehicle_number": data.get("vehicle_number"),
		"id_proof_type": _normalize_id_proof_type(data.get("id_proof_type")),
		"id_proof_number": data.get("id_proof_number"),
		"id_proof_scan": id_proof_url,
		"visitor_photo": visitor_photo_url,
		"status": target_state,
		"workflow_state": target_state,
		"request_channel": "Portal",
		"visitor_invitation": invitation.name if invitation else None,
	}


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
		}

	required_fields = [
		"visitor_full_name",
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
		form_value = data.get(fieldname) or (data.get("visitor_name") if fieldname == "visitor_full_name" else None)
		if require_full_submission and not (field_value or form_value):
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
	visitor_items = _parse_visitor_items(data.get("visitor_items"))

	if person_to_visit and not frappe.db.exists("Employee", person_to_visit):
		frappe.throw("Person to Visit must be a valid Employee (Employee ID or exact Employee Name).")

	existing_doc = None
	if invitation and invitation.visitor_pass and frappe.db.exists("Visitor Pass", invitation.visitor_pass):
		existing_doc = frappe.get_doc("Visitor Pass", invitation.visitor_pass)

	id_proof_url = _store_file(
		id_proof_filename,
		id_proof_content,
	) or (existing_doc.id_proof_scan if existing_doc else None)
	visitor_photo_url = _store_file(
		visitor_photo_filename,
		visitor_photo_content,
	) or (existing_doc.visitor_photo if existing_doc else None)

	doc_values = _build_visitor_pass_values(
		data,
		person_to_visit,
		id_proof_url,
		visitor_photo_url,
		invitation=invitation,
	)

	visitor_pass = existing_doc or frappe.new_doc("Visitor Pass")
	visitor_pass.update(doc_values)
	visitor_pass.set("visitor_items", [])
	for item in visitor_items:
		visitor_pass.append("visitor_items", item)

	target_state = _get_portal_submission_state(visitor_pass.visitor_type, submission_action)

	if visitor_pass.is_new():
		visitor_pass.insert(ignore_permissions=True, ignore_mandatory=not require_full_submission)
	else:
		visitor_pass.flags.ignore_mandatory = not require_full_submission
		visitor_pass.save(ignore_permissions=True)

	if visitor_pass.status != target_state or visitor_pass.workflow_state != target_state:
		visitor_pass.db_set(
			{
				"status": target_state,
				"workflow_state": target_state,
			},
			update_modified=False,
		)

	if invitation:
		invitation_updates = {
			"visitor_pass": visitor_pass.name,
			"invitation_status": "Saved" if submission_action == "save" else "Submitted",
		}
		if not invitation.link_opened_on:
			invitation_updates["link_opened_on"] = now_datetime()
		if submission_action == "save":
			invitation_updates["form_saved_on"] = now_datetime()
		else:
			invitation_updates["form_submitted_on"] = now_datetime()
		invitation.db_set(invitation_updates, update_modified=False)

	saved_values = frappe.db.get_value(
		"Visitor Pass",
		visitor_pass.name,
		["name", "status", "workflow_state"],
		as_dict=True,
	)
	return {
		"name": saved_values.name,
		"status": saved_values.status,
		"workflow_state": saved_values.workflow_state,
		"action": submission_action,
	}
