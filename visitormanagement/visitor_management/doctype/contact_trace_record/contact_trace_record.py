# For license information, please see license.txt

from frappe.model.document import Document


class ContactTraceRecord(Document):
	def validate(self):
		if self.time_out and not self.status:
			self.status = "Closed"
		elif not self.status:
			self.status = "Active"
