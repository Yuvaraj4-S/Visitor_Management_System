# Copyright (c) 2026, Harthesh and contributors
# For license information, please see license.txt

from frappe.model.document import Document

from visitormanagement.visitor_management.lifecycle import sync_hospitality_to_pass


class HospitalityRequest(Document):
	def validate(self):
		if not self.status:
			self.status = "Pending"

	def on_update(self):
		sync_hospitality_to_pass(self)
