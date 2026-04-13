# Copyright (c) 2026, Harthesh and contributors
# For license information, please see license.txt

import re

import frappe
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
		self._compute_hotel_nights()
		self._validate_cab_timing()
		self._validate_tour_safety()
		self._validate_buggy_conflict()

	def _compute_hotel_nights(self):
		if self.hotel_required and self.check_in and self.check_out:
			nights = date_diff(self.check_out, self.check_in)
			if nights < 1:
				frappe.throw("Hotel check-out must be after check-in")
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
		if self.tour_status == "Scheduled" and not self.safety_briefing_done:
			frappe.throw("Safety briefing must be completed before scheduling a factory tour")
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
				"buggy_status": ("not in", ("Cancelled", "Completed")),
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
