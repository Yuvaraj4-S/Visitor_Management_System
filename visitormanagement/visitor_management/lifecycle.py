import frappe
from frappe.utils import cint, getdate, now_datetime, nowdate


DEFAULT_RISK_BY_TYPE = {
	"Candidate": "Low",
	"Contractor": "High",
	"Customer": "Low",
	"Supplier": "Medium",
	"VIP": "Medium",
}

DEFAULT_SLA_BY_TYPE = {
	"Candidate": 180,
	"Contractor": 120,
	"Customer": 90,
	"Supplier": 90,
	"VIP": 30,
}

COMPLIANCE_OK_STATUSES = {"Completed", "Served", "Closed", "Cancelled"}


def normalize_visitor_pass(doc):
	if not doc.status:
		doc.status = "Draft"

	if not doc.request_channel:
		doc.request_channel = "Desk"

	if not doc.risk_level:
		doc.risk_level = infer_risk_level(doc)

	if not doc.approval_sla_minutes:
		doc.approval_sla_minutes = DEFAULT_SLA_BY_TYPE.get(doc.visitor_type, 120)

	if doc.visitor_type == "Supplier" and not doc.supplier_visit_mode:
		doc.supplier_visit_mode = "Delivery"

	if doc.actual_checkin:
		doc.no_show = 0
	elif should_mark_no_show(doc):
		doc.no_show = 1


def infer_risk_level(doc):
	if doc.visitor_type == "Supplier" and getattr(doc, "supplier_visit_mode", None) == "Delivery":
		return "Medium"

	return DEFAULT_RISK_BY_TYPE.get(doc.visitor_type, "Medium")


def should_mark_no_show(doc):
	if not getattr(doc, "visit_date", None):
		return False

	if getattr(doc, "status", None) in {"Checked-In", "Checked-Out", "Cancelled"}:
		return False

	if getattr(doc, "actual_checkin", None):
		return False

	return getdate(doc.visit_date) < getdate(nowdate())


def ensure_hospitality_request(visitor_pass):
	if not visitor_pass.name:
		return None

	requires_service = any(
		[
			cint(getattr(visitor_pass, "meal_required", 0)),
			cint(getattr(visitor_pass, "refreshments_required", 0)),
			getattr(visitor_pass, "conference_room", None),
		]
	)
	if not requires_service:
		return None

	request_name = visitor_pass.hospitality_request or frappe.db.get_value(
		"Hospitality Request", {"visitor_pass": visitor_pass.name}, "name"
	)
	if request_name:
		doc = frappe.get_doc("Hospitality Request", request_name)
	else:
		doc = frappe.new_doc("Hospitality Request")
		doc.visitor_pass = visitor_pass.name

	doc.meal_required = cint(getattr(visitor_pass, "meal_required", 0))
	doc.meal_type = getattr(visitor_pass, "meal_type", None)
	doc.snacks_required = cint(getattr(visitor_pass, "refreshments_required", 0))
	doc.tea_coffee_required = cint(getattr(visitor_pass, "refreshments_required", 0))
	doc.conference_room = getattr(visitor_pass, "conference_room", None)
	doc.seating_capacity = getattr(visitor_pass, "number_of_people", None)
	doc.service_time = getattr(visitor_pass, "service_time", None)
	doc.assigned_staff = getattr(visitor_pass, "food_dept_staff_assigned", None)
	doc.status = getattr(visitor_pass, "food_status", None) or "Pending"
	doc.notes = "\n".join(
		filter(
			None,
			[
				getattr(visitor_pass, "hospitality_notes", None),
				getattr(visitor_pass, "refreshment_notes", None),
			],
		)
	)

	if doc.is_new():
		doc.insert(ignore_permissions=True)
	else:
		doc.save(ignore_permissions=True)

	if visitor_pass.hospitality_request != doc.name:
		visitor_pass.db_set("hospitality_request", doc.name, update_modified=False)

	return doc.name


def sync_hospitality_to_pass(request_doc):
	if not request_doc.visitor_pass:
		return

	frappe.db.set_value(
		"Visitor Pass",
		request_doc.visitor_pass,
		{
			"hospitality_request": request_doc.name,
			"food_status": request_doc.status,
			"food_dept_staff_assigned": request_doc.assigned_staff,
			"conference_room": request_doc.conference_room,
			"service_time": request_doc.service_time,
		},
		update_modified=False,
	)


def log_visitor_event(
	visitor_pass_name,
	event_type,
	event_status=None,
	source_doctype=None,
	source_name=None,
	details=None,
):
	if not visitor_pass_name or not event_type:
		return None

	payload = {
		"visitor_pass": visitor_pass_name,
		"event_type": event_type,
		"event_status": event_status or "Recorded",
		"source_doctype": source_doctype,
		"source_name": source_name,
		"event_time": now_datetime(),
		"details": frappe.as_json(details or {}),
	}

	log_name = None
	if source_doctype and source_name:
		log_name = frappe.db.get_value(
			"Visitor Event Log",
			{
				"visitor_pass": visitor_pass_name,
				"source_doctype": source_doctype,
				"source_name": source_name,
				"event_type": event_type,
			},
			"name",
		)

	if log_name:
		doc = frappe.get_doc("Visitor Event Log", log_name)
		doc.update(payload)
		doc.save(ignore_permissions=True)
		return doc.name

	doc = frappe.get_doc({"doctype": "Visitor Event Log", **payload})
	doc.insert(ignore_permissions=True)
	return doc.name


def sync_compliance_check(visitor_pass_name, security_log=None):
	if not visitor_pass_name:
		return None

	visitor_pass = frappe.get_doc("Visitor Pass", visitor_pass_name)
	hospitality_status = None
	if visitor_pass.hospitality_request:
		hospitality_status = frappe.db.get_value(
			"Hospitality Request", visitor_pass.hospitality_request, "status"
		)

	if not security_log:
		names = frappe.get_all(
			"Security Log",
			filters={"visitor_pass": visitor_pass.name},
			fields=["name"],
			order_by="creation desc",
			limit=1,
		)
		if names:
			security_log = frappe.get_doc("Security Log", names[0].name)

	id_verified = cint(getattr(security_log, "id_proof_match", 0)) if security_log else 0
	pass_photo_verified = cint(getattr(security_log, "pass_photo_match", 0)) if security_log else 0
	gate_photo_captured = 1 if security_log and getattr(security_log, "photo_at_gate", None) else 0
	items_verified = cint(
		getattr(visitor_pass, "items_verified", 0) or getattr(visitor_pass, "all_items_verified", 0)
	)
	hospitality_closed = 1
	if visitor_pass.hospitality_request:
		hospitality_closed = cint((hospitality_status or "Pending") in COMPLIANCE_OK_STATUSES)

	manual_override = cint(getattr(security_log, "manual_override", 0)) if security_log else 0
	alert_level = getattr(security_log, "alert_level", None) if security_log else None
	no_show = cint(getattr(visitor_pass, "no_show", 0))
	verification_duration = getattr(security_log, "verification_duration", 0) if security_log else 0

	missing_requirements = []
	if no_show:
		missing_requirements.append("Visitor did not check in")
	if not id_verified:
		missing_requirements.append("ID proof verification missing")
	if not pass_photo_verified:
		missing_requirements.append("Pass photo verification missing")
	if not gate_photo_captured:
		missing_requirements.append("Gate photo capture missing")
	if not items_verified:
		missing_requirements.append("Declared items not fully verified")
	if not hospitality_closed:
		missing_requirements.append("Hospitality workflow still open")
	if manual_override:
		missing_requirements.append("Manual override used at security")
	if alert_level in {"High", "Critical"}:
		missing_requirements.append(f"Raised alert level: {alert_level}")

	if no_show:
		compliance_status = "No Show"
	elif manual_override or alert_level in {"High", "Critical"}:
		compliance_status = "Non-Compliant"
	elif missing_requirements:
		compliance_status = "Needs Review"
	else:
		compliance_status = "Compliant"

	score = max(0, 100 - (len(missing_requirements) * 15))
	check_name = frappe.db.get_value("Compliance Check", {"visitor_pass": visitor_pass.name}, "name")
	doc = (
		frappe.get_doc("Compliance Check", check_name)
		if check_name
		else frappe.new_doc("Compliance Check")
	)
	doc.visitor_pass = visitor_pass.name
	doc.visit_date = visitor_pass.visit_date
	doc.visitor_type = visitor_pass.visitor_type
	doc.host = visitor_pass.person_to_visit
	doc.compliance_status = compliance_status
	doc.score = score
	doc.id_verified = id_verified
	doc.pass_photo_verified = pass_photo_verified
	doc.gate_photo_captured = gate_photo_captured
	doc.items_verified = items_verified
	doc.hospitality_closed = hospitality_closed
	doc.manual_override = manual_override
	doc.alert_level = alert_level
	doc.no_show = no_show
	doc.verification_duration = verification_duration or 0
	doc.missing_requirements = "\n".join(missing_requirements)
	doc.exception_reason = getattr(security_log, "exception_reason", None) if security_log else None
	doc.last_security_log = getattr(security_log, "name", None) if security_log else None
	doc.last_checked_on = now_datetime()

	if doc.is_new():
		doc.insert(ignore_permissions=True)
	else:
		doc.save(ignore_permissions=True)

	return doc.name
