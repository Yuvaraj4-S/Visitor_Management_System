# Copyright (c) 2026, Harthesh and contributors
# For license information, please see license.txt

from frappe.model.document import Document
from frappe.utils import now_datetime


class HealthScreening(Document):
	def validate(self):
		if not self.screened_on:
			self.screened_on = now_datetime()

		if not self.screening_status:
			self.screening_status = "Pending"
