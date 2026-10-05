"""Hospitality Request extensions.

Phase 4 — team routing: each arrangement (meal, cab, hotel, buggy, factory tour,
greeting) is sent to its own team role (VMS Settings), once, when the visit is
approved. Confirmed cab / hotel bookings are emailed to the visitor.

Phase 5 — financial approval for cab / hotel: Transport submits an estimate ->
Dept Head (HOD) approves -> Finance (VMS Finance Approver) approves. Cab / hotel
bookings cannot be confirmed before Finance Approved.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime, nowdate

from visitormanagement.visitor_management.utils import (
	doc_link,
	get_employee_users,
	get_role_users,
	notify_users,
	send_mail,
)

APPROVED_PASS_STATES = ("Approved", "Items Verified", "Checked-In", "Checked-Out")

# key: (flag field, VMS Settings role field, default role, fallback email field, label)
ARRANGEMENTS = {
	"meal": ("meal_required", "food_notification_role", "Hospitality Manager", "admin_team_email", "Meal / Refreshments"),
	"cab": ("cab_required", "transport_notification_role", "Transport Coordinator", "travel_desk_email", "Cab"),
	"hotel": ("hotel_required", "transport_notification_role", "Transport Coordinator", "travel_desk_email", "Hotel"),
	"buggy": ("buggy_required", "transport_notification_role", "Transport Coordinator", "travel_desk_email", "Buggy"),
	"tour": ("factory_tour_required", "tour_notification_role", "Factory Tour Coordinator", "admin_team_email", "Factory Tour"),
	"greeting": ("greeting_required", "greeting_notification_role", "Greeting Staff", "admin_team_email", "Greeting"),
}

NOT_REQUIRED = "Not Required"
PENDING_ESTIMATE = "Pending Estimate"
ESTIMATE_SUBMITTED = "Estimate Submitted"
DEPT_HEAD_APPROVED = "Dept Head Approved"
FINANCE_APPROVED = "Finance Approved"
REJECTED = "Rejected"

ESTIMATE_ROLES = {"Transport Coordinator", "Hospitality Manager", "System Manager"}
DEPT_HEAD_ROLES = {"HOD", "System Manager"}
FINANCE_ROLES = {"VMS Finance Approver", "System Manager"}


# ─────────────────────────────────────────────────────────
# Hooks called from HospitalityRequest
# ─────────────────────────────────────────────────────────
def before_save(doc):
	"""validate (draft) and before_update_after_submit (approved)."""
	_sync_financial_requirement(doc)
	_compute_cost_totals(doc)
	_guard_booking_confirmation(doc)


def after_save(doc):
	"""after_insert / on_update / on_update_after_submit."""
	route_to_teams(doc)
	send_booking_details_to_visitor(doc)


# ─────────────────────────────────────────────────────────
# Phase 4 — team routing
# ─────────────────────────────────────────────────────────
def _visitor_pass(doc):
	if not doc.visitor_pass:
		return None
	return frappe.db.get_value(
		"Visitor Pass",
		doc.visitor_pass,
		["name", "status", "visitor_full_name", "email_id", "mobile_number", "visit_date",
		 "expected_checkin", "expected_checkout", "person_to_visit", "company__organisation"],
		as_dict=True,
	)


def route_to_teams(doc):
	vp = _visitor_pass(doc)
	# Teams start preparing only once the visit is approved.
	if not vp or vp.status not in APPROVED_PASS_STATES:
		return
	settings = frappe.get_cached_doc("VMS Settings")
	notified = {k for k in (doc.get("teams_notified") or "").split(",") if k}
	newly = set()
	for key, (flag, role_field, default_role, fallback_field, label) in ARRANGEMENTS.items():
		if key in notified or not cint(doc.get(flag)):
			continue
		role = settings.get(role_field) or default_role
		subject = _("{0} required: {1} on {2}").format(label, vp.visitor_full_name, frappe.utils.formatdate(vp.visit_date))
		message = _arrangement_message(doc, vp, key, label)
		users = get_role_users(role)
		if users:
			notify_users(users, subject, message, doc.doctype, doc.name)
		elif settings.get(fallback_field):
			send_mail([settings.get(fallback_field)], subject, message, doc.doctype, doc.name)
		else:
			frappe.log_error(
				f"No user has role '{role}' and no fallback email is set in VMS Settings; {label} for {doc.name} was not routed.",
				"VMS Team Routing",
			)
			continue
		newly.add(key)
	if newly:
		doc.db_set("teams_notified", ",".join(sorted(notified | newly)), update_modified=False)


def _arrangement_message(doc, vp, key, label):
	esc = frappe.utils.escape_html
	rows = [
		(_("Visitor"), vp.visitor_full_name),
		(_("Company"), vp.company__organisation or "-"),
		(_("Visit"), f"{frappe.utils.formatdate(vp.visit_date)} {vp.expected_checkin or ''} – {vp.expected_checkout or ''}"),
		(_("Host"), vp.person_to_visit or "-"),
	]
	specifics = {
		"meal": [(_("Meal Type"), doc.get("meal_type")), (_("Meal Slots"), doc.get("assigned_meal_slots")),
				 (_("Special Diet"), doc.get("special_diet")), (_("Allergies"), doc.get("dietary_allergies"))],
		"cab": [(_("Cab Type"), doc.get("cab_type")), (_("Pickup"), f"{doc.get('pickup_location') or '-'} @ {doc.get('pickup_datetime') or '-'}"),
				(_("Drop"), f"{doc.get('drop_location') or '-'} @ {doc.get('drop_datetime') or '-'}"),
				(_("Instructions"), doc.get("cab_pickup_instructions"))],
		"hotel": [(_("Check-in / out"), f"{doc.get('check_in') or '-'} → {doc.get('check_out') or '-'}"),
				  (_("Rooms / Guests"), f"{doc.get('no_of_rooms') or '-'} / {doc.get('no_of_guests') or '-'}"),
				  (_("Room Type"), doc.get("room_type")), (_("Requests"), doc.get("hotel_special_requests"))],
		"buggy": [(_("Pickup → Drop"), f"{doc.get('buggy_pickup_point') or '-'} → {doc.get('buggy_drop_point') or '-'}"),
				  (_("When"), doc.get("buggy_datetime"))],
		"tour": [(_("Tour"), f"{doc.get('tour_date') or '-'} {doc.get('tour_start_time') or ''} – {doc.get('tour_end_time') or ''}"),
				 (_("Areas"), ", ".join(r.area_name for r in (doc.get("tour_areas") or []) if r.area_name))],
		"greeting": [(_("Greeting"), doc.get("greeting_type")), (_("Delivery"), f"{doc.get('greeting_delivery_point') or '-'} @ {doc.get('greeting_delivery_time') or '-'}")],
	}.get(key, [])
	table = "".join(
		f"<tr><td style='padding:4px 8px;'><b>{k}</b></td><td style='padding:4px 8px;'>{esc(str(v or '-'))}</td></tr>"
		for k, v in rows + specifics
	)
	return (
		f"<p><b>{esc(label)}</b> is required for an approved visit.</p>"
		f"<table style='border-collapse:collapse;'>{table}</table>"
		f"<p><a href='{doc_link(doc.doctype, doc.name)}'>Open Hospitality Request {doc.name}</a></p>"
	)


def send_booking_details_to_visitor(doc):
	vp = _visitor_pass(doc)
	if not vp or not vp.email_id:
		return
	esc = frappe.utils.escape_html
	if cint(doc.get("cab_booking_confirmed")) and not cint(doc.get("cab_details_sent_to_visitor")) and doc.get("driver_name") and doc.get("driver_phone"):
		send_mail(
			[vp.email_id],
			_("Your cab details for {0}").format(frappe.utils.formatdate(vp.visit_date)),
			(
				f"<p>Dear {esc(vp.visitor_full_name)},</p><p>Your cab has been arranged.</p><ul>"
				f"<li><b>Driver:</b> {esc(doc.driver_name)}</li>"
				f"<li><b>Driver Phone:</b> {esc(doc.driver_phone)}</li>"
				f"<li><b>Vehicle Number:</b> {esc(doc.get('cab_number') or '-')}</li>"
				f"<li><b>Pickup:</b> {esc(doc.get('pickup_location') or '-')} at {doc.get('pickup_datetime') or '-'}</li>"
				f"<li><b>Drop:</b> {esc(doc.get('drop_location') or '-')} at {doc.get('drop_datetime') or '-'}</li></ul>"
			),
			doc.doctype,
			doc.name,
		)
		doc.db_set("cab_details_sent_to_visitor", 1, update_modified=False)
	if cint(doc.get("hotel_booking_confirmed")) and not cint(doc.get("hotel_details_sent_to_visitor")) and doc.get("hotel_name"):
		attachments = [{"file_url": doc.hotel_booking_voucher}] if doc.get("hotel_booking_voucher") else None
		send_mail(
			[vp.email_id],
			_("Your hotel booking for {0}").format(frappe.utils.formatdate(vp.visit_date)),
			(
				f"<p>Dear {esc(vp.visitor_full_name)},</p><p>Your hotel has been booked.</p><ul>"
				f"<li><b>Hotel:</b> {esc(doc.hotel_name)}</li>"
				f"<li><b>Check-in:</b> {doc.get('check_in') or '-'}</li>"
				f"<li><b>Check-out:</b> {doc.get('check_out') or '-'}</li>"
				f"<li><b>Room Type:</b> {esc(doc.get('room_type') or '-')}</li>"
				f"<li><b>Booking Reference:</b> {esc(doc.get('booking_reference') or '-')}</li></ul>"
				+ ("<p>The booking voucher is attached.</p>" if attachments else "")
			),
			doc.doctype,
			doc.name,
			attachments=attachments,
		)
		doc.db_set("hotel_details_sent_to_visitor", 1, update_modified=False)


# ─────────────────────────────────────────────────────────
# Phase 5 — financial approval
# ─────────────────────────────────────────────────────────
def _sync_financial_requirement(doc):
	requires = 1 if (cint(doc.get("cab_required")) or cint(doc.get("hotel_required"))) else 0
	doc.requires_financial_approval = requires
	status = doc.get("financial_approval_status") or NOT_REQUIRED
	if requires and status == NOT_REQUIRED:
		doc.financial_approval_status = PENDING_ESTIMATE
	elif not requires and status == PENDING_ESTIMATE:
		doc.financial_approval_status = NOT_REQUIRED
	elif not doc.get("financial_approval_status"):
		doc.financial_approval_status = status


def _compute_cost_totals(doc):
	doc.total_estimated_cost = flt(doc.get("estimated_cab_cost")) + flt(doc.get("estimated_hotel_cost"))
	doc.total_actual_cost = flt(doc.get("actual_cab_cost")) + flt(doc.get("actual_hotel_cost"))


def _guard_booking_confirmation(doc):
	if not cint(doc.get("requires_financial_approval")) or doc.get("financial_approval_status") == FINANCE_APPROVED:
		return
	before = doc.get_doc_before_save()
	for field, label in (("cab_booking_confirmed", _("Cab")), ("hotel_booking_confirmed", _("Hotel"))):
		if cint(doc.get(field)) and not (before and cint(before.get(field))):
			frappe.throw(
				_("{0} booking can be confirmed only after Finance approval (current: {1}).").format(
					label, doc.get("financial_approval_status")
				),
				title=_("Financial Approval Pending"),
			)


def _require_roles(roles):
	if not set(frappe.get_roles()) & set(roles) and frappe.session.user != "Administrator":
		frappe.throw(_("You are not allowed to perform this action."), frappe.PermissionError)


def _load(name, expected_status):
	doc = frappe.get_doc("Hospitality Request", name)
	if doc.financial_approval_status != expected_status:
		frappe.throw(
			_("This action needs status {0}; the request is {1}.").format(expected_status, doc.financial_approval_status)
		)
	doc.flags.ignore_permissions = True
	return doc


def _host_users(doc):
	vp = _visitor_pass(doc)
	return get_employee_users([vp.person_to_visit]) if vp and vp.person_to_visit else []


def _finance_summary(doc):
	return (
		f"<ul><li><b>Request:</b> <a href='{doc_link(doc.doctype, doc.name)}'>{doc.name}</a></li>"
		f"<li><b>Visitor Pass:</b> {doc.visitor_pass}</li>"
		f"<li><b>Estimated Cab:</b> {frappe.utils.fmt_money(doc.estimated_cab_cost)}</li>"
		f"<li><b>Estimated Hotel:</b> {frappe.utils.fmt_money(doc.estimated_hotel_cost)}</li>"
		f"<li><b>Total:</b> {frappe.utils.fmt_money(doc.total_estimated_cost)}</li>"
		f"<li><b>Notes:</b> {frappe.utils.escape_html(doc.cost_estimate_notes or '-')}</li></ul>"
	)


@frappe.whitelist()
def submit_estimate(hospitality_request, estimated_cab_cost=0, estimated_hotel_cost=0, cost_estimate_notes=None):
	_require_roles(ESTIMATE_ROLES)
	doc = frappe.get_doc("Hospitality Request", hospitality_request)
	if doc.financial_approval_status not in (PENDING_ESTIMATE, REJECTED):
		frappe.throw(_("An estimate can be submitted only while Pending Estimate (or after a rejection)."))
	if flt(estimated_cab_cost) <= 0 and flt(estimated_hotel_cost) <= 0:
		frappe.throw(_("Enter at least one estimated cost."))
	doc.flags.ignore_permissions = True
	doc.update({
		"estimated_cab_cost": flt(estimated_cab_cost),
		"estimated_hotel_cost": flt(estimated_hotel_cost),
		"cost_estimate_notes": cost_estimate_notes,
		"estimated_by": frappe.session.user,
		"estimated_on": nowdate(),
		"financial_approval_status": ESTIMATE_SUBMITTED,
		"dept_head_approved_by": None, "dept_head_approved_on": None, "dept_head_remarks": None,
		"finance_approved_by": None, "finance_approved_on": None, "finance_remarks": None,
	})
	doc.save()
	notify_users(get_role_users("HOD"), _("Cab/Hotel estimate awaiting your approval: {0}").format(doc.name),
				 "<p>A cab / hotel cost estimate needs Department Head approval.</p>" + _finance_summary(doc),
				 doc.doctype, doc.name)
	return doc.financial_approval_status


@frappe.whitelist()
def dept_head_decision(hospitality_request, approve, remarks=None):
	_require_roles(DEPT_HEAD_ROLES)
	doc = _load(hospitality_request, ESTIMATE_SUBMITTED)
	approve = cint(approve)
	doc.update({
		"financial_approval_status": DEPT_HEAD_APPROVED if approve else REJECTED,
		"dept_head_approved_by": frappe.session.user,
		"dept_head_approved_on": now_datetime(),
		"dept_head_remarks": remarks,
	})
	doc.save()
	if approve:
		notify_users(get_role_users("VMS Finance Approver"), _("Cab/Hotel estimate awaiting Finance approval: {0}").format(doc.name),
					 "<p>The Department Head approved this estimate.</p>" + _finance_summary(doc), doc.doctype, doc.name)
	else:
		_notify_rejection(doc, _("Department Head"), remarks)
	return doc.financial_approval_status


@frappe.whitelist()
def finance_decision(hospitality_request, approve, remarks=None):
	_require_roles(FINANCE_ROLES)
	doc = _load(hospitality_request, DEPT_HEAD_APPROVED)
	approve = cint(approve)
	doc.update({
		"financial_approval_status": FINANCE_APPROVED if approve else REJECTED,
		"finance_approved_by": frappe.session.user,
		"finance_approved_on": now_datetime(),
		"finance_remarks": remarks,
	})
	doc.save()
	if approve:
		settings = frappe.get_cached_doc("VMS Settings")
		transport = get_role_users(settings.get("transport_notification_role") or "Transport Coordinator")
		notify_users(transport + _host_users(doc), _("Cab/Hotel approved by Finance: {0}").format(doc.name),
					 "<p>Finance approved the estimate. The cab / hotel can now be booked.</p>" + _finance_summary(doc),
					 doc.doctype, doc.name)
	else:
		_notify_rejection(doc, _("Finance"), remarks)
	return doc.financial_approval_status


def _notify_rejection(doc, stage, remarks):
	settings = frappe.get_cached_doc("VMS Settings")
	users = get_role_users(settings.get("transport_notification_role") or "Transport Coordinator")
	users += _host_users(doc) + ([doc.estimated_by] if doc.estimated_by else [])
	notify_users(users, _("Cab/Hotel estimate rejected by {0}: {1}").format(stage, doc.name),
				 _("<p>Rejected by {0}. Remarks: {1}</p>").format(stage, frappe.utils.escape_html(remarks or "-")) + _finance_summary(doc),
				 doc.doctype, doc.name)
