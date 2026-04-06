# Copyright (c) 2026, Harthesh and contributors
# For license information, please see license.txt

from frappe.model.document import Document
from frappe.utils import now_datetime


class EvacuationMuster(Document):
	def validate(self):
		if self.accounted_status == "Accounted" and not self.accounted_time:
			self.accounted_time = now_datetime()
