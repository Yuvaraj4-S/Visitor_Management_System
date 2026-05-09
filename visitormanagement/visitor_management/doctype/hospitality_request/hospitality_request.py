# Copyright (c) 2026, Harthesh and contributors
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

	frappe.sendmail(
		recipients=[email],
		subject=subject,
		message="<br>".join(lines),
		now=True,
	)


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
