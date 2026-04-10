# Copyright (c) 2026, Harthesh and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

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
	def validate(self):
		if not self.status:
			self.status = "Pending"
		if self.visitor_pass:
			populate_hospitality_request_from_pass(self)

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
