"""Backend for the Security Gate PWA (/gate).

Every endpoint requires gate permission (create on Security Log — Security /
System Manager), exactly like the desk gate APIs in visitor_gate.py. Check-in /
check-out go through Security Log, so all existing gate rules (blacklist, status,
QR / photo / ID confirmation, recurring passes, host alerts) apply unchanged.
"""

import base64
import json
import os

import frappe
from frappe import _
from frappe.utils import add_days, cint, getdate, today

from visitormanagement.visitor_management.api.visitor_gate import _assert_gate_permission
from visitormanagement.visitor_management.utils import (
	checked_in_on,
	in_recurring_window,
	mask_for_user,
	recurring_window,
)

PASS_FIELDS = [
	"name", "visitor_full_name", "visitor_type", "company__organisation", "mobile_number", "person_to_visit",
	"purpose_of_visit", "visit_date", "expected_checkin", "expected_checkout", "status", "badge_number",
	"badge_colour", "visitor_photo", "actual_checkin", "actual_checkout", "is_recurring", "recurring_start_date",
	"recurring_end_date", "mapping_type", "visitor_group",
]
BADGE_HEX = {"Orange": "#fd7e14", "Purple": "#6f42c1", "Green": "#28a745", "Teal": "#20c997",
			 "Gold": "#ffc107", "Blue": "#007bff", "Red": "#dc3545", "Grey": "#6c757d"}


def _host_names(rows):
	employees = {r.person_to_visit for r in rows if r.get("person_to_visit")}
	if not employees:
		return {}
	return dict(frappe.get_all("Employee", filters={"name": ["in", list(employees)]}, fields=["name", "employee_name"], as_list=True))


def _decorate(rows):
	names = _host_names(rows)
	for r in rows:
		r.host_name = names.get(r.person_to_visit) or r.person_to_visit
		r.badge_hex = BADGE_HEX.get(r.badge_colour or "", "#64748b")
	return rows


# ─────────────────────────────────────────────────────────
# Dashboard / lists
# ─────────────────────────────────────────────────────────
@frappe.whitelist()
def get_gate_dashboard():
	_assert_gate_permission()
	day = getdate(today())
	approved = frappe.get_all(
		"Visitor Pass",
		filters={"docstatus": 1, "status": ["in", ["Approved", "Items Verified", "Checked-Out"]],
				 "visit_date": [">=", add_days(day, -31)]},
		fields=PASS_FIELDS,
		order_by="expected_checkin asc",
	)
	expected = []
	for r in approved:
		if r.is_recurring and recurring_window(r):
			if in_recurring_window(r, day) and not checked_in_on(r.name, day):
				expected.append(r)
		elif r.status != "Checked-Out" and getdate(r.visit_date) == day:
			expected.append(r)
	inside = frappe.get_all("Visitor Pass", filters={"docstatus": 1, "status": "Checked-In"}, fields=PASS_FIELDS,
							order_by="actual_checkin desc")
	left = frappe.get_all(
		"Visitor Pass",
		filters={"docstatus": 1, "status": "Checked-Out",
				 "actual_checkout": ["between", [f"{day} 00:00:00", f"{add_days(day, 1)} 00:00:00"]]},
		fields=PASS_FIELDS,
		order_by="actual_checkout desc",
	)
	pending = frappe.db.count("Gate Entry Request", {"status": "Pending Approval"})
	return {
		"stats": {
			"expected": len(expected),
			"inside": len(inside),
			"left": len(left),
			"pending_requests": pending,
			"vip_today": sum(1 for r in expected + inside if r.visitor_type == "VIP"),
		},
		"expected": _decorate(expected),
		"inside": _decorate(inside),
		"left": _decorate(left),
		"badge_legend": [{"visitor_type": t, "colour": c, "hex": BADGE_HEX.get(c, "#64748b")} for t, c in _badge_colours().items()],
	}


def _badge_colours():
	settings = frappe.get_cached_doc("VMS Settings")
	defaults = {"Contractor": "Orange", "Candidate": "Purple", "Customer": "Green", "Supplier": "Teal", "VIP": "Gold"}
	return {t: settings.get(f"badge_colour_{t.lower()}") or c for t, c in defaults.items()}


# ─────────────────────────────────────────────────────────
# Lookups
# ─────────────────────────────────────────────────────────
@frappe.whitelist()
def search_employee(query=""):
	_assert_gate_permission()
	like = f"%{query or ''}%"
	return frappe.get_all(
		"Employee",
		filters={"status": "Active"},
		or_filters={"name": ["like", like], "employee_name": ["like", like], "department": ["like", like]},
		fields=["name", "employee_name", "department", "designation"],
		order_by="employee_name asc",
		limit=15,
	)


@frappe.whitelist()
def search_employee_group(query=""):
	_assert_gate_permission()
	return frappe.get_all(
		"Employee Group", filters={"name": ["like", f"%{query or ''}%"]}, pluck="name", order_by="name asc", limit=15
	)


@frappe.whitelist()
def resolve_qr(qr_data):
	"""QR payload (PASS:<name>|VISITOR:..|VISIT_DATE:..) or a typed pass ID -> pass name."""
	_assert_gate_permission()
	value = (qr_data or "").strip()
	parts = dict(p.split(":", 1) for p in value.split("|") if ":" in p)
	name = (parts.get("PASS") or value).strip()
	if not frappe.db.exists("Visitor Pass", name):
		frappe.throw(_("No Visitor Pass found for {0}.").format(frappe.bold(name)))
	return {"visitor_pass": name, "scanned": cint("PASS" in parts)}


@frappe.whitelist()
def get_visitor_for_checkin(pass_name):
	_assert_gate_permission()
	doc = frappe.get_doc("Visitor Pass", pass_name)
	doc.check_permission("read")
	data = {f: doc.get(f) for f in PASS_FIELDS}
	data.update(
		{
			"id_proof_type": doc.id_proof_type,
			"id_proof_number": mask_for_user(doc),
			"id_proof_scan": doc.id_proof_scan,
			"host_name": frappe.db.get_value("Employee", doc.person_to_visit, "employee_name") or doc.person_to_visit,
			"badge_hex": BADGE_HEX.get(doc.badge_colour or "", "#64748b"),
			"items": [
				{"name": i.name, "item_name": i.item_name, "quantity": i.quantity, "serial_number": i.serial_number}
				for i in doc.visitor_items or []
			],
		}
	)
	data["next_action"], data["blocked_reason"] = _next_action(doc)
	return data


def _next_action(doc):
	if doc.docstatus != 1:
		return None, _("Pass is not approved yet ({0}).").format(doc.workflow_state or doc.status)
	if doc.status == "Checked-In":
		return "checkout", None
	day = getdate(today())
	if recurring_window(doc):
		if not in_recurring_window(doc, day):
			start, end = recurring_window(doc)
			return None, _("Pass valid {0} to {1}, not today.").format(start, end)
		if checked_in_on(doc.name, day):
			return None, _("Already checked in today.")
		return "checkin", None
	if doc.status in ("Approved", "Items Verified"):
		if getdate(doc.visit_date) != day:
			return None, _("Pass is for {0}, not today.").format(doc.visit_date)
		return "checkin", None
	return None, _("Pass is {0}.").format(doc.status)


# ─────────────────────────────────────────────────────────
# Check-in / check-out
# ─────────────────────────────────────────────────────────
def save_base64_image(base64_data, pass_name, prefix="gate"):
	"""Store a camera capture (data URL) as a private File on the pass."""
	if not base64_data:
		return None
	if "," in base64_data:
		header, base64_data = base64_data.split(",", 1)
		ext = "png" if "png" in header else "jpg"
	else:
		ext = "jpg"
	content = base64.b64decode(base64_data)
	if len(content) > 5 * 1024 * 1024:
		frappe.throw(_("Photo is too large (max 5 MB)."))
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"{prefix}_{pass_name}_{frappe.generate_hash(length=6)}.{ext}",
			"attached_to_doctype": "Visitor Pass",
			"attached_to_name": pass_name,
			"content": content,
			"is_private": 1,
		}
	)
	file_doc.insert(ignore_permissions=True)
	return file_doc.file_url


@frappe.whitelist()
def complete_checkin(pass_name, gate_photo, id_proof_match=0, pass_photo_match=0, qr_scanned=0, verified_items=None):
	_assert_gate_permission()
	doc = frappe.get_doc("Visitor Pass", pass_name)
	verified = set(json.loads(verified_items) if isinstance(verified_items, str) else (verified_items or []))
	log = frappe.get_doc(
		{
			"doctype": "Security Log",
			"visitor_pass": pass_name,
			"event_type": "Check-In",
			"qr_code_value": f"PASS:{pass_name}" if cint(qr_scanned) else None,
			"qr_code_scanned": cint(qr_scanned),
			"photo_at_gate": save_base64_image(gate_photo, pass_name, "checkin"),
			"id_proof_match": cint(id_proof_match),
			"pass_photo_match": cint(pass_photo_match),
			"items_verification": [
				{
					"visitor_item_row_name": i.name,
					"item_name": i.item_name,
					"item_category": i.item_category,
					"quantity_declared": i.quantity,
					"uom": i.unit_of_measure,
					"serial__asset_number": i.serial_number,
					"quantity_found": i.quantity,
					"item_verified": 1 if i.name in verified else 0,
				}
				for i in doc.visitor_items or []
			],
		}
	)
	log.insert()
	return {"security_log": log.name, "status": frappe.db.get_value("Visitor Pass", pass_name, "status")}


@frappe.whitelist()
def complete_checkout(pass_name, gate_photo, id_proof_match=0, pass_photo_match=0, qr_scanned=0):
	_assert_gate_permission()
	log = frappe.get_doc(
		{
			"doctype": "Security Log",
			"visitor_pass": pass_name,
			"event_type": "Check-Out",
			"qr_code_value": f"PASS:{pass_name}" if cint(qr_scanned) else None,
			"qr_code_scanned": cint(qr_scanned),
			"photo_at_gate": save_base64_image(gate_photo, pass_name, "checkout"),
			"id_proof_match": cint(id_proof_match),
			"pass_photo_match": cint(pass_photo_match),
		}
	)
	log.insert()
	return {"security_log": log.name, "status": frappe.db.get_value("Visitor Pass", pass_name, "status")}


# ─────────────────────────────────────────────────────────
# Gate Entry Requests (ad-hoc visitors)
# ─────────────────────────────────────────────────────────
def _save_request_image(base64_data, prefix):
	if not base64_data:
		return None
	if "," in base64_data:
		base64_data = base64_data.split(",", 1)[1]
	file_doc = frappe.get_doc(
		{"doctype": "File", "file_name": f"{prefix}_{frappe.generate_hash(length=8)}.jpg",
		 "content": base64.b64decode(base64_data), "is_private": 1}
	)
	file_doc.insert(ignore_permissions=True)
	return file_doc


@frappe.whitelist()
def create_gate_entry(data):
	"""Create a Gate Entry Request from the PWA form and send it for approval."""
	_assert_gate_permission()
	data = frappe._dict(json.loads(data) if isinstance(data, str) else data)
	photo = _save_request_image(data.pop("visitor_photo_data", None), "visitor")
	id_scan = _save_request_image(data.pop("id_proof_scan_data", None), "idproof")
	allowed = {
		"visitor_full_name", "mobile_number", "email_id", "visitor_type", "visitor_company", "number_of_visitors",
		"id_proof_type", "id_proof_number", "mapping_type", "person_to_visit", "visitor_group", "purpose_of_visit",
		"expected_duration", "meal_required", "cab_required", "hotel_required", "factory_tour_required",
		"greeting_required", "special_instructions",
	}
	values = {k: v for k, v in data.items() if k in allowed}
	item_fields = ("item_name", "item_category", "quantity", "serial_number", "description")
	values["visitor_items"] = [
		{f: row.get(f) for f in item_fields}
		for row in (data.get("items") or [])
		if isinstance(row, dict) and (row.get("item_name") or "").strip()
	]
	doc = frappe.get_doc({"doctype": "Gate Entry Request", **values})
	doc.visitor_photo = photo.file_url if photo else None
	doc.id_proof_scan = id_scan.file_url if id_scan else None
	doc.insert()
	for f in (photo, id_scan):
		if f:
			f.db_set({"attached_to_doctype": "Gate Entry Request", "attached_to_name": doc.name})
	doc.submit_for_approval()
	return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def get_gate_entries(days=1):
	_assert_gate_permission()
	since = add_days(today(), -cint(days or 1) + 1)
	return frappe.get_all(
		"Gate Entry Request",
		filters={"creation": [">=", f"{since} 00:00:00"]},
		fields=["name", "visitor_full_name", "visitor_type", "status", "person_to_visit", "person_to_visit_name",
				"visitor_group", "mapping_type", "request_datetime", "visitor_pass", "rejection_reason", "creation"],
		order_by="creation desc",
		limit=100,
	)


# ─────────────────────────────────────────────────────────
# Service worker (scope /gate)
# ─────────────────────────────────────────────────────────
@frappe.whitelist(allow_guest=True, methods=["GET"])
def service_worker():
	from werkzeug.wrappers import Response

	path = os.path.join(frappe.get_app_path("visitormanagement"), "public", "gate", "service-worker.js")
	content = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
	response = Response(content, mimetype="application/javascript")
	response.headers["Service-Worker-Allowed"] = "/gate"
	response.headers["Cache-Control"] = "no-cache"
	return response
