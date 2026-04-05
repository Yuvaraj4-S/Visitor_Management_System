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

	doc = frappe.get_doc(
		{
			"doctype": "Pre-Registration Request",
			"visitor_name": data.get("visitor_name"),
			"mobile_number": data.get("mobile_number"),
			"email_id": data.get("email_id"),
			"company__organisation": data.get("company__organisation"),
			"visit_date": data.get("visit_date"),
			"expected_checkin": data.get("expected_checkin"),
			"expected_checkout": data.get("expected_checkout"),
			"person_to_visit": data.get("person_to_visit"),
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
			"status": "Pending Approval",
			"request_channel": "Portal",
		}
	)
	doc.insert(ignore_permissions=True)

	id_proof_scan = _attach_file(
		doc.doctype,
		doc.name,
		"id_proof_scan",
		data.get("id_proof_scan_filename") or f"{doc.name}-id-proof.png",
		_decode_base64_payload(data.get("id_proof_scan")),
	)
	visitor_photo = _attach_file(
		doc.doctype,
		doc.name,
		"visitor_photo",
		data.get("visitor_photo_filename") or f"{doc.name}-visitor-photo.png",
		_decode_base64_payload(data.get("visitor_photo")),
	)

	if id_proof_scan or visitor_photo:
		doc.db_set(
			{
				"id_proof_scan": id_proof_scan,
				"visitor_photo": visitor_photo,
			}
		)

	return {"name": doc.name, "status": doc.status}
