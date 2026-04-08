import base64
import json

import frappe


def _decode_base64_payload(payload):
	if not payload:
		return None

	if "," in payload:
		payload = payload.split(",", 1)[1]

	return base64.b64decode(payload)


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


@frappe.whitelist(allow_guest=True)
def submit_pre_registration(payload=None):
	data = payload or frappe.form_dict
	if isinstance(data, str):
		data = json.loads(data)

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
		if not data.get(fieldname):
			frappe.throw(f"{frappe.unscrub(fieldname).title()} is required.")

	if not data.get("id_proof_scan"):
		frappe.throw("ID Proof Scan is required.")

	if not data.get("visitor_photo"):
		frappe.throw("Visitor Photo is required.")

	id_proof_content = _decode_base64_payload(data.get("id_proof_scan"))
	visitor_photo_content = _decode_base64_payload(data.get("visitor_photo"))
	person_to_visit = _resolve_employee_link(data.get("person_to_visit"))

	if person_to_visit and not frappe.db.exists("Employee", person_to_visit):
		frappe.throw("Person to Visit must be a valid Employee (Employee ID or exact Employee Name).")

	id_proof_url = _store_file(
		data.get("id_proof_scan_filename") or "visitor-id-proof.png",
		id_proof_content,
	)
	visitor_photo_url = _store_file(
		data.get("visitor_photo_filename") or "visitor-photo.png",
		visitor_photo_content,
	)

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
			"status": "Converted",
			"request_channel": "Portal",
			"visitor_pass": doc.name,
		}
	)
	prr_doc.insert(ignore_permissions=True)
	doc.db_set("pre_registration_request", prr_doc.name, update_modified=False)

	doc.reload()
	return {
		"name": doc.name,
		"status": doc.status,
		"workflow_state": doc.workflow_state,
		"pre_registration_request": prr_doc.name,
	}
