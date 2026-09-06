import frappe
from frappe import _

from visitormanagement.visitor_management import settings as vms_settings

from frappe.model.workflow import apply_workflow
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, flt, get_datetime, get_time, getdate, now_datetime, nowdate


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
# Meal windows are configured in VMS Settings (VMS Meal Window child table).
# `settings.meal_windows()` falls back to the original Breakfast/Lunch/Dinner
# slots when a site has not customised them.
DOUBLE_MEAL_TYPES = {
	("Breakfast", "Lunch"): "Breakfast + Lunch",
	("Breakfast", "Dinner"): "Breakfast + Dinner",
	("Lunch", "Dinner"): "Lunch + Dinner",
}


def normalize_visitor_pass(doc):
	if not doc.status:
		doc.status = "Draft"

	# VMS Settings carries a default check-out time; apply it when the caller did
	# not supply one (portal/API/import paths) instead of failing the mandatory
	# field. This setting previously had no consumer at all.
	if not getattr(doc, "expected_checkout", None):
		fallback = vms_settings.default_checkout_time()
		if fallback:
			doc.expected_checkout = fallback

	if not doc.request_channel:
		doc.request_channel = "Desk"

	# Line 1 (title in Link dropdown) — just the visitor name.
	doc.visitor_summary = doc.visitor_full_name or "Unnamed"

	# Supplier-layout types default to a meeting visit; keyed on the layout so a
	# site's own vendor type behaves the same without being named "Supplier".
	if getattr(doc, "visitor_type_layout", None) == "Supplier" and not doc.supplier_visit_mode:
		doc.supplier_visit_mode = "Meeting"

	if doc.actual_checkin:
		doc.no_show = 0
	elif should_mark_no_show(doc):
		doc.no_show = 1

	if doc.status != "Checked-In" and getattr(doc, "current_location", None):
		doc.current_location = None

	# service_time preservation stays channel-gated as before — recomputing it on
	# every Desk save is intentional so a rescheduled visit gets a fresh service
	# slot; only a repeat Portal save (the visitor revisiting their own request)
	# keeps the slot they were already given.
	preserve_hospitality_choices = bool(
		getattr(doc, "request_channel", None) == "Portal"
		and not doc.is_new()
	)
	# meal_type is different: this used to reuse preserve_hospitality_choices,
	# which meant a Desk-created pass (request_channel != "Portal") had ANY
	# receptionist-typed Meal Type overwritten by the derived value on every
	# single save, including the very first one — the channel a request came
	# in on says nothing about whether a human just chose this field. What
	# actually matters is whether the value in front of us differs from what
	# was already on record, which get_doc_before_save() tells us for an
	# existing document; a brand-new document has no "before" to compare
	# against, so any non-blank value here can only have come from the form.
	apply_hospitality_meal_plan(
		doc,
		preserve_existing=preserve_hospitality_choices,
		honor_manual_meal_type=_field_was_manually_set(doc, "meal_type"),
	)


def _field_was_manually_set(doc, fieldname, ignore_as_default=()):
	"""True if `fieldname` looks like a human just chose it on this save,
	rather than a value auto-copied on some earlier save simply surviving
	untouched. Used to decide whether a fetched/derived field (meal_type,
	special_diet) should override what is already on the document.

	A brand-new document has no "before" snapshot to diff against, so any
	non-blank value here can only have come from the form the user just
	submitted — EXCEPT for a value listed in `ignore_as_default`. That escape
	hatch exists because special_diet's Select options start with the literal
	string "None" rather than a blank first option (unlike meal_type), so the
	client sets doc.special_diet = "None" on a brand-new form the user never
	touched at all — confirmed live: an untouched new Hospitality Request
	reached the server with special_diet already "None", which would
	otherwise have looked exactly like a deliberate choice and blocked the
	Visitor Pass's value from ever flowing in. `ignore_as_default` only
	applies to the is_new() branch: on an existing document a change *back*
	to "None" is a real, diffable edit and is honoured below regardless.

	For an existing document, get_doc_before_save() (populated by
	check_if_latest() before validate() runs, for both Visitor Pass and
	Hospitality Request via the normal .save()/.insert() path) gives the row
	as it stood before this save's changes were applied — if the field
	differs from that, the caller changed it just now.
	"""
	value = getattr(doc, fieldname, None)
	if not value:
		return False
	if doc.is_new():
		return value not in ignore_as_default
	before = doc.get_doc_before_save()
	if not before:
		# No prior snapshot to diff against (e.g. called outside a normal
		# .save()/.insert() flow) — treat a present value as intentional
		# rather than silently discarding it.
		return True
	return value != before.get(fieldname)


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

	# Read-then-insert is a time-of-check/time-of-use race. Two saves arriving
	# together — a portal submission alongside a desk edit, or a double-clicked
	# workflow action — both find nothing here and both insert, leaving one pass
	# with two hospitality requests (HOSP-…-130826 and HOSP-…-130826-2, Frappe
	# suffixing the naming collision). `FOR UPDATE` takes a gap lock on the
	# visitor_pass index range, so the second transaction waits and then sees the
	# first one's row. This is the same guard `_validate_duplicate_pass` already
	# uses on Visitor Pass for exactly this failure.
	#
	# The index on `visitor_pass` is what keeps the lock narrow — without it
	# InnoDB escalates to locking the whole table on every save.
	request_name = visitor_pass.hospitality_request
	if not request_name:
		locked = frappe.db.sql(
			"""
			SELECT name FROM `tabHospitality Request`
			WHERE visitor_pass = %s
			LIMIT 1
			FOR UPDATE
			""",
			visitor_pass.name,
		)
		request_name = locked[0][0] if locked else None
	is_new_request = not request_name
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

	# Once the parent Visitor Pass is Approved (or beyond), move this request out
	# of Draft and into the Hospitality Manager's queue. Done as its own step,
	# AFTER the save above has already committed, and through the workflow's real
	# "Submit" transition (`apply_workflow`) rather than a raw field assignment —
	# see the long comment in `populate_hospitality_request_from_pass` for why a
	# raw assignment inside that save used to throw and roll back the parent's
	# approval/rejection.
	#
	# `apply_workflow` is role-checked against whoever is currently saving the
	# Visitor Pass, who is not necessarily the Hospitality Request's owner and may
	# not even hold the "Employee" role the Submit transition requires. That is a
	# legitimate way for this to fail (not a bug in this function), so it is
	# caught and logged rather than allowed to undo the Visitor Pass approval that
	# triggered it. `status` above already reflects the real-world outcome
	# regardless of whether this transition succeeds; a request left behind here
	# still needs a human to Submit it from the Hospitality Request itself.
	current_wf = getattr(doc, "workflow_state", None) or "Draft"
	vp_status = getattr(visitor_pass, "status", None)
	if current_wf == "Draft" and vp_status in ("Approved", "Items Verified", "Checked-In", "Checked-Out"):
		try:
			apply_workflow(doc, "Submit")
		except Exception as exc:
			frappe.log_error(
				f"Hospitality Request {doc.name} auto-promotion to Pending Approval failed "
				f"for Visitor Pass {visitor_pass.name}: {exc}",
				"VMS Hospitality Auto-Promote",
			)

	if visitor_pass.hospitality_request != doc.name:
		visitor_pass.db_set("hospitality_request", doc.name, update_modified=False)

	# Surface what just happened so the host/reception isn't surprised that a
	# hospitality doc magically exists. Only on creation, not on every save —
	# avoids alert spam on subsequent edits.
	if is_new_request:
		try:
			frappe.msgprint(
				_("Hospitality Request {0} was created from this pass — the Hospitality Manager will see it in their queue.").format(
					frappe.bold(doc.name)
				),
				title=_("Hospitality Arranged"),
				indicator="green",
				alert=True,
			)
		except Exception:
			# Background contexts (workflow_action) sometimes lack a request — ignore.
			pass

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

		# Move the CRB into the Facility Manager's queue as soon as the parent VP
		# is confirmed. Without this, auto-created CRBs sit in Draft forever and
		# the FM never sees an Approve/Reject button. Use save() (not db_set) so
		# the Notification 'CRB Pending Approval' fires and the FM gets emailed.
		vp_status = getattr(visitor_pass, "status", None)
		current_wf = getattr(booking, "workflow_state", None) or "Draft"
		if current_wf == "Draft" and vp_status in (
			"Approved", "Items Verified", "Checked-In", "Checked-Out"
		):
			booking.workflow_state = "Pending Approval"
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
	# Reduce visit_time to a HH:MM:SS string and strip tzinfo so the result is
	# always naive — meal-window slots (built from the time-only MEAL_WINDOWS
	# constants) are naive, and mixing naive + tz-aware crashes the comparison
	# in `_overlaps_time_window` with `can't compare offset-naive and offset-aware`.

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
	for meal_label, slot_start, slot_end in vms_settings.meal_windows():
		slot_start_dt = _combine_visit_datetime(visit_date, slot_start)
		slot_end_dt = _combine_visit_datetime(visit_date, slot_end)
		if _overlaps_time_window(start_dt, end_dt, slot_start_dt, slot_end_dt):
			applicable_meals.append(meal_label)
			if not first_service_time:
				first_service_time = slot_start_dt

	meal_required = 1 if applicable_meals else 0
	# Meal windows are admin-configurable, so any combination is reachable, but
	# DOUBLE_MEAL_TYPES only names the three built-in pairs. Joining the labels
	# for anything else produced values like "Lunch + Snacks" that are not
	# options on the meal_type Select, and Frappe refused the save outright —
	# a site with a fourth meal window could not record an ordinary 10:00-17:00
	# visit for any visitor type. "All Day" is the catch-all the field already
	# offers. `>= 3` rather than `== 3` so a fifth window cannot fall through to
	# the else branch and leave meal_required set with no meal named.
	if len(applicable_meals) >= 3:
		derived_meal_type = "All Day"
		hospitality_type = "Full Day"
	elif len(applicable_meals) == 2:
		derived_meal_type = DOUBLE_MEAL_TYPES.get(tuple(applicable_meals)) or "All Day"
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


def apply_hospitality_meal_plan(doc, preserve_existing=False, honor_manual_meal_type=False):
	meal_plan = derive_hospitality_meal_plan(doc)
	existing_meal_type = getattr(doc, "meal_type", None)
	existing_service_time = getattr(doc, "service_time", None)
	# Respect user's manual selection — only auto-set if currently unchecked.
	user_wants_meal = cint(getattr(doc, "meal_required", 0))
	effective_meal_required = user_wants_meal or meal_plan["meal_required"]
	doc.meal_required = effective_meal_required
	# Keep meal_plan-derived values in sync for downstream logic
	meal_plan["meal_required"] = effective_meal_required
	# meal_type: an explicit human choice (honor_manual_meal_type, from
	# _meal_type_was_manually_set) always wins over the derived value, on top
	# of the older channel-gated preserve_existing carve-out. Without either
	# flag, or when meal is no longer required at all, fall back to what the
	# visit window derives.
	# `honor_manual_meal_type` only catches the save on which the human actually
	# changed the field, because it works by diffing against the before-save
	# snapshot. That is not enough on its own: on the NEXT save — a receptionist
	# correcting a phone number, a workflow transition, anything — meal_type is
	# unchanged since before-save, so the diff says "not manual" and the derived
	# value overwrites the choice. That silently reintroduced the original bug
	# from the second save onward (proven: an explicit "Dinner" became "All Day"
	# after resaving only `remarks`).
	#
	# So a stored value that DIVERGES from what the derivation would produce is
	# also treated as deliberate: the derivation is a suggestion, and the only
	# way a row can hold something else is that a human put it there. When the
	# two agree, recomputing is a no-op anyway, so nothing is lost by letting
	# the derived value through.
	diverged_from_derived = bool(
		not doc.is_new() and existing_meal_type and existing_meal_type != meal_plan["meal_type"]
	)
	doc.meal_type = (
		existing_meal_type
		if effective_meal_required
		and existing_meal_type
		and (preserve_existing or honor_manual_meal_type or diverged_from_derived)
		else meal_plan["meal_type"]
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
	# special_diet is a plain editable Select on this form (hidden=0, read_only=0)
	# — it used to be overwritten from the Visitor Pass unconditionally, so a
	# Hospitality Manager's own pick (e.g. "Vegetarian") never survived a save.
	# Mirror the Visitor Pass value only when nothing was just chosen here,
	# using the same manual-vs-stale distinction as meal_type above.
	if not _field_was_manually_set(doc, "special_diet", ignore_as_default={"None"}):
		doc.special_diet = getattr(visitor_pass, "special_diet", None)
	doc.snacks_required = cint(getattr(visitor_pass, "refreshments_required", 0))
	doc.tea_coffee_required = cint(getattr(visitor_pass, "refreshments_required", 0))
	doc.conference_room = getattr(visitor_pass, "conference_room", None)
	doc.seating_capacity = getattr(visitor_pass, "number_of_people", None)
	doc.service_time = meal_plan["service_time"]
	# This function must NOT touch `workflow_state`. It used to force Draft ->
	# "Pending Approval" here whenever the parent pass was Approved, but assigning
	# the field and letting the following `doc.save()` validate it is not a real
	# workflow transition — Frappe's `validate_workflow` only recognises a hop that
	# matches a `Workflow Transition` role-checked against the CURRENT session
	# user, so it refused the assignment as an unrecognised jump
	# (WorkflowPermissionError) and, because this function runs inside every save
	# of this doctype (including the Hospitality Request's own `validate()`, and a
	# rejected pass's `doc.save()` in `ensure_hospitality_request`), that throw
	# rolled back whatever outer save triggered it — once making Reapply on a
	# rejected pass impossible (Reapply itself sets workflow_state to "Draft" and
	# saves; this code immediately rewrote it to "Pending Approval" mid-transition
	# and Frappe compared the real pre-save state, "Rejected", against that
	# mutated target and threw), and separately making it impossible to reject any
	# pass that had requested a meal, a room or any arrangement.
	#
	# The fix keeps this function to pure field population. The one place that is
	# allowed to promote a Hospitality Request out of Draft is
	# `ensure_hospitality_request`, below, and only via `apply_workflow` (a real,
	# role-checked transition) performed AFTER this document's own save has
	# already committed — so a promotion that the approving user isn't entitled to
	# (e.g. they lack the "Employee" role the Hospitality Request workflow's
	# Submit transition requires) is caught and logged there instead of blowing up
	# here and rolling back the Visitor Pass approval that triggered it. The
	# outcome of a rejected/approved parent is recorded on `status` below instead
	# — this document's own field, which needs no workflow transition.

	if sync_management_fields:
		doc.assigned_staff = getattr(visitor_pass, "food_dept_staff_assigned", None)
		doc.status = HOSPITALITY_REQUEST_STATUS_FROM_PASS.get(
			getattr(visitor_pass, "food_status", None), "Pending"
		)
		if getattr(visitor_pass, "status", None) == "Rejected":
			doc.status = "Cancelled"
		doc.notes = "\n".join(
			note
			for note in [
				getattr(visitor_pass, "hospitality_notes", None),
				getattr(visitor_pass, "refreshment_notes", None),
			]
			if note
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
	any_required = any(cint(getattr(request_doc, f, 0)) for f in ARRANGEMENT_REQUIRED_FIELDS)
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
@rate_limit(limit=60, seconds=60 * 60)
def get_hospitality_meal_plan(visit_date=None, expected_checkin=None, expected_checkout=None):
	"""Preview the meals a visit would qualify for. Reachable without login.

	The portal calls this on every change to the visit times, so it is both
	anonymous and chatty. Inputs are parsed rather than trusted: a malformed
	date previously reached dateutil and surfaced as a 500 with a traceback,
	which told an anonymous caller more about the stack than it should and
	turned a typo into an error-log entry.
	"""
	return derive_hospitality_meal_plan(
		frappe._dict(
			{
				"visit_date": _parse_date_arg(visit_date, "Visit Date"),
				"expected_checkin": _parse_time_arg(expected_checkin, "Expected Check-In"),
				"expected_checkout": _parse_time_arg(expected_checkout, "Expected Check-Out"),
			}
		)
	)


def _parse_date_arg(value, label):
	if not value:
		return None
	try:
		return getdate(value)
	except Exception:
		frappe.throw(_("{0} is not a valid date.").format(_(label)), frappe.ValidationError)


def _parse_time_arg(value, label):
	if not value:
		return None
	try:
		return get_time(value)
	except Exception:
		frappe.throw(_("{0} is not a valid time.").format(_(label)), frappe.ValidationError)


# Keys in an event's `details` that hold a record ID rather than something a
# person recognises. The log is read by security staff, so the ID alone is not
# an answer to "who was on the gate".
_DETAIL_LINKS = {
	"security_officer": ("Employee", "employee_name"),
	"gate_verified_by": ("Employee", "employee_name"),
	"assigned_staff": ("Employee", "employee_name"),
}

# Keys whose humanised label reads better spelled out.
_DETAIL_LABELS = {
	"gate_name": "Gate",
	"visited_area": "Visited Area",
	"exception_reason": "Exception",
	"grace_hours": "Grace (hours)",
}


def _format_event_details(details):
	"""Render an event's details as something a person can read.

	These were stored with `frappe.as_json`, so the Details section of every
	Visitor Event Log showed the raw payload — braces, quoted keys, and a
	`"exception_reason": null` line for the common case where nothing went
	wrong. It also printed `"security_officer": "HR-EMP-00001"`, which is an
	internal identifier, not a person.

	The structured data is not lost by writing prose here: every log carries
	`source_doctype`/`source_name` back to the document the event came from,
	and that document still holds the fields themselves.
	"""
	if not details:
		return ""
	if isinstance(details, str):
		return details

	lines = []
	for key, value in details.items():
		# An empty value means "not applicable to this event", which is noise in
		# a log meant to be skimmed.
		if value is None or value == "":
			continue

		label = _DETAIL_LABELS.get(key) or key.replace("_", " ").title()
		text = value

		link = _DETAIL_LINKS.get(key)
		if link:
			doctype, display_field = link
			display = frappe.db.get_value(doctype, value, display_field)
			# The name only. `HR-EMP-00060` is an internal identifier — it means
			# nothing to the person reading the log, and printing it next to the
			# name just puts the code back in front of them. The Employee record
			# is still reachable through the linked source document.
			text = display or value

		lines.append(f"{label}: {text}")

	return "\n".join(lines)


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
		"details": _format_event_details(details),
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
		doc.notes = "\n".join(part for part in [doc.notes, notes] if part)
	doc.save(ignore_permissions=True)
	return doc.name


# Fallback only — the live value comes from VMS Settings (fever_threshold_c()).
# Health policy on what counts as "fever" differs by site and authority (some
# use 38.0C, some record Fahrenheit), so this must not stay a bare literal.
DEFAULT_FEVER_THRESHOLD_C = 37.5


def _fever_threshold_c():
	"""vms_settings.fever_threshold_c(), read defensively.

	That accessor may not exist yet on a site mid-deploy (or in a test run
	against an older settings.py), and this exposure-risk calculation must never
	break because of it — fall back to the previous hardcoded value instead.
	"""
	getter = getattr(vms_settings, "fever_threshold_c", None)
	if not callable(getter):
		return DEFAULT_FEVER_THRESHOLD_C
	try:
		value = flt(getter())
	except Exception:
		return DEFAULT_FEVER_THRESHOLD_C
	return value if value else DEFAULT_FEVER_THRESHOLD_C


def sync_contact_trace(visitor_pass_name, security_log=None):
	if not visitor_pass_name or not security_log:
		return None

	if security_log.event_type not in {"Check-In", "Gate Transfer", "Check-Out"}:
		return None

	# Read the timestamp that belongs to the event being recorded. This used to
	# take `check_in_date_time` first whatever the event was, so a Check-Out that
	# also carried a check-in time closed the trace at the moment the visitor
	# *arrived*. On a visit spanning midnight — in yesterday, out today — that
	# time_out precedes the record's own time_in, and Contact Trace Record
	# rightly refuses it, which blocked the check-out itself.
	#
	# The Desk form only fills the field matching the event, so this did not
	# surface there; an API caller or a hand-edited log reaches it.
	if security_log.event_type == "Check-Out":
		event_time = getattr(security_log, "check_out_date_time", None) or getattr(
			security_log, "check_in_date_time", None
		)
	else:
		event_time = getattr(security_log, "check_in_date_time", None) or getattr(
			security_log, "check_out_date_time", None
		)
	event_time = get_datetime(
		event_time or getattr(security_log, "modified", None) or now_datetime()
	)

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
		if flt(getattr(security_log, "temperature", 0) or 0) >= _fever_threshold_c()
		or cint(getattr(security_log, "symptoms_flag", 0))
		else "Low"
	)
	doc.notes = "\n".join(
		note
		for note in [
			getattr(security_log, "remarks", None),
			getattr(security_log, "verification_notes", None),
		]
		if note
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


