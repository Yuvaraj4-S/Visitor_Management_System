# For license information, please see license.txt

import re
from datetime import timedelta

import frappe
from frappe import _
from frappe.model.document import Document

from frappe.utils import date_diff, get_datetime, getdate, nowdate


from visitormanagement.visitor_management.lifecycle import (
	populate_hospitality_request_from_pass,
	sync_hospitality_to_pass,
)


def _get_assigned_staff_email(employee_name):
	if not employee_name:
		return None

	employee = frappe.db.get_value(
		"Employee",
		employee_name,
		["company_email", "personal_email", "user_id", "employee_name"],
		as_dict=True,
	)
	if not employee:
		return None

	return employee.company_email or employee.personal_email or employee.user_id


def _send_hospitality_assignment_mail(doc):
	email = _get_assigned_staff_email(doc.assigned_staff)
	if not email:
		return

	visitor_name = frappe.db.get_value("Visitor Pass", doc.visitor_pass, "visitor_full_name") or doc.visitor_pass
	subject = f"Hospitality Confirmed: {visitor_name}"
	lines = [
		f"Hospitality Request: {doc.name}",
		f"Visitor Pass: {doc.visitor_pass}",
		f"Visitor: {visitor_name}",
		f"Meal Required: {'Yes' if doc.meal_required else 'No'}",
		f"Meal Type: {doc.meal_type or '-'}",
		f"Meal Slots: {getattr(doc, 'assigned_meal_slots', None) or '-'}",
		f"Hospitality Type: {getattr(doc, 'hospitality_type', None) or '-'}",
		f"Special Diet: {getattr(doc, 'special_diet', None) or '-'}",
		f"Conference Room: {doc.conference_room or '-'}",
		f"Service Time: {doc.service_time or '-'}",
	]
	if doc.notes:
		lines.extend(["", f"Notes: {frappe.utils.strip_html(doc.notes)}"])

	try:
		frappe.sendmail(
			recipients=[email],
			subject=subject,
			message="<br>".join(lines),
			now=True,
		)
	except Exception as exc:
		# Email Account may not be configured — log and continue rather than blocking the save.
		frappe.log_error(f"Hospitality assignment email failed for {doc.name}: {exc}", "VMS Hospitality Assignment Email")


class HospitalityRequest(Document):
	def autoname(self):
		visitor_name = None
		visit_date = None
		if self.visitor_pass:
			visitor_name, visit_date = frappe.db.get_value(
				"Visitor Pass", self.visitor_pass, ["visitor_full_name", "visit_date"]
			) or (None, None)

		letters = re.sub(r"[^A-Za-z]", "", visitor_name or "").upper()[:3] or "XXX"
		letters = letters.ljust(3, "X")
		date_part = getdate(visit_date or nowdate()).strftime("%d%m%y")

		base = f"HOSP-{letters}-{date_part}"
		candidate = base
		suffix = 2
		while frappe.db.exists("Hospitality Request", candidate):
			candidate = f"{base}-{suffix}"
			suffix += 1
		self.name = candidate

	def validate(self):
		if not self.status:
			self.status = "Pending"
		if self.visitor_pass:
			populate_hospitality_request_from_pass(self)
		self._validate_visitor_pass_approved()
		self._compute_hotel_nights()
		self._validate_cab_timing()
		self._validate_tour_safety()
		self._validate_buggy_conflict()
		self._validate_seating_capacity()
		self._validate_hotel_in_visit_window()
		self._validate_activities_in_visit_window()

	# Real-world rule: hospitality preparation should not begin until the visitor
	# is confirmed. Drafts (and re-applications after rejection) can be created
	# against any pass, but moving the request out of Draft requires the linked
	# Visitor Pass to be Approved (or beyond — Items Verified / Checked-In / -Out).
	# is confirmed. Drafts can be created against any pass (so meal flags etc. can
	# be drafted alongside the pass), but moving the request out of Draft requires
	# the linked Visitor Pass to be Approved or beyond.

	def _validate_visitor_pass_approved(self):
		if not self.visitor_pass:
			return
		current_state = getattr(self, "workflow_state", None) or "Draft"
		if current_state in ("Draft", "Rejected"):
			return
		vp_status = frappe.db.get_value("Visitor Pass", self.visitor_pass, "status")
		if vp_status not in ("Approved", "Items Verified", "Checked-In", "Checked-Out"):
			frappe.throw(
				_(
					"Visitor Pass {0} is currently <b>{1}</b>. Please ensure the Visitor "
					"Pass is Approved before submitting this Hospitality Request."
				).format(self.visitor_pass, vp_status or _("Draft")),
				title=_("Approval Not Allowed"),
			)

	def _validate_seating_capacity(self):
		if self.seating_capacity is not None and int(self.seating_capacity or 0) < 0:
			frappe.throw(
				_("Seating Capacity cannot be negative."),
				title=_("Invalid Seating"),
			)
		if not self.conference_room:
			return
		room_cap = frappe.db.get_value("Conference Room", self.conference_room, "capacity") or 0
		guests = int(self.seating_capacity or self.no_of_guests or 0)
		if guests and room_cap and guests > int(room_cap):
			frappe.throw(
				_("Seating ({0}) exceeds room {1} capacity ({2}). Pick a larger room.").format(
					guests, self.conference_room, room_cap
				),
				title=_("Room Over-Booked"),
			)

	def _validate_activities_in_visit_window(self):
		"""Tour / buggy / greeting times must fall within the visit window."""
		if not (self.visit_start_time and self.visit_end_time):
			return
		start_dt = get_datetime(self.visit_start_time)
		end_dt = get_datetime(self.visit_end_time)

		def _check(label, dt_value, buffer_hours=0):
			if not dt_value:
				return
			v = get_datetime(dt_value)
			lo = start_dt - timedelta(hours=buffer_hours)
			hi = end_dt + timedelta(hours=buffer_hours)
			if v < lo or v > hi:
				frappe.throw(
					_("{0} ({1}) is outside the visit window ({2} \u2192 {3}).").format(
						label, dt_value, start_dt, end_dt
					),
					title=_("Out of Visit Window"),
				)

		if self.factory_tour_required and self.tour_date:
			if self.tour_start_time:
				_check(_("Tour start"),
					get_datetime(f"{self.tour_date} {self.tour_start_time}"))
			if self.tour_end_time:
				_check(_("Tour end"),
					get_datetime(f"{self.tour_date} {self.tour_end_time}"))

		if self.buggy_required and self.buggy_datetime:
			_check(_("Buggy pickup"), self.buggy_datetime)

		if self.greeting_required and self.greeting_delivery_time:
			# greeting often happens at arrival — allow 30 min buffer either side
			_check(_("Greeting delivery"), self.greeting_delivery_time, buffer_hours=0.5)

	def _validate_hotel_in_visit_window(self):
		"""Hotel check-in/out should fall within (or very close to) the visit window."""
		if not (self.hotel_required and self.check_in and self.visitor_pass):
			return

		vp = frappe.db.get_value(
			"Visitor Pass", self.visitor_pass,
			["visit_date", "pass_valid_until"],
			as_dict=True,
		) or {}
		visit_date = vp.get("visit_date")
		valid_until = vp.get("pass_valid_until") or visit_date

		if visit_date and getdate(self.check_in) < getdate(visit_date):
			# Allow arriving up to 1 day earlier (for late evening/next-day meetings)
			if date_diff(visit_date, self.check_in) > 1:
				frappe.throw(
					_("Hotel check-in ({0}) is before the visit date ({1}).").format(
						self.check_in, visit_date
					),
					title=_("Invalid Hotel Dates"),
				)

		if self.check_out and valid_until:
			if getdate(self.check_out) > getdate(valid_until):
				# Allow departing up to 1 day after
				if date_diff(self.check_out, valid_until) > 1:
					frappe.throw(
						_("Hotel check-out ({0}) is after the visit ends ({1}).").format(
							self.check_out, valid_until
						),
						title=_("Invalid Hotel Dates"),
					)

	def _compute_hotel_nights(self):
		if self.hotel_required and self.check_in and self.check_out:
			nights = date_diff(self.check_out, self.check_in)
			if nights < 0:
				frappe.throw(
					_("Hotel check-out ({0}) cannot be before check-in ({1}).").format(
						self.check_out, self.check_in
					),
					title=_("Invalid Hotel Dates"),
				)
			# 0 nights is valid for day-use hotel (prayer room / locker / day stay).
			self.nights = nights
		else:
			self.nights = 0

	def _validate_cab_timing(self):
		if not self.cab_required:
			return
		if self.cab_type in ("Pickup", "Both") and not self.pickup_datetime:
			frappe.throw("Pickup datetime required when cab type includes Pickup")
		if self.cab_type in ("Drop", "Both") and not self.drop_datetime:
			frappe.throw("Drop datetime required when cab type includes Drop")
		if self.pickup_datetime and self.drop_datetime:
			if get_datetime(self.drop_datetime) < get_datetime(self.pickup_datetime):
				frappe.throw("Drop datetime cannot be before pickup datetime")

	def _validate_tour_safety(self):
		if not self.factory_tour_required:
			return
		if self.tour_start_time and self.tour_end_time:
			if self.tour_end_time <= self.tour_start_time:
				frappe.throw("Tour end time must be after start time")

	def _validate_buggy_conflict(self):
		if not (self.buggy_required and self.buggy_number and self.buggy_datetime):
			return
		conflict = frappe.db.exists(
			"Hospitality Request",
			{
				"name": ("!=", self.name),
				"buggy_required": 1,
				"buggy_number": self.buggy_number,
				"buggy_datetime": self.buggy_datetime,
				"status": ("not in", ("Cancelled", "Completed")),
			},
		)
		if conflict:
			frappe.throw(f"Buggy {self.buggy_number} already booked at {self.buggy_datetime} ({conflict})")

	def on_update(self):
		sync_hospitality_to_pass(self)
		previous = self.get_doc_before_save()
		status_changed_to_confirmed = self.status == "Confirmed" and (
			not previous or previous.status != "Confirmed"
		)
		assigned_staff_changed = (
			self.status == "Confirmed"
			and self.assigned_staff
			and previous
			and previous.assigned_staff != self.assigned_staff
		)
		if status_changed_to_confirmed or assigned_staff_changed:
			_send_hospitality_assignment_mail(self)

		# Handle service assignment ToDos and booking confirmation emails
		_handle_service_assignments(self, previous)
		_handle_booking_confirmations(self, previous)


def _resolve_user_from_assignment(field_value, link_type):
	"""Resolve the User ID from an assigned field value.

	Cab/Hotel fields link to User directly. Buggy/Greeting link to Employee,
	so we need to look up the employee's user_id.
	"""
	if not field_value:
		return None
	if link_type == "User":
		return field_value
	# link_type == "Employee"
	return frappe.db.get_value("Employee", field_value, "user_id")


def _handle_service_assignments(doc, previous):
	"""Create ToDo, share document, and send system notification when a service is assigned."""
	from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification
	from frappe.utils import get_fullname

	_SERVICE_ASSIGNMENTS = [
		{
			"required_field": "cab_required",
			"assigned_field": "cab_assigned_to",
			"link_type": "User",
			"label": "Cab Booking",
		},
		{
			"required_field": "hotel_required",
			"assigned_field": "hotel_assigned_to",
			"link_type": "User",
			"label": "Hotel Booking",
		},
		{
			"required_field": "buggy_required",
			"assigned_field": "buggy_assigned_to",
			"link_type": "Employee",
			"label": "Buggy Vehicle",
		},
		{
			"required_field": "greeting_required",
			"assigned_field": "greeting_assigned_to",
			"link_type": "Employee",
			"label": "Greeting Arrangement",
		},
	]

	for svc in _SERVICE_ASSIGNMENTS:
		if not getattr(doc, svc["required_field"], 0):
			continue
		assigned_value = getattr(doc, svc["assigned_field"], None)
		if not assigned_value:
			continue
		prev_assigned = getattr(previous, svc["assigned_field"], None) if previous else None
		if assigned_value == prev_assigned:
			continue

		assigned_user = _resolve_user_from_assignment(assigned_value, svc["link_type"])
		if not assigned_user:
			frappe.log_error(
				f"No user linked to {svc['link_type']} {assigned_value} — cannot send {svc['label']} notification.",
				"VMS Service Assignment",
			)
			continue

		visitor_name = doc.visitor_name_display or doc.visitor_pass
		task_description = _("{0} required for visitor {1}. Please complete and update the details.").format(
			svc["label"], visitor_name
		)

		# 1. Create ToDo
		frappe.get_doc({
			"doctype": "ToDo",
			"allocated_to": assigned_user,
			"reference_type": "Hospitality Request",
			"reference_name": doc.name,
			"description": task_description,
			"priority": "High",
		}).insert(ignore_permissions=True)

		# 2. Share the document with the assigned user (read + write)
		frappe.share.add(
			"Hospitality Request",
			doc.name,
			user=assigned_user,
			read=1,
			write=1,
			notify=0,  # we send our own notification below
		)

		# 3. Send system notification (appears in bell icon)
		assigner_name = get_fullname(frappe.session.user)
		enqueue_create_notification(
			[assigned_user],
			{
				"type": "Assignment",
				"document_type": "Hospitality Request",
				"document_name": doc.name,
				"subject": _("{0} assigned {1} for visitor {2} to you").format(
					frappe.bold(assigner_name),
					frappe.bold(svc["label"]),
					frappe.bold(visitor_name),
				),
				"from_user": frappe.session.user,
				"email_content": task_description,
			},
		)

		# 4. Send email notification to the assigned user
		_send_service_assignment_email(assigned_user, svc["label"], visitor_name, doc)


def _send_service_assignment_email(user_email, service_label, visitor_name, doc):
	"""Send email to the assigned user about the service assignment."""
	link = frappe.utils.get_url_to_form("Hospitality Request", doc.name)
	try:
		frappe.sendmail(
			recipients=[user_email],
			subject=_("{0} Assignment: {1}").format(service_label, visitor_name),
			message=(
				f"<p>Dear Colleague,</p>"
				f"<p>You have been assigned <b>{service_label}</b> for visitor "
				f"<b>{visitor_name}</b>.</p>"
				f"<p>Please open the Hospitality Request and complete the required details:</p>"
				f"<p><a href='{link}'>{doc.name}</a></p>"
				f"<p>Regards,<br>Visitor Management Team</p>"
			),
			now=True,
		)
	except Exception as exc:
		frappe.log_error(
			f"Service assignment email failed for {doc.name} ({service_label}): {exc}",
			"VMS Service Assignment Email",
		)


def _handle_booking_confirmations(doc, previous):
	"""Send confirmation email to visitor when a service is marked as Booked."""
	_SERVICE_CONFIRMATIONS = [
		{
			"required_field": "cab_required",
			"status_field": "cab_status",
			"label": "Cab / Transport",
			"details_fn": _get_cab_confirmation_details,
		},
		{
			"required_field": "hotel_required",
			"status_field": "hotel_status",
			"label": "Hotel Booking",
			"details_fn": _get_hotel_confirmation_details,
		},
	]

	for svc in _SERVICE_CONFIRMATIONS:
		if not getattr(doc, svc["required_field"], 0):
			continue
		current_status = getattr(doc, svc["status_field"], None)
		prev_status = getattr(previous, svc["status_field"], None) if previous else None
		if current_status != "Booked" or prev_status == "Booked":
			continue

		# Close the corresponding ToDo
		assigned_field = svc["status_field"].replace("_status", "_assigned_to")
		_close_service_todo(doc.name, assigned_field)

		# Send confirmation to visitor (with attachment for hotel voucher)
		attachments = []
		if svc["status_field"] == "hotel_status" and doc.hotel_voucher:
			attachments = _get_file_attachments(doc.hotel_voucher)
		_send_visitor_booking_confirmation(doc, svc["label"], svc["details_fn"](doc), attachments)


def _get_cab_confirmation_details(doc):
	lines = []
	if doc.cab_type:
		lines.append(f"<li><b>Service:</b> {doc.cab_type}</li>")
	if doc.cab_vehicle_number:
		lines.append(f"<li><b>Vehicle Number:</b> {doc.cab_vehicle_number}</li>")
	if doc.driver_name:
		lines.append(f"<li><b>Driver Name:</b> {doc.driver_name}</li>")
	if doc.driver_phone:
		lines.append(f"<li><b>Driver Phone:</b> {doc.driver_phone}</li>")
	if doc.pickup_location:
		lines.append(f"<li><b>Pickup Location:</b> {doc.pickup_location}</li>")
	if doc.pickup_datetime:
		lines.append(f"<li><b>Pickup Time:</b> {doc.pickup_datetime}</li>")
	if doc.drop_location:
		lines.append(f"<li><b>Drop Location:</b> {doc.drop_location}</li>")
	if doc.drop_datetime:
		lines.append(f"<li><b>Drop Time:</b> {doc.drop_datetime}</li>")
	if doc.cab_pickup_instructions:
		lines.append(f"<li><b>Instructions:</b> {doc.cab_pickup_instructions}</li>")
	return "<ul>" + "".join(lines) + "</ul>" if lines else ""


def _get_hotel_confirmation_details(doc):
	lines = []
	if doc.hotel_name:
		hotel_label = frappe.db.get_value("Supplier", doc.hotel_name, "supplier_name") or doc.hotel_name
		lines.append(f"<li><b>Hotel:</b> {hotel_label}</li>")
	if doc.check_in:
		lines.append(f"<li><b>Check-in:</b> {doc.check_in}</li>")
	if doc.check_out:
		lines.append(f"<li><b>Check-out:</b> {doc.check_out}</li>")
	if doc.nights:
		lines.append(f"<li><b>Nights:</b> {doc.nights}</li>")
	if doc.room_type:
		lines.append(f"<li><b>Room Type:</b> {doc.room_type}</li>")
	if doc.no_of_rooms:
		lines.append(f"<li><b>Rooms:</b> {doc.no_of_rooms}</li>")
	if doc.booking_reference:
		lines.append(f"<li><b>Booking Reference:</b> {doc.booking_reference}</li>")
	return "<ul>" + "".join(lines) + "</ul>" if lines else ""


def _send_visitor_booking_confirmation(doc, service_label, details_html, attachments=None):
	"""Send booking confirmation email to the visitor, optionally with attachments."""
	if not doc.visitor_pass:
		return

	vp = frappe.db.get_value(
		"Visitor Pass", doc.visitor_pass,
		["visitor_full_name", "email_id", "mobile_number"],
		as_dict=True,
	)
	if not vp or not vp.email_id:
		return

	subject = _("{0} Confirmed for Your Visit").format(service_label)
	message = (
		f"<p>Dear {vp.visitor_full_name},</p>"
		f"<p>Your <b>{service_label}</b> has been confirmed. Here are the details:</p>"
		f"{details_html}"
		f"<p>If you have any questions, please contact your host.</p>"
		f"<p>Regards,<br>Visitor Management Team</p>"
	)

	try:
		frappe.sendmail(
			recipients=[vp.email_id],
			subject=subject,
			message=message,
			attachments=attachments or [],
			now=True,
		)
	except Exception as exc:
		frappe.log_error(
			f"Visitor booking confirmation email failed for {doc.name}: {exc}",
			"VMS Booking Confirmation Email",
		)


def _get_file_attachments(file_url):
	"""Get attachment dict for frappe.sendmail from a file URL."""
	if not file_url:
		return []
	file_doc = frappe.db.get_value(
		"File",
		{"file_url": file_url},
		["name", "file_name", "file_url", "is_private"],
		as_dict=True,
	)
	if not file_doc:
		return []
	return [{"fid": file_doc.name}]


def _close_service_todo(request_name, assigned_field):
	"""Close ToDo entries linked to this hospitality request for a specific service."""
	todos = frappe.get_all(
		"ToDo",
		filters={
			"reference_type": "Hospitality Request",
			"reference_name": request_name,
			"status": "Open",
		},
		pluck="name",
	)
	for todo_name in todos:
		frappe.db.set_value("ToDo", todo_name, "status", "Closed")
