"""Backend for the Security Gate PWA (/gate).

Every endpoint requires gate permission (create on Security Log — Security /
System Manager), exactly like the desk gate APIs in visitor_gate.py. Check-in /
check-out go through Security Log, so all existing gate rules (blacklist, status,
QR / photo / ID confirmation, host alerts) apply unchanged. Walk-in visitors go
through the existing Walk In Visitor Request (host approves -> Visitor Pass).
"""

import base64
import json
import os

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, flt, get_datetime, getdate, now_datetime, today

from visitormanagement.visitor_management.api.visitor_gate import _assert_gate_permission
from visitormanagement.visitor_management.multi_day import can_check_in_again, date_range_label, is_valid_on
from visitormanagement.visitor_management.time_utils import strip_seconds
from visitormanagement.visitor_management.validators import mask_id_number

WALK_IN_REQUEST = "Walk In Visitor Request"
PASS_FIELDS = [
	"name", "visitor_full_name", "visitor_type", "company__organisation", "mobile_number", "person_to_visit",
	"purpose_of_visit", "visit_date", "expected_checkin", "expected_checkout", "status", "badge_number",
	"badge_colour", "visitor_photo", "actual_checkin", "actual_checkout", "request_channel",
	"multi_day_pass", "pass_valid_until",
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
def _multi_day_expected_today(day, exclude=()):
	"""Multi-day passes due at the gate today: inside their date range and either not yet
	checked in, or checked out on an earlier day (they return each day)."""
	rows = frappe.get_all(
		"Visitor Pass",
		filters={
			"docstatus": 1,
			"multi_day_pass": 1,
			"status": ["in", ["Approved", "Items Verified", "Checked-Out"]],
			"visit_date": ["<=", day],
			"pass_valid_until": [">=", day],
		},
		fields=PASS_FIELDS,
	)
	return [
		r for r in rows
		if r.name not in exclude
		and not (r.status == "Checked-Out" and r.actual_checkout and getdate(r.actual_checkout) >= day)
	]


def _end_time_on(day_value, expected_checkout):
	"""Datetime of the pass's Expected Check-Out on the day of ``day_value``."""
	if not day_value or not expected_checkout:
		return None
	return get_datetime(f"{getdate(day_value)} {expected_checkout}")


def _time_over_rows(inside, left, now):
	"""Visitors past their end time: still inside after Expected Check-Out (judged on the
	day they checked in), or checked out today later than it."""
	rows = []
	for r in inside:
		deadline = _end_time_on(r.actual_checkin or now, r.expected_checkout)
		if deadline and now > deadline:
			rows.append(frappe._dict(r, time_over_state="Inside", overdue_minutes=int((now - deadline).total_seconds() // 60)))
	for r in left:
		deadline = _end_time_on(r.actual_checkout, r.expected_checkout)
		checked_out = get_datetime(r.actual_checkout) if r.actual_checkout else None
		if deadline and checked_out and checked_out > deadline:
			rows.append(
				frappe._dict(r, time_over_state="Left", overdue_minutes=int((checked_out - deadline).total_seconds() // 60))
			)
	# Still-inside first (needs action), then the longest overstay.
	rows.sort(key=lambda r: (r.time_over_state != "Inside", -r.overdue_minutes))
	return rows


@frappe.whitelist()
def get_gate_dashboard():
	_assert_gate_permission()
	day = getdate(today())
	expected = frappe.get_all(
		"Visitor Pass",
		filters={"docstatus": 1, "status": ["in", ["Approved", "Items Verified"]], "visit_date": day},
		fields=PASS_FIELDS,
		order_by="expected_checkin asc",
	)
	expected += _multi_day_expected_today(day, exclude={r.name for r in expected})
	expected.sort(key=lambda r: str(r.expected_checkin or ""))
	inside = frappe.get_all("Visitor Pass", filters={"docstatus": 1, "status": "Checked-In"}, fields=PASS_FIELDS,
							order_by="actual_checkin desc")
	left = frappe.get_all(
		"Visitor Pass",
		filters={"docstatus": 1, "status": "Checked-Out",
				 "actual_checkout": ["between", [f"{day} 00:00:00", f"{day} 23:59:59"]]},
		fields=PASS_FIELDS,
		order_by="actual_checkout desc",
	)
	pending = frappe.db.count(WALK_IN_REQUEST, {"status": "Pending Approval"})
	inside, left = _decorate(inside), _decorate(left)
	time_over = _time_over_rows(inside, left, now_datetime())
	return {
		"stats": {
			"expected": len(expected),
			"inside": len(inside),
			"left": len(left),
			"pending_requests": pending,
			"vip_today": sum(1 for r in expected + inside if r.visitor_type == "VIP"),
			"time_over_inside": sum(1 for r in time_over if r.time_over_state == "Inside"),
		},
		"expected": _decorate(expected),
		"inside": inside,
		"left": left,
		"time_over": time_over,
		"badge_legend": [
			{"visitor_type": t.name, "colour": t.badge_colour, "hex": BADGE_HEX.get(t.badge_colour or "", "#64748b")}
			for t in _visitor_types()
		],
	}


def _visitor_types():
	"""Active Visitor Type masters — badge colours are configured there on this branch."""
	return frappe.get_all(
		"Visitor Type", filters={"is_active": 1}, fields=["name", "badge_colour"], order_by="name asc"
	)


@frappe.whitelist()
def get_visitor_types():
	_assert_gate_permission()
	return [t.name for t in _visitor_types()]


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
			"id_proof_number": mask_id_number(doc.id_proof_number) if doc.id_proof_number else "",
			"id_proof_scan": doc.id_proof_scan,
			"items_carried": doc.get("items_carried"),
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
	if doc.status in ("Approved", "Items Verified") or can_check_in_again(doc):
		if not is_valid_on(doc):
			return None, _("Pass is for {0}, not today.").format(date_range_label(doc))
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
# Walk-in visitors (Walk In Visitor Request -> host approves -> Visitor Pass)
# ─────────────────────────────────────────────────────────
def _save_request_image(base64_data, prefix):
	if not base64_data:
		return None
	if "," in base64_data:
		base64_data = base64_data.split(",", 1)[1]
	content = base64.b64decode(base64_data)
	if len(content) > 5 * 1024 * 1024:
		frappe.throw(_("Photo is too large (max 5 MB)."))
	file_doc = frappe.get_doc(
		{"doctype": "File", "file_name": f"{prefix}_{frappe.generate_hash(length=8)}.jpg",
		 "content": content, "is_private": 1}
	)
	file_doc.insert(ignore_permissions=True)
	return file_doc


def _items_text(items):
	"""Items typed at the gate -> the request's 'Items Carried' text (one line per item)."""
	lines = []
	for row in items or []:
		if not isinstance(row, dict) or not (row.get("item_name") or "").strip():
			continue
		line = f"{row['item_name'].strip()} × {flt(row.get('quantity')) or 1:g}"
		if (row.get("serial_number") or "").strip():
			line += f" (S/N {row['serial_number'].strip()})"
		lines.append(line)
	return "\n".join(lines)


def _additional_visitor_rows(rows):
	"""Other people in a walk-in group: name required, everything else optional."""
	out, files = [], []
	for row in rows:
		name = (row.get("visitor_full_name") or "").strip()
		if not name:
			frappe.throw(_("Each additional visitor needs a name."))
		photo = _save_request_image(row.get("visitor_photo_data"), "visitor")
		id_scan = _save_request_image(row.get("id_proof_scan_data"), "idproof")
		files += [f for f in (photo, id_scan) if f]
		out.append(
			{
				"visitor_full_name": name,
				"mobile_number": (row.get("mobile_number") or "").strip(),
				"email_id": (row.get("email_id") or "").strip(),
				"company__organisation": (row.get("company__organisation") or "").strip(),
				"vehicle_number": (row.get("vehicle_number") or "").strip(),
				"items_carried": _items_text(row.get("items")),
				"id_proof_type": row.get("id_proof_type") or "",
				"id_proof_number": (row.get("id_proof_number") or "").strip(),
				# Per-person times; empty falls back to the request's on the pass.
				"expected_checkin": strip_seconds(row.get("expected_checkin")) or None,
				"expected_checkout": strip_seconds(row.get("expected_checkout")) or None,
				"visitor_photo": photo.file_url if photo else None,
				"id_proof_scan": id_scan.file_url if id_scan else None,
			}
		)
	return out, files


@frappe.whitelist()
def create_walk_in_request(data):
	"""Create a Walk In Visitor Request from the PWA form. Its after_insert emails the host
	and creates their ToDo — the same flow as creating it from the desk."""
	_assert_gate_permission()
	data = frappe._dict(json.loads(data) if isinstance(data, str) else data)
	photo = _save_request_image(data.pop("visitor_photo_data", None), "visitor")
	id_scan = _save_request_image(data.pop("id_proof_scan_data", None), "idproof")
	extra_rows, extra_files = _additional_visitor_rows(data.pop("additional_visitors", None) or [])
	allowed = {
		"visitor_type", "visitor_full_name", "mobile_number", "email_id", "company__organisation",
		"id_proof_type", "id_proof_number", "number_of_visitors", "person_to_visit", "purpose_of_visit",
		"vehicle_number",
	}
	values = {k: v for k, v in data.items() if k in allowed}
	# Guard enters date + times; older app builds sent only an expected duration.
	checkin = now_datetime()
	checkout = add_to_date(checkin, hours=flt(data.get("expected_duration")) or 2)
	if checkout.date() != checkin.date():
		checkout = checkin.replace(hour=23, minute=59, second=0, microsecond=0)
	visit_date = data.get("visit_date") or today()
	expected_checkin = strip_seconds(data.get("expected_checkin")) or checkin.strftime("%H:%M:00")
	expected_checkout = strip_seconds(data.get("expected_checkout")) or checkout.strftime("%H:%M:00")
	doc = frappe.get_doc(
		{
			"doctype": WALK_IN_REQUEST,
			**values,
			"visit_date": visit_date,
			"expected_checkin": expected_checkin,
			"expected_checkout": expected_checkout,
			"items_carried": _items_text(data.get("items")),
			"visitor_photo": photo.file_url if photo else None,
			"id_proof_scan": id_scan.file_url if id_scan else None,
			"additional_visitors": extra_rows,
		}
	)
	doc.insert()
	for f in (photo, id_scan, *extra_files):
		if f:
			f.db_set({"attached_to_doctype": WALK_IN_REQUEST, "attached_to_name": doc.name})
	return {"name": doc.name, "status": doc.status}


@frappe.whitelist()
def get_walk_in_requests(days=1):
	_assert_gate_permission()
	since = add_to_date(getdate(today()), days=-cint(days or 1) + 1)
	rows = frappe.get_all(
		WALK_IN_REQUEST,
		filters={"creation": [">=", f"{since} 00:00:00"]},
		fields=["name", "visitor_full_name", "visitor_type", "status", "person_to_visit", "visitor_pass",
				"rejection_reason", "creation", "visit_date", "expected_checkin", "expected_checkout"],
		order_by="creation desc",
		limit=100,
	)
	names = _host_names(rows)
	extras = {}
	if rows:
		for x in frappe.get_all(
			"Walk In Additional Visitor",
			filters={"parenttype": WALK_IN_REQUEST, "parent": ["in", [r.name for r in rows]]},
			fields=["parent", "visitor_full_name", "visitor_pass", "expected_checkin", "expected_checkout"],
			order_by="idx asc",
		):
			extras.setdefault(x.parent, []).append(x)
	for r in rows:
		r.host_name = names.get(r.person_to_visit) or r.person_to_visit
		r.additional_visitors = extras.get(r.name, [])
		# Empty per-person times mean "same as the request".
		for x in r.additional_visitors:
			x.expected_checkin = x.expected_checkin or r.expected_checkin
			x.expected_checkout = x.expected_checkout or r.expected_checkout
	return rows


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
