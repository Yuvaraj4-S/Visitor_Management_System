# Copyright (c) 2026, Harthesh and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime

from visitormanagement.visitor_management.lifecycle import generate_emergency_muster_records


class EmergencyEvent(Document):
	def validate(self):
		if not self.triggered_on:
			self.triggered_on = now_datetime()

		if not self.status:
			self.status = "Draft"

	def after_insert(self):
		if self.status == "Active":
			generate_emergency_muster_records(self)

	def on_update(self):
		if self.status == "Active":
			generate_emergency_muster_records(self)

	@frappe.whitelist()
	def regenerate_muster(self):
		return generate_emergency_muster_records(self)
