import frappe
from frappe.utils import cint, flt, get_datetime, get_time, getdate, now_datetime, nowdate


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
HEALTH_SCREENING_OK_STATUSES = {"Cleared"}
HEALTH_REVIEW_TEMPERATURE = 37.5
HEALTH_DENY_TEMPERATURE = 38.0
VISITOR_PASS_FOOD_STATUS_FROM_REQUEST = {
	"Pending": "Pending",
	"Confirmed": "Ordered",
	"Served": "Served",
	"Completed": "Completed",
	"Cancelled": "Cancelled",
}
HOSPITALITY_REQUEST_STATUS_FROM_PASS = {
	"Pending": "Pending",
	"Ordered": "Confirmed",
	"Served": "Served",
	"Completed": "Completed",
	"Cancelled": "Cancelled",
}
ARRANGEMENT_REQUIRED_FIELDS = (
	"cab_required",
	"hotel_required",
	"factory_tour_required",
	"buggy_required",
	"greeting_required",
)
ARRANGEMENT_TERMINAL_STATUSES = {"Completed", "Delivered", "Checked Out", "Cancelled"}
MEAL_WINDOWS = (
	("Breakfast", "08:00:00", "09:00:00"),
	("Lunch", "13:00:00", "14:00:00"),
	("Dinner", "20:00:00", "21:30:00"),
)
MEAL_TYPE_SEQUENCE = ("Breakfast", "Lunch", "Dinner")
DOUBLE_MEAL_TYPES = {
	("Breakfast", "Lunch"): "Breakfast + Lunch",
	("Breakfast", "Dinner"): "Breakfast + Dinner",
	("Lunch", "Dinner"): "Lunch + Dinner",
}


def normalize_visitor_pass(doc):
	if not doc.status:
		doc.status = "Draft"

	if not doc.request_channel:
		doc.request_channel = "Desk"

	# Line 1 (title in Link dropdown) — just the visitor name.
	doc.visitor_summary = doc.visitor_full_name or "Unnamed"

	expected_risk_level = infer_risk_level(doc)
	if (
		not doc.risk_level
		or (doc.is_new() and doc.risk_level == "Low" and expected_risk_level != "Low")
	):
		doc.risk_level = expected_risk_level

	if not doc.approval_sla_minutes:
		doc.approval_sla_minutes = DEFAULT_SLA_BY_TYPE.get(doc.visitor_type, 120)

	if doc.visitor_type == "Supplier" and not doc.supplier_visit_mode:
		doc.supplier_visit_mode = "Meeting"

	if doc.actual_checkin:
		doc.no_show = 0
	elif should_mark_no_show(doc):
		doc.no_show = 1

	if doc.status != "Checked-In" and getattr(doc, "current_location", None):
		doc.current_location = None

	preserve_hospitality_choices = bool(
		getattr(doc, "request_channel", None) == "Portal"
		and not doc.is_new()
	)
	apply_hospitality_meal_plan(doc, preserve_existing=preserve_hospitality_choices)


def infer_risk_level(doc):
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
		+ [cint(getattr(visitor_pass, f, 0)) for f in ARRANGEMENT_REQUIRED_FIELDS]
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

	populate_hospitality_request_from_pass(doc, visitor_pass=visitor_pass, sync_management_fields=True)

	if doc.is_new():
		doc.insert(ignore_permissions=True)
	else:
		doc.save(ignore_permissions=True)

	if visitor_pass.hospitality_request != doc.name:
		visitor_pass.db_set("hospitality_request", doc.name, update_modified=False)

	# Auto-create a Conference Room Booking if a room is selected on the pass.
	if getattr(visitor_pass, "conference_room", None):
		ensure_conference_room_booking(visitor_pass)

	return doc.name


def ensure_conference_room_booking(visitor_pass):
	"""Create or update a Conference Room Booking for this Visitor Pass."""
	if not getattr(visitor_pass, "conference_room", None):
		return None

	existing = frappe.db.get_value(
		"Conference Room Booking",
		{"visitor_pass": visitor_pass.name, "docstatus": ["<", 2]},
		"name",
	)
	if existing:
		booking = frappe.get_doc("Conference Room Booking", existing)
	else:
		booking = frappe.new_doc("Conference Room Booking")
		booking.visitor_pass = visitor_pass.name

	# Clamp times to room operating hours if needed
	start_time, end_time = _clamp_to_room_hours(
		visitor_pass.conference_room,
		visitor_pass.expected_checkin,
		visitor_pass.expected_checkout,
	)

	booking.conference_room = visitor_pass.conference_room
	booking.meeting_title = f"Visitor Meeting — {visitor_pass.visitor_full_name or visitor_pass.name}"
	booking.booking_date = visitor_pass.visit_date
	booking.start_time = start_time
	booking.end_time = end_time
	booking.meeting_type = "External"
	booking.expected_attendees = cint(getattr(visitor_pass, "number_of_people", None)) or 1
	if not booking.booked_by:
		booking.booked_by = visitor_pass.person_to_visit

	try:
		if booking.is_new():
			booking.insert(ignore_permissions=True)
		else:
			booking.save(ignore_permissions=True)
		return booking.name
	except Exception as exc:
		frappe.log_error(f"CRB auto-create failed for {visitor_pass.name}: {exc}", "VMS CRB Auto-Create")
		return None


def _clamp_to_room_hours(room_name, start, end):
	"""Clamp visitor time window to the room's operating hours.
	Returns (start_time, end_time) strings usable for CRB booking.
	Falls back to 09:00:00–17:00:00 if room has no hours defined."""
	from frappe.utils import get_time

	from datetime import datetime, timedelta

	default_start, default_end = "09:00:00", "17:00:00"
	room = frappe.db.get_value(
		"Conference Room",
		room_name,
		["available_from", "available_to", "max_booking_hours"],
		as_dict=True,
	) or {}
	room_open = room.get("available_from") or default_start
	room_close = room.get("available_to") or default_end
	max_hours = int(room.get("max_booking_hours") or 0)

	def _as_str(t):
		if not t:
			return None
		try:
			return str(get_time(t))
		except Exception:
			return str(t)

	room_open_s = _as_str(room_open)
	room_close_s = _as_str(room_close)
	start_s = _as_str(start) or room_open_s
	end_s = _as_str(end) or room_close_s

	# Clamp start within [room_open, room_close]
	if start_s < room_open_s or start_s >= room_close_s:
		start_s = room_open_s
	# Clamp end within (start, room_close]
	if end_s <= start_s or end_s > room_close_s:
		end_s = room_close_s

	# Enforce max booking duration
	if max_hours > 0:
		base = datetime(2000, 1, 1)
		start_dt = datetime.combine(base.date(), get_time(start_s))
		end_dt = datetime.combine(base.date(), get_time(end_s))
		if (end_dt - start_dt) > timedelta(hours=max_hours):
			end_dt = start_dt + timedelta(hours=max_hours)
			# keep within room_close
			close_dt = datetime.combine(base.date(), get_time(room_close_s))
			if end_dt > close_dt:
				end_dt = close_dt
			end_s = str(end_dt.time())

	return start_s, end_s


def sync_hospitality_to_pass(request_doc):
	if not request_doc.visitor_pass:
		return

	pass_updates = {
		"hospitality_request": request_doc.name,
		"food_status": VISITOR_PASS_FOOD_STATUS_FROM_REQUEST.get(request_doc.status, "Pending"),
		"food_dept_staff_assigned": request_doc.assigned_staff,
		"conference_room": request_doc.conference_room,
		"service_time": request_doc.service_time,
		"cab_required": cint(getattr(request_doc, "cab_required", 0)),
		"hotel_required": cint(getattr(request_doc, "hotel_required", 0)),
		"factory_tour_required": cint(getattr(request_doc, "factory_tour_required", 0)),
		"buggy_required": cint(getattr(request_doc, "buggy_required", 0)),
		"greeting_required": cint(getattr(request_doc, "greeting_required", 0)),
		"hospitality_overall_status": _compute_overall_hospitality_status(request_doc),
	}
	if hasattr(request_doc, "meal_required"):
		pass_updates["meal_required"] = cint(request_doc.meal_required)
	if hasattr(request_doc, "meal_type"):
		pass_updates["meal_type"] = request_doc.meal_type
	if hasattr(request_doc, "assigned_meal_slots"):
		pass_updates["assigned_meal_slots"] = request_doc.assigned_meal_slots
	if hasattr(request_doc, "hospitality_type"):
		pass_updates["hospitality_type"] = request_doc.hospitality_type
	if hasattr(request_doc, "special_diet"):
		pass_updates["special_diet"] = request_doc.special_diet

	frappe.db.set_value(
		"Visitor Pass",
		request_doc.visitor_pass,
		pass_updates,
		update_modified=False,
	)


def _combine_visit_datetime(visit_date, visit_time):
	# Reduce visit_time to a HH:MM:SS string and strip tzinfo so the resulting datetime
	# is always naive — meal-window slots are naive too, and mixing the two raises
	# `can't compare offset-naive and offset-aware datetimes` in _overlaps_time_window.
	if not visit_date or not visit_time:
		return None

	try:
		time_obj = get_time(visit_time)  # accepts time / datetime / timedelta / str
		time_str = time_obj.strftime("%H:%M:%S")
	except Exception:
		time_str = str(visit_time)

	dt = get_datetime(f"{visit_date} {time_str}")
	if dt and getattr(dt, "tzinfo", None) is not None:
		dt = dt.replace(tzinfo=None)
	return dt


def _overlaps_time_window(start_dt, end_dt, window_start_dt, window_end_dt):
	if not start_dt or not end_dt or not window_start_dt or not window_end_dt:
		return False

	return start_dt < window_end_dt and end_dt > window_start_dt


def derive_hospitality_meal_plan(visitor_pass):
	visit_date = getattr(visitor_pass, "visit_date", None)
	start_dt = _combine_visit_datetime(visit_date, getattr(visitor_pass, "expected_checkin", None))
	end_dt = _combine_visit_datetime(visit_date, getattr(visitor_pass, "expected_checkout", None))
	if start_dt and end_dt and end_dt < start_dt:
		end_dt = start_dt

	applicable_meals = []
	first_service_time = None
	for meal_label, slot_start, slot_end in MEAL_WINDOWS:
		slot_start_dt = _combine_visit_datetime(visit_date, slot_start)
		slot_end_dt = _combine_visit_datetime(visit_date, slot_end)
		if _overlaps_time_window(start_dt, end_dt, slot_start_dt, slot_end_dt):
			applicable_meals.append(meal_label)
			if not first_service_time:
				first_service_time = slot_start_dt

	meal_required = 1 if applicable_meals else 0
	if len(applicable_meals) == 3:
		derived_meal_type = "All Day"
		hospitality_type = "Full Day"
	elif len(applicable_meals) == 2:
		derived_meal_type = DOUBLE_MEAL_TYPES.get(tuple(applicable_meals), " + ".join(applicable_meals))
		hospitality_type = "Two Meals"
	elif len(applicable_meals) == 1:
		derived_meal_type = applicable_meals[0]
		hospitality_type = "Single Meal"
	else:
		derived_meal_type = None
		hospitality_type = None

	return {
		"meal_required": meal_required,
		"visit_start_time": start_dt,
		"visit_end_time": end_dt,
		"assigned_meal_slots": ", ".join(applicable_meals),
		"meal_type": derived_meal_type,
		"hospitality_type": hospitality_type,
		"service_time": first_service_time if meal_required else None,
	}


def apply_hospitality_meal_plan(doc, preserve_existing=False):
	meal_plan = derive_hospitality_meal_plan(doc)
	existing_meal_type = getattr(doc, "meal_type", None)
	existing_service_time = getattr(doc, "service_time", None)
	# Respect user's manual selection — only auto-set if currently unchecked.
	user_wants_meal = cint(getattr(doc, "meal_required", 0))
	effective_meal_required = user_wants_meal or meal_plan["meal_required"]
	doc.meal_required = effective_meal_required
	# Keep meal_plan-derived values in sync for downstream logic
	meal_plan["meal_required"] = effective_meal_required
	doc.meal_type = (
		existing_meal_type if preserve_existing and effective_meal_required and existing_meal_type else meal_plan["meal_type"]
	)

	if hasattr(doc, "assigned_meal_slots"):
		doc.assigned_meal_slots = meal_plan["assigned_meal_slots"] if meal_plan["meal_required"] else None

	if hasattr(doc, "hospitality_type"):
		doc.hospitality_type = meal_plan["hospitality_type"] if meal_plan["meal_required"] else None

	if hasattr(doc, "service_time"):
		doc.service_time = (
			existing_service_time
			if preserve_existing and meal_plan["meal_required"] and existing_service_time
			else meal_plan["service_time"]
		)

	return meal_plan


def populate_hospitality_request_from_pass(doc, visitor_pass=None, sync_management_fields=False):
	visitor_pass = visitor_pass or (
		frappe.get_doc("Visitor Pass", doc.visitor_pass) if getattr(doc, "visitor_pass", None) else None
	)
	if not visitor_pass:
		return doc

	meal_plan = derive_hospitality_meal_plan(visitor_pass)
	# Honor manual meal_required on the Visitor Pass — if host/guest ticked it, carry it across
	# even if visit window doesn't overlap standard meal slots.
	vp_meal_required = cint(getattr(visitor_pass, "meal_required", 0))
	doc.meal_required = vp_meal_required or meal_plan["meal_required"]
	doc.meal_type = (
		getattr(visitor_pass, "meal_type", None) or meal_plan["meal_type"]
	) if doc.meal_required else None
	doc.visit_start_time = meal_plan["visit_start_time"]
	doc.visit_end_time = meal_plan["visit_end_time"]
	doc.assigned_meal_slots = meal_plan["assigned_meal_slots"] if doc.meal_required else None
	doc.hospitality_type = meal_plan["hospitality_type"] if doc.meal_required else None
	doc.special_diet = getattr(visitor_pass, "special_diet", None)
	doc.snacks_required = cint(getattr(visitor_pass, "refreshments_required", 0))
	doc.tea_coffee_required = cint(getattr(visitor_pass, "refreshments_required", 0))
	doc.conference_room = getattr(visitor_pass, "conference_room", None)
	doc.seating_capacity = getattr(visitor_pass, "number_of_people", None)
	doc.service_time = meal_plan["service_time"]
	if sync_management_fields:
		doc.assigned_staff = getattr(visitor_pass, "food_dept_staff_assigned", None)
		doc.status = HOSPITALITY_REQUEST_STATUS_FROM_PASS.get(
			getattr(visitor_pass, "food_status", None), "Pending"
		)
		doc.notes = "\n".join(
			filter(
				None,
				[
					getattr(visitor_pass, "hospitality_notes", None),
					getattr(visitor_pass, "refreshment_notes", None),
				],
			)
		)
	# Mirror arrangement request flags from Visitor Pass (host-entered intent)
	for flag in ARRANGEMENT_REQUIRED_FIELDS:
		if hasattr(visitor_pass, flag):
			setattr(doc, flag, cint(getattr(visitor_pass, flag, 0)))

	# Auto-fetch dates/times from Visitor Pass (only when HR fields empty)
	vp_date = getattr(visitor_pass, "visit_date", None)
	vp_checkin = _combine_visit_datetime(vp_date, getattr(visitor_pass, "expected_checkin", None))
	vp_checkout = _combine_visit_datetime(vp_date, getattr(visitor_pass, "expected_checkout", None))
	vp_valid_until = getattr(visitor_pass, "pass_valid_until", None)
	vp_people = getattr(visitor_pass, "number_of_people", None)

	if cint(doc.cab_required):
		if not doc.cab_type:
			doc.cab_type = "Both"
		if doc.cab_type in ("Pickup", "Both") and not doc.pickup_datetime and vp_checkin:
			doc.pickup_datetime = vp_checkin
		if doc.cab_type in ("Drop", "Both") and not doc.drop_datetime and vp_checkout:
			doc.drop_datetime = vp_checkout

	if cint(doc.hotel_required):
		if not doc.check_in and vp_date:
			doc.check_in = vp_date
		if not doc.check_out:
			doc.check_out = vp_valid_until or vp_date
		if not doc.no_of_guests and vp_people:
			doc.no_of_guests = vp_people

	if cint(doc.factory_tour_required):
		if not doc.tour_date:
			doc.tour_date = vp_date or nowdate()
		if not doc.tour_start_time and getattr(visitor_pass, "expected_checkin", None):
			doc.tour_start_time = getattr(visitor_pass, "expected_checkin", None)

	if cint(doc.buggy_required) and not doc.buggy_datetime:
		doc.buggy_datetime = vp_checkin

	if cint(doc.greeting_required) and not doc.greeting_delivery_time and vp_checkin:
		from frappe.utils import add_to_date
		doc.greeting_delivery_time = add_to_date(vp_checkin, minutes=-30)

	return doc


def _compute_overall_hospitality_status(request_doc):
	# Individual per-service statuses were removed. Overall status now derives
	# from the Hospitality Request's main `status` field plus whether any
	# arrangement was requested at all.
	required_flags = ("cab_required", "hotel_required", "factory_tour_required", "buggy_required", "greeting_required")
	any_required = any(cint(getattr(request_doc, f, 0)) for f in required_flags)
	has_food_or_room = cint(getattr(request_doc, "meal_required", 0)) or getattr(request_doc, "conference_room", None)

	if not any_required and not has_food_or_room:
		return "Not Required"

	main_status = (getattr(request_doc, "status", None) or "Pending").strip()
	if main_status == "Completed":
		return "Completed"
	if main_status == "Cancelled":
		return "Cancelled"
	if main_status in ("In Progress", "Confirmed", "Served", "Delivered", "Checked In"):
		return "In Progress"
	return "Pending"


@frappe.whitelist(allow_guest=True)
def get_hospitality_meal_plan(visit_date=None, expected_checkin=None, expected_checkout=None):
	return derive_hospitality_meal_plan(
		frappe._dict(
			{
				"visit_date": visit_date,
				"expected_checkin": expected_checkin,
				"expected_checkout": expected_checkout,
			}
		)
	)


def derive_health_screening_status(temperature=None, symptoms_flag=0):
	temperature = flt(temperature or 0)
	if temperature >= HEALTH_DENY_TEMPERATURE:
		return "Denied Entry"

	if temperature >= HEALTH_REVIEW_TEMPERATURE or cint(symptoms_flag):
		return "Needs Review"

	return "Cleared"


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


def sync_health_screening(visitor_pass_name, security_log=None):
	if not visitor_pass_name or not security_log:
		return None

	if security_log.event_type != "Check-In":
		return None

	screening_status = derive_health_screening_status(
		temperature=getattr(security_log, "temperature", None),
		symptoms_flag=getattr(security_log, "symptoms_flag", 0),
	)
	screening_name = getattr(security_log, "health_screening", None) or frappe.db.get_value(
		"Health Screening", {"security_log": security_log.name}, "name"
	)
	doc = (
		frappe.get_doc("Health Screening", screening_name)
		if screening_name
		else frappe.new_doc("Health Screening")
	)
	doc.visitor_pass = visitor_pass_name
	doc.security_log = security_log.name
	doc.screened_on = (
		getattr(security_log, "check_in_date_time", None)
		or getattr(security_log, "modified", None)
		or now_datetime()
	)
	doc.screened_by = getattr(security_log, "security_officer", None)
	doc.temperature = getattr(security_log, "temperature", None)
	doc.symptoms_flag = cint(getattr(security_log, "symptoms_flag", 0))
	doc.symptoms_details = getattr(security_log, "symptoms_details", None)
	doc.screening_status = screening_status
	doc.screening_notes = "\n".join(
		filter(
			None,
			[
				getattr(security_log, "remarks", None),
				getattr(security_log, "verification_notes", None),
			],
		)
	)
	doc.restricted_entry = cint(screening_status == "Denied Entry")
	doc.location = getattr(security_log, "visited_area", None) or getattr(security_log, "gate_name", None)

	if doc.is_new():
		doc.insert(ignore_permissions=True)
	else:
		doc.save(ignore_permissions=True)

	frappe.db.set_value(
		"Visitor Pass",
		visitor_pass_name,
		{
			"last_health_screening": doc.name,
			"health_screening_status": doc.screening_status,
		},
		update_modified=False,
	)
	frappe.db.set_value(
		"Security Log",
		security_log.name,
		{
			"health_screening": doc.name,
			"health_screening_status": doc.screening_status,
		},
		update_modified=False,
	)
	log_visitor_event(
		visitor_pass_name,
		"Health Screening",
		event_status=doc.screening_status,
		source_doctype="Health Screening",
		source_name=doc.name,
		details={
			"temperature": doc.temperature,
			"symptoms_flag": doc.symptoms_flag,
		},
	)
	return doc.name


def _get_active_contact_trace(visitor_pass_name):
	records = frappe.get_all(
		"Contact Trace Record",
		filters={"visitor_pass": visitor_pass_name, "status": "Active"},
		fields=["name"],
		order_by="modified desc",
		limit=1,
	)
	return records[0].name if records else None


def _close_active_contact_trace(visitor_pass_name, event_time, notes=None):
	active_name = _get_active_contact_trace(visitor_pass_name)
	if not active_name:
		return None

	doc = frappe.get_doc("Contact Trace Record", active_name)
	doc.time_out = event_time
	doc.status = "Closed"
	if notes:
		doc.notes = "\n".join(filter(None, [doc.notes, notes]))
	doc.save(ignore_permissions=True)
	return doc.name


def sync_contact_trace(visitor_pass_name, security_log=None):
	if not visitor_pass_name or not security_log:
		return None

	if security_log.event_type not in {"Check-In", "Gate Transfer", "Check-Out"}:
		return None

	event_time = (
		getattr(security_log, "check_in_date_time", None)
		or getattr(security_log, "check_out_date_time", None)
		or getattr(security_log, "modified", None)
		or now_datetime()
	)
	event_time = get_datetime(event_time)

	if security_log.event_type == "Check-Out":
		_close_active_contact_trace(visitor_pass_name, event_time, notes="Visitor checked out")
		frappe.db.set_value(
			"Visitor Pass", visitor_pass_name, {"current_location": None}, update_modified=False
		)
		return None

	visited_area = getattr(security_log, "visited_area", None) or getattr(security_log, "gate_name", None)
	if security_log.event_type == "Gate Transfer":
		_close_active_contact_trace(visitor_pass_name, event_time, notes="Gate transfer recorded")

	if not visited_area:
		return None

	record_name = frappe.db.get_value(
		"Contact Trace Record", {"security_log": security_log.name}, "name"
	)
	doc = (
		frappe.get_doc("Contact Trace Record", record_name)
		if record_name
		else frappe.new_doc("Contact Trace Record")
	)
	doc.visitor_pass = visitor_pass_name
	doc.security_log = security_log.name
	doc.visited_area = visited_area
	doc.time_in = doc.time_in or event_time
	doc.status = "Active"
	doc.exposure_risk = (
		"High"
		if flt(getattr(security_log, "temperature", 0) or 0) >= HEALTH_REVIEW_TEMPERATURE
		or cint(getattr(security_log, "symptoms_flag", 0))
		else "Low"
	)
	doc.notes = "\n".join(
		filter(
			None,
			[
				getattr(security_log, "remarks", None),
				getattr(security_log, "verification_notes", None),
			],
		)
	)

	if doc.is_new():
		doc.insert(ignore_permissions=True)
	else:
		doc.save(ignore_permissions=True)

	frappe.db.set_value(
		"Visitor Pass",
		visitor_pass_name,
		{"current_location": visited_area},
		update_modified=False,
	)
	return doc.name


def get_last_known_location(visitor_pass_name):
	active_name = _get_active_contact_trace(visitor_pass_name)
	if active_name:
		return frappe.db.get_value("Contact Trace Record", active_name, "visited_area")

	record = frappe.get_all(
		"Contact Trace Record",
		filters={"visitor_pass": visitor_pass_name},
		fields=["visited_area"],
		order_by="modified desc",
		limit=1,
	)
	if record:
		return record[0].visited_area

	return frappe.db.get_value("Visitor Pass", visitor_pass_name, "current_location")


def _get_latest_security_log(visitor_pass_name, event_types=None):
	filters = {"visitor_pass": visitor_pass_name}
	if event_types:
		filters["event_type"] = ["in", event_types]

	names = frappe.get_all(
		"Security Log",
		filters=filters,
		fields=["name"],
		order_by="creation desc",
		limit=1,
	)
	return frappe.get_doc("Security Log", names[0].name) if names else None


def generate_emergency_muster_records(emergency_event):
	if not emergency_event or emergency_event.status != "Active":
		return 0

	active_visitors = frappe.get_all(
		"Visitor Pass",
		filters={"status": "Checked-In"},
		fields=["name", "person_to_visit", "last_health_screening"],
	)

	count = 0
	for visitor in active_visitors:
		muster_name = frappe.db.get_value(
			"Evacuation Muster",
			{"emergency_event": emergency_event.name, "visitor_pass": visitor.name},
			"name",
		)
		doc = (
			frappe.get_doc("Evacuation Muster", muster_name)
			if muster_name
			else frappe.new_doc("Evacuation Muster")
		)
		doc.emergency_event = emergency_event.name
		doc.visitor_pass = visitor.name
		doc.employee_host = visitor.person_to_visit
		doc.assembly_point = emergency_event.assembly_point
		doc.last_known_location = get_last_known_location(visitor.name)
		doc.health_screening = visitor.last_health_screening
		if not doc.accounted_status:
			doc.accounted_status = "Pending"

		if doc.is_new():
			doc.insert(ignore_permissions=True)
		else:
			doc.save(ignore_permissions=True)

		count += 1

	frappe.db.set_value(
		"Emergency Event",
		emergency_event.name,
		{
			"muster_count": count,
			"muster_generated_on": now_datetime(),
		},
		update_modified=False,
	)
	return count


def sync_compliance_check(visitor_pass_name, security_log=None):
	if not visitor_pass_name:
		return None

	visitor_pass = frappe.get_doc("Visitor Pass", visitor_pass_name)
	hospitality_status = None
	if visitor_pass.hospitality_request:
		hospitality_status = frappe.db.get_value(
			"Hospitality Request", visitor_pass.hospitality_request, "status"
		)
	health_screening_status = None
	if getattr(visitor_pass, "last_health_screening", None):
		health_screening_status = frappe.db.get_value(
			"Health Screening", visitor_pass.last_health_screening, "screening_status"
		)

	if not security_log:
		security_log = _get_latest_security_log(visitor_pass.name)

	verification_log = (
		security_log if security_log and security_log.event_type == "Check-In" else None
	)
	if not verification_log:
		verification_log = _get_latest_security_log(visitor_pass.name, ["Check-In"])

	id_verified = cint(getattr(verification_log, "id_proof_match", 0)) if verification_log else 0
	pass_photo_verified = cint(getattr(verification_log, "pass_photo_match", 0)) if verification_log else 0
	gate_photo_captured = 1 if verification_log and getattr(verification_log, "photo_at_gate", None) else 0
	items_verified = cint(
		getattr(visitor_pass, "items_verified", 0) or getattr(visitor_pass, "all_items_verified", 0)
	)
	hospitality_closed = 1
	if visitor_pass.hospitality_request:
		hospitality_closed = cint((hospitality_status or "Pending") in COMPLIANCE_OK_STATUSES)

	no_show = cint(getattr(visitor_pass, "no_show", 0))
	verification_duration = (
		getattr(verification_log, "verification_duration", 0) if verification_log else 0
	)

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
	if visitor_pass.actual_checkin and not getattr(visitor_pass, "last_health_screening", None):
		missing_requirements.append("Health screening missing")
	if health_screening_status and health_screening_status not in HEALTH_SCREENING_OK_STATUSES:
		missing_requirements.append(f"Health screening status: {health_screening_status}")

	if no_show:
		compliance_status = "No Show"
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
	doc.no_show = no_show
	doc.verification_duration = verification_duration or 0
	doc.health_screening_status = health_screening_status
	doc.current_location = getattr(visitor_pass, "current_location", None)
	doc.missing_requirements = "\n".join(missing_requirements)
	doc.exception_reason = getattr(security_log, "exception_reason", None) if security_log else None
	doc.last_security_log = getattr(security_log, "name", None) if security_log else None
	doc.last_checked_on = now_datetime()

	if doc.is_new():
		doc.insert(ignore_permissions=True)
	else:
		doc.save(ignore_permissions=True)

	return doc.name
