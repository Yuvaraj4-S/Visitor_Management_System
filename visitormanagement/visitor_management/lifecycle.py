from datetime import timedelta

import frappe
from frappe import _
from frappe.model.workflow import apply_workflow
from frappe.rate_limiter import rate_limit
from frappe.utils import (
	cint,
	escape_html,
	flt,
	get_datetime,
	get_link_to_form,
	get_time,
	getdate,
	now_datetime,
	nowdate,
)

from visitormanagement.visitor_management import settings as vms_settings

# The Hospitality Request owns its fulfilment status; the pass only mirrors it
# (as `food_status`). There is deliberately no map in the other direction: the
# request used to be re-derived from that mirror on every pass save, so a status
# the pass had once been told ("Cancelled", when the pass was rejected) came back
# to the request for good.
VISITOR_PASS_FOOD_STATUS_FROM_REQUEST = {
	"Pending": "Pending",
	"Confirmed": "Ordered",
	"Served": "Served",
	"Completed": "Completed",
	"Cancelled": "Cancelled",
}
# Pass statuses from which the visit is going ahead (approved, or already at the gate).
PASS_CONFIRMED_STATUSES = ("Approved", "Items Verified", "Checked-In", "Checked-Out")
# Pass statuses in which the visit is off, so nothing is prepared or held for it.
PASS_CALLED_OFF_STATUSES = ("Rejected", "Cancelled")
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
		getattr(doc, "request_channel", None) == "Portal" and not doc.is_new()
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
	touched at all — an untouched new Hospitality Request
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
	"""Bring the pass's Hospitality Request and room booking in line with the pass."""
	if not visitor_pass.name:
		return None

	request_name = _sync_hospitality_request(visitor_pass)
	# The room follows the pass whether or not anything else was asked for: a
	# room the host has since removed, or a pass that was rejected, must stop
	# holding its slot.
	ensure_conference_room_booking(visitor_pass)
	return request_name


def _sync_hospitality_request(visitor_pass):
	requires_service = any(
		[
			cint(getattr(visitor_pass, "meal_required", 0)),
			cint(getattr(visitor_pass, "refreshments_required", 0)),
			getattr(visitor_pass, "conference_room", None),
		]
		+ [cint(getattr(visitor_pass, f, 0)) for f in ARRANGEMENT_REQUIRED_FIELDS]
	)
	# Nothing requested and no request on file: nothing to do. A request that
	# already exists is still synced below, so a meal the host has since
	# unticked disappears from it instead of staying on the kitchen's list.
	if not requires_service and not visitor_pass.hospitality_request:
		return None

	# A request the Hospitality Manager cancelled is finished: it is never
	# written to again, and the pass must not keep linking to it (Frappe refuses
	# to save a document that links to a cancelled one).
	request_name = visitor_pass.hospitality_request
	if request_name and cint(frappe.db.get_value("Hospitality Request", request_name, "docstatus")) == 2:
		request_name = None

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
	if not request_name:
		locked = frappe.db.sql(
			"""
			SELECT name, docstatus FROM `tabHospitality Request`
			WHERE visitor_pass = %s
			ORDER BY docstatus ASC, creation DESC
			LIMIT 1
			FOR UPDATE
			""",
			visitor_pass.name,
		)
		if locked and cint(locked[0][1]) == 2:
			# Every request this pass had was cancelled by the Hospitality Manager.
			# Raising a fresh one here would put back what they called off; Amend
			# on the cancelled request is how it returns.
			return None
		request_name = locked[0][0] if locked else None
	is_new_request = not request_name
	if request_name:
		doc = frappe.get_doc("Hospitality Request", request_name)
	else:
		doc = frappe.new_doc("Hospitality Request")
		doc.visitor_pass = visitor_pass.name

	populate_hospitality_request_from_pass(doc, visitor_pass=visitor_pass)

	# This save runs inside the pass's own save, so whatever the request refuses
	# reaches the person saving the PASS. Its messages name a field ("Buggy pickup
	# ... is outside the visit window") but not the document it is on, which
	# reads as an error about the pass. Say where it comes from.
	messages_before = list(frappe.message_log)
	try:
		if doc.is_new():
			doc.insert(ignore_permissions=True)
		else:
			doc.save(ignore_permissions=True)
	except frappe.ValidationError as exc:
		frappe.local.message_log = messages_before
		frappe.throw(
			_("Hospitality Request {0} could not be brought in line with this pass: {1}").format(
				frappe.bold(doc.name) if doc.is_new() else get_link_to_form("Hospitality Request", doc.name),
				str(exc),
			),
			title=_("Hospitality Request Needs Attention"),
		)

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
	# triggered it — and the approver is told, because a request left behind here
	# still needs a human to Submit it from the Hospitality Request itself.
	current_wf = getattr(doc, "workflow_state", None) or "Draft"
	vp_status = getattr(visitor_pass, "status", None)
	if requires_service and current_wf == "Draft" and vp_status in PASS_CONFIRMED_STATUSES:
		messages_before = list(frappe.message_log)
		try:
			apply_workflow(doc, "Submit")
		except Exception as exc:
			frappe.local.message_log = messages_before
			frappe.log_error(
				title=f"Hospitality Request {doc.name} was not sent for approval",
				message=f"Visitor Pass {visitor_pass.name}: {exc}\n\n{frappe.get_traceback(with_context=True)}",
			)
			frappe.msgprint(
				_(
					"Hospitality Request {0} could not be sent to the Hospitality Manager "
					"automatically: {1}<br>Open it and use Actions > Submit."
				).format(get_link_to_form("Hospitality Request", doc.name), str(exc)),
				title=_("Hospitality Request Not Sent"),
				indicator="orange",
			)

	if visitor_pass.hospitality_request != doc.name:
		visitor_pass.db_set("hospitality_request", doc.name, update_modified=False)

	# Surface what just happened so the host/reception isn't surprised that a
	# hospitality doc magically exists. Only on creation, not on every save —
	# avoids alert spam on subsequent edits.
	if is_new_request:
		try:
			frappe.msgprint(
				_(
					"Hospitality Request {0} was created from this pass — the Hospitality Manager will see it in their queue."
				).format(frappe.bold(doc.name)),
				title=_("Hospitality Arranged"),
				indicator="green",
				alert=True,
			)
		except Exception:
			# Background contexts (workflow_action) sometimes lack a request — ignore.
			pass

	return doc.name


def call_off_pass_arrangements(visitor_pass):
	"""A cancelled visit keeps nothing booked: its meal request and room are called off.

	Approved (submitted) ones are cancelled — their own on_cancel moves `status`
	to Cancelled, which frees the room and takes the order off the kitchen's
	list. Ones still in Draft or awaiting approval are set to Rejected, so they
	leave the approver's queue; they could not be approved anyway once the pass
	is Cancelled (both refuse unless the pass is Approved or beyond).

	Called from VisitorPass.on_cancel, as whoever cancelled the pass — who need
	not hold the Hospitality / Facility Manager role these transitions need. A
	cancel does not run workflow validation (Document._validate is skipped for
	it), so the approved ones are cancelled directly with permissions ignored.
	Whatever has already happened — a meal served, a booking on an earlier day —
	is history and left alone.
	"""
	today_date = getdate(nowdate())
	for doctype, name in [
		*(
			("Hospitality Request", n)
			for n in frappe.get_all(
				"Hospitality Request",
				filters={
					"visitor_pass": visitor_pass.name,
					"docstatus": ("<", 2),
					"status": ("not in", ("Served", "Completed", "Cancelled")),
				},
				pluck="name",
			)
		),
		*(
			("Conference Room Booking", n)
			for n in frappe.get_all(
				"Conference Room Booking",
				filters={
					"visitor_pass": visitor_pass.name,
					"docstatus": ("<", 2),
					"booking_date": (">=", today_date),
				},
				pluck="name",
			)
		),
	]:
		_call_off(frappe.get_doc(doctype, name))


def _call_off(doc):
	"""Call off one Hospitality Request or Conference Room Booking (see above)."""
	if doc.docstatus == 1:
		doc.workflow_state = "Cancelled"
		doc.flags.ignore_permissions = True
		doc.cancel()
	elif doc.docstatus == 0 and doc.get("workflow_state") != "Rejected":
		values = {"workflow_state": "Rejected"}
		values["status"] = "Cancelled" if doc.doctype == "Hospitality Request" else "Rejected"
		doc.db_set(values)


def assert_submitted_through_approval(doc):
	"""A submit is only legitimate as the tail of the workflow's approving transition.

	For Hospitality Request and Conference Room Booking, `Approved` is the only
	`doc_status = 1` state of the workflow. Frappe checks a workflow transition
	only when the state field changes, and afterwards force-sets the state that
	matches the new docstatus (`set_workflow_state_on_action`). So a bare
	`frappe.client.submit` — or the list's bulk Submit, or a save carrying
	docstatus 1 — on a Draft lands on `Approved` with no transition ever
	evaluated: no approver role, no self-approval rule, nothing. Anyone holding
	`submit` on the DocType could approve their own request.

	What the workflow would have checked is checked here instead, against the
	state the document is stored in: it must be one an approving transition
	starts from, the user must hold a role that transition allows, and may not
	be approving their own document unless the transition permits it.

	Call from `before_submit`.
	"""
	workflow_name = doc.meta.get_workflow()
	if not workflow_name:
		return
	workflow = frappe.get_cached_doc("Workflow", workflow_name)
	approved_states = {state.state for state in workflow.states if cint(state.doc_status) == 1}
	stored_state = frappe.db.get_value(doc.doctype, doc.name, workflow.workflow_state_field)
	approvals = [
		transition
		for transition in workflow.transitions
		if transition.state == stored_state and transition.next_state in approved_states
	]
	if not approvals:
		frappe.throw(
			_(
				"This {0} is in <b>{1}</b> and has not been through approval. "
				"Use the workflow Actions button — it cannot be submitted directly."
			).format(_(doc.doctype), _(stored_state or "Draft")),
			title=_("Approval Required"),
		)

	user = frappe.session.user
	if user == "Administrator" or doc.flags.ignore_permissions:
		return

	roles = set(frappe.get_roles(user))
	allowed = [transition for transition in approvals if transition.allowed in roles]
	if not allowed:
		frappe.throw(
			_("Only {0} can approve this {1}.").format(
				", ".join(sorted({_(transition.allowed) for transition in approvals})), _(doc.doctype)
			),
			frappe.PermissionError,
			title=_("Not Permitted"),
		)
	if doc.owner == user and not any(cint(transition.allow_self_approval) for transition in allowed):
		frappe.throw(
			_("You raised this {0}, so it has to be approved by someone else.").format(_(doc.doctype)),
			frappe.PermissionError,
			title=_("Self Approval Not Allowed"),
		)


ROOM_BOOKING_SAVEPOINT = "vms_pass_room_booking"


def ensure_conference_room_booking(visitor_pass):
	"""Keep the pass's Conference Room Booking in step with the pass.

	The booking is the pass's own, raised and moved by the system as the pass
	moves, so none of it depends on what the person saving the pass may do to a
	booking:

	- a pass that is going ahead books its room for the real visit window;
	- once the pass is Approved the booking goes to the Facility Manager;
	- a Rejected pass, or one whose room was removed, releases the slot;
	- a pass that is submitted again asks for the room again, and the slot is
	  checked again.

	Whether the slot can be had is decided by the booking's own validation and
	nowhere else. Nothing is adjusted to make it fit: a visit outside the room's
	hours used to be moved to the opening time, which booked the room for hours
	the visitor was not there and left them without one when they were.
	"""
	booking_name = frappe.db.get_value(
		"Conference Room Booking",
		{"visitor_pass": visitor_pass.name, "docstatus": ["<", 2]},
		"name",
		order_by="creation desc",
	)
	pass_status = getattr(visitor_pass, "status", None)
	room = getattr(visitor_pass, "conference_room", None)

	if not room or pass_status in PASS_CALLED_OFF_STATUSES:
		# Nothing to hold. A booking on an earlier day is history and stays.
		if booking_name:
			booking = frappe.get_doc("Conference Room Booking", booking_name)
			if getdate(booking.booking_date) >= getdate(nowdate()):
				_call_off(booking)
		return None

	if booking_name:
		booking = frappe.get_doc("Conference Room Booking", booking_name)
	else:
		booking = frappe.new_doc("Conference Room Booking")
		booking.visitor_pass = visitor_pass.name

	# Before approval only this module rejects the booking (because the pass was
	# rejected), so a pass that is back is asking again. After approval a
	# rejection is the Facility Manager's answer, and it stands until the pass
	# asks for a different room.
	was_rejected = booking.get("workflow_state") == "Rejected"
	if was_rejected and visitor_pass.docstatus == 1 and booking.conference_room == room:
		return None

	# A room that cannot be had must not look like a failed save or approval:
	# the booking's validation calls frappe.throw, which queues its message for
	# the client before raising, and the pass does go through. So the queue is
	# snapshotted and, on failure, restored — and the person is told what
	# actually happened, in the booking's own words.
	#
	# The savepoint makes a refusal leave nothing behind: no half-written
	# booking, no reopened one, no number taken from the series.
	messages_before = list(frappe.message_log)
	frappe.db.savepoint(ROOM_BOOKING_SAVEPOINT)
	try:
		if was_rejected:
			booking.db_set({"workflow_state": "Draft", "status": "Draft"})

		booking.conference_room = room
		booking.meeting_title = _("Visitor Meeting — {0}").format(
			visitor_pass.visitor_full_name or visitor_pass.name
		)
		booking.booking_date = visitor_pass.visit_date
		booking.start_time = visitor_pass.expected_checkin
		booking.end_time = visitor_pass.expected_checkout
		booking.meeting_type = "External"
		booking.expected_attendees = cint(getattr(visitor_pass, "number_of_people", None)) or 1
		if not booking.booked_by:
			booking.booked_by = visitor_pass.person_to_visit

		if booking.is_new():
			booking.insert(ignore_permissions=True)
		else:
			booking.save(ignore_permissions=True)

		if (booking.get("workflow_state") or "Draft") == "Draft" and pass_status in PASS_CONFIRMED_STATUSES:
			_send_booking_for_approval(booking)

		frappe.db.release_savepoint(ROOM_BOOKING_SAVEPOINT)
		return booking.name
	except Exception as exc:
		frappe.db.rollback(save_point=ROOM_BOOKING_SAVEPOINT)
		frappe.local.message_log = messages_before
		frappe.log_error(
			title=f"Room not reserved for Visitor Pass {visitor_pass.name}",
			message=frappe.get_traceback(with_context=True),
		)

		# Say what is actually true of THIS pass. The old wording opened with
		# "The visit is approved", but this runs from on_update on every save of a
		# pass that is not a Draft — so a host saving a Pending pass was told their
		# visit was approved while the status badge in front of them said otherwise.
		lead = (
			_("The visit is approved, but")
			if pass_status in PASS_CONFIRMED_STATUSES
			else _("This pass is saved, but")
		)
		if isinstance(exc, frappe.ValidationError) and str(exc):
			# The booking's own reason: "QA-ROOM-B is available until 18:00:00",
			# "Maximum booking duration ...", "Time conflict with ...". It used to
			# be replaced by a clash lookup that did not exclude the pass's own
			# booking, so the approver was told the room was taken by the very
			# booking that had just been saved for this pass.
			frappe.msgprint(
				_(
					"{0} <b>{1}</b> is not reserved: {2}<br>"
					"Change the room or the visit time on this pass, or book a room from "
					"Conference Room Booking. Nothing else about this pass is affected."
				).format(lead, escape_html(room), str(exc)),
				title=_("Room Not Reserved"),
				indicator="orange",
			)
		else:
			frappe.msgprint(
				_(
					"{0} <b>{1}</b> could not be reserved. An administrator can see "
					"why in the Error Log; book the room manually in the meantime."
				).format(lead, escape_html(room)),
				title=_("Room Not Reserved"),
				indicator="orange",
			)
		return None


def _send_booking_for_approval(booking):
	"""Put the pass's room booking in the Facility Manager's queue.

	This is the system's step, taken because the pass was approved — it is not
	the pass approver "submitting" somebody else's booking. Saving the new state
	as the approver sent it through Frappe's workflow check, which first asks
	whether that user may READ the booking; an approver who is neither its owner
	nor its organiser may not, the PermissionError was swallowed, and the
	booking sat in Draft where no Facility Manager ever saw it.

	The booking has just been saved and validated, so only the state is written.
	A direct write runs no on_change, and that is what the "CRB Pending
	Approval" alert listens to — so it is run by hand with a before-image to
	diff against (the same shape as security_log.py's `_advance_pass`). An alert
	that fails is logged; the booking is in the queue either way.
	"""
	before = frappe.get_doc("Conference Room Booking", booking.name)
	values = {"workflow_state": "Pending Approval", "status": "Pending Approval"}
	frappe.db.set_value("Conference Room Booking", booking.name, values)
	booking.update(values)

	after = frappe.get_doc("Conference Room Booking", booking.name)
	after._doc_before_save = before
	after.add_comment("Workflow", _("Pending Approval"))

	messages_before = list(frappe.message_log)
	try:
		after.run_notifications("on_change")
	except Exception:
		frappe.log_error(
			title=f"Room booking {booking.name} approval alert failed",
			message=frappe.get_traceback(with_context=True),
		)
	finally:
		frappe.local.message_log = messages_before
	after.notify_update()


def sync_hospitality_to_pass(request_doc, status_only=False):
	"""Mirror the request onto its pass.

	`status_only` is for the saves that run no validation — cancelling, and a
	Hospitality Manager updating an approved request. There the request's copies
	of the pass's own fields (meal, room, arrangement flags) were not refreshed
	from the pass first, so writing them back would undo anything changed on the
	pass since. Only what the request owns is sent: its status and its staff.
	"""
	if not request_doc.visitor_pass:
		return

	pass_updates = {
		"food_status": VISITOR_PASS_FOOD_STATUS_FROM_REQUEST.get(request_doc.status, "Pending"),
		"food_dept_staff_assigned": request_doc.assigned_staff,
		"hospitality_overall_status": _compute_overall_hospitality_status(request_doc),
	}
	if request_doc.docstatus < 2:
		pass_updates["hospitality_request"] = request_doc.name
	elif (
		frappe.db.get_value("Visitor Pass", request_doc.visitor_pass, "hospitality_request")
		== request_doc.name
	):
		# A cancelled request is no longer the pass's request, and Frappe will not
		# save a document that links to a cancelled one — the pass could not be
		# updated at all while it pointed here.
		pass_updates["hospitality_request"] = None

	if not status_only:
		pass_updates.update(
			{
				"conference_room": request_doc.conference_room,
				"service_time": request_doc.service_time,
				"cab_required": cint(getattr(request_doc, "cab_required", 0)),
				"hotel_required": cint(getattr(request_doc, "hotel_required", 0)),
				"factory_tour_required": cint(getattr(request_doc, "factory_tour_required", 0)),
				"buggy_required": cint(getattr(request_doc, "buggy_required", 0)),
				"greeting_required": cint(getattr(request_doc, "greeting_required", 0)),
			}
		)
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
	# Meal Required is the host's decision. The visit window only SUGGESTS it —
	# the desk form ticks it live as the times are entered (refresh_hospitality_plan
	# in visitor_pass.js), where the host can see it and untick it. Forcing it on
	# here overrode that untick on every save, so a pass overlapping a meal window
	# could never be saved without a meal and the kitchen was sent orders nobody
	# asked for. What the derivation still fills in is the detail of a meal the
	# host did ask for (type, slots, service time).
	effective_meal_required = cint(getattr(doc, "meal_required", 0))
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
	if not effective_meal_required:
		# No meal asked for: no meal details either, even when the window overlaps one.
		doc.meal_type = None
	elif existing_meal_type and (preserve_existing or honor_manual_meal_type or diverged_from_derived):
		doc.meal_type = existing_meal_type
	else:
		doc.meal_type = meal_plan["meal_type"]

	if hasattr(doc, "assigned_meal_slots"):
		doc.assigned_meal_slots = meal_plan["assigned_meal_slots"] if meal_plan["meal_required"] else None

	if hasattr(doc, "hospitality_type"):
		doc.hospitality_type = meal_plan["hospitality_type"] if meal_plan["meal_required"] else None

	if hasattr(doc, "service_time"):
		if not effective_meal_required:
			doc.service_time = None
		elif preserve_existing and existing_service_time:
			doc.service_time = existing_service_time
		else:
			doc.service_time = meal_plan["service_time"]

	return meal_plan


def populate_hospitality_request_from_pass(doc, visitor_pass=None):
	"""Copy onto the request what the Visitor Pass decides.

	The pass owns the visit window, the meal decision and which arrangements
	were asked for. The request owns how they are delivered — its times and
	places, its fulfilment status, its staff and its notes — and none of that is
	written here, except the times, which start as defaults taken from the visit
	window and follow it when the visit is moved (`_follow_visit_window`).
	"""
	visitor_pass = visitor_pass or (
		frappe.get_doc("Visitor Pass", doc.visitor_pass) if getattr(doc, "visitor_pass", None) else None
	)
	if not visitor_pass:
		return doc

	# The window the request's times were last set for, read before it is
	# overwritten below.
	previous_window = _window_from_request(doc)

	meal_plan = derive_hospitality_meal_plan(visitor_pass)
	# The Visitor Pass's Meal Required is the decision — carried across as is,
	# ticked or not (see apply_hospitality_meal_plan).
	doc.meal_required = cint(getattr(visitor_pass, "meal_required", 0))
	doc.meal_type = (
		(getattr(visitor_pass, "meal_type", None) or meal_plan["meal_type"]) if doc.meal_required else None
	)
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
	doc.service_time = meal_plan["service_time"] if doc.meal_required else None
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
	# here and rolling back the Visitor Pass approval that triggered it.
	#
	# Nor does it touch `status`, `assigned_staff` or `notes`. Those are the
	# request's own (the Hospitality Manager's) and used to be overwritten from
	# the pass's hidden mirror fields on every pass save: the status went back to
	# whatever the pass had last been told, so a request called off because its
	# pass was rejected stayed "Cancelled" after the pass was approved. The
	# request's controller keeps `status` in step with its own workflow and with
	# the state of the pass (HospitalityRequest._sync_status_with_workflow).

	# Mirror arrangement request flags from Visitor Pass (host-entered intent)
	for flag in ARRANGEMENT_REQUIRED_FIELDS:
		if hasattr(visitor_pass, flag):
			setattr(doc, flag, cint(getattr(visitor_pass, flag, 0)))

	if cint(doc.cab_required) and not doc.cab_type:
		doc.cab_type = "Both"
	vp_people = getattr(visitor_pass, "number_of_people", None)
	if cint(doc.hotel_required) and not doc.no_of_guests and vp_people:
		doc.no_of_guests = vp_people

	_follow_visit_window(doc, visitor_pass, previous_window)

	return doc


def _window_from_request(doc):
	"""The visit window as the request last recorded it, or None."""
	start = getattr(doc, "visit_start_time", None)
	end = getattr(doc, "visit_end_time", None)
	if not (start and end):
		return None
	start, end = get_datetime(start), get_datetime(end)
	# The request does not record the pass's Valid Until, so a multi-day pass's
	# last day is unknown here; the visit day stands in for it.
	return frappe._dict(start=start, end=end, first_day=start.date(), last_day=start.date())


def _window_from_pass(visitor_pass):
	"""The visit window the pass asks for now, or None while it has no times."""
	visit_date = getattr(visitor_pass, "visit_date", None)
	start = _combine_visit_datetime(visit_date, getattr(visitor_pass, "expected_checkin", None))
	end = _combine_visit_datetime(visit_date, getattr(visitor_pass, "expected_checkout", None))
	if not (start and end):
		return None
	if end < start:
		end = start
	first_day = getdate(visit_date)
	last_day = getdate(getattr(visitor_pass, "pass_valid_until", None) or visit_date)
	return frappe._dict(start=start, end=end, first_day=first_day, last_day=max(first_day, last_day))


def _default_times(window):
	"""The time each service starts out with for a given visit window."""
	return {
		"pickup_datetime": window.start,
		"drop_datetime": window.end,
		"check_in": window.first_day,
		"check_out": window.last_day,
		"tour_start_time": window.start.strftime("%H:%M:%S"),
		"buggy_datetime": window.start,
		# A greeting is ready before the visitor walks in.
		"greeting_delivery_time": window.start - timedelta(minutes=30),
	}


def _fits_window(fieldname, value, window):
	"""Whether a time still belongs to the visit.

	The same limits HospitalityRequest validates (tour, buggy and greeting inside
	the visit; hotel within a day of it). A cab has no validated limit, so it is
	held to the visit's days, give or take one — a pickup the evening before is
	plausible, one on last month's date is the old visit.
	"""
	if fieldname in ("pickup_datetime", "drop_datetime", "check_in", "check_out"):
		day = timedelta(days=1)
		return window.first_day - day <= getdate(value) <= window.last_day + day
	if fieldname in ("tour_start_time", "tour_end_time"):
		return window.start.time() <= get_time(value) <= window.end.time()
	if fieldname == "buggy_datetime":
		return window.start <= get_datetime(value) <= window.end
	if fieldname == "greeting_delivery_time":
		buffer = timedelta(minutes=30)
		return window.start - buffer <= get_datetime(value) <= window.end + buffer
	return True


def _same_moment(fieldname, value, other):
	if fieldname in ("check_in", "check_out"):
		return getdate(value) == getdate(other)
	if fieldname in ("tour_start_time", "tour_end_time"):
		return get_time(value) == get_time(other)
	return get_datetime(value) == get_datetime(other)


def _follow_visit_window(doc, visitor_pass, previous_window):
	"""Set the request's service times from the visit window, and move them with it.

	Each service's time starts as a default taken from the visit (cab pickup at
	check-in, greeting half an hour before it, ...) and can then be set by a
	person. These were only ever filled in while empty, so when a visit was
	rescheduled the request kept the old day's tour, buggy and greeting — and
	since the request is saved inside the pass's save, its own "outside the
	visit window" check then refused the PASS.

	When the visit window has changed since the request last recorded it:
	- a time that still holds the old default follows to the new default;
	- a time a person set stays if it still belongs to the new visit;
	- a time a person set that no longer does is reset to the new default, and
	  the person saving is told which ones, so they can be set again.
	A window that has not changed leaves everything a person typed alone — their
	own entries are for the request's validation to judge, not to be replaced.
	"""
	window = _window_from_pass(visitor_pass)
	if not window:
		return

	services = []
	if cint(doc.cab_required):
		if doc.cab_type in ("Pickup", "Both"):
			services.append("pickup_datetime")
		if doc.cab_type in ("Drop", "Both"):
			services.append("drop_datetime")
	if cint(doc.hotel_required):
		services += ["check_in", "check_out"]
	if cint(doc.factory_tour_required):
		# Tour Date is read-only on the form ("Auto-set from Visitor Pass visit
		# date"), so it is always the visit date, not only while empty.
		doc.tour_date = window.first_day
		services += ["tour_start_time", "tour_end_time"]
	if cint(doc.buggy_required):
		services.append("buggy_datetime")
	if cint(doc.greeting_required):
		services.append("greeting_delivery_time")

	defaults = _default_times(window)
	moved = bool(
		previous_window and (previous_window.start != window.start or previous_window.end != window.end)
	)
	old_defaults = _default_times(previous_window) if moved else {}
	reset = []

	for fieldname in services:
		value = doc.get(fieldname)
		# Tour End Time has no default: it is the one time only a person sets.
		default = defaults.get(fieldname)
		if not value:
			if default is not None:
				doc.set(fieldname, default)
			continue
		if not moved:
			continue
		old_default = old_defaults.get(fieldname)
		if old_default is not None and _same_moment(fieldname, value, old_default):
			doc.set(fieldname, default)
		elif not _fits_window(fieldname, value, window):
			doc.set(fieldname, default)
			reset.append(fieldname)

	if moved and cint(doc.factory_tour_required) and doc.tour_start_time and doc.tour_end_time:
		# A reset start can land on or after an end that was kept.
		if get_time(doc.tour_end_time) <= get_time(doc.tour_start_time):
			doc.tour_end_time = None
			if "tour_end_time" not in reset:
				reset.append("tour_end_time")

	if reset:
		labels = ", ".join(frappe.bold(_(doc.meta.get_label(fieldname))) for fieldname in reset)
		frappe.msgprint(
			_(
				"The visit was moved, and these times on Hospitality Request {0} no longer "
				"fitted it: {1}. They were reset to the new visit time — open the request to "
				"set them again."
			).format(doc.name or _("(new)"), labels),
			title=_("Hospitality Times Reset"),
			indicator="orange",
		)


def _compute_overall_hospitality_status(request_doc):
	# Individual per-service statuses were removed. Overall status now derives
	# from the Hospitality Request's main `status` field plus whether any
	# arrangement was requested at all.
	any_required = any(cint(getattr(request_doc, f, 0)) for f in ARRANGEMENT_REQUIRED_FIELDS)
	has_food_or_room = cint(getattr(request_doc, "meal_required", 0)) or getattr(
		request_doc, "conference_room", None
	)

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


# nosemgrep: guest-whitelisted-method - portal meal preview; rate-limited, reads settings only
@frappe.whitelist(allow_guest=True)
@rate_limit(limit=60, seconds=60 * 60)
def get_hospitality_meal_plan(
	visit_date: str | None = None,
	expected_checkin: str | None = None,
	expected_checkout: str | None = None,
):
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
	event_time = get_datetime(event_time or getattr(security_log, "modified", None) or now_datetime())

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

	record_name = frappe.db.get_value("Contact Trace Record", {"security_log": security_log.name}, "name")
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
