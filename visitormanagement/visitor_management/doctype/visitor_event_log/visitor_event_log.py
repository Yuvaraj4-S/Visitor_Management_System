# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class VisitorEventLog(Document):
	def validate(self):
		# Visitor Pass must exist (defensive — Link field enforces this, but be explicit).
		if self.visitor_pass and not frappe.db.exists("Visitor Pass", self.visitor_pass):
			frappe.throw(
				_("Referenced Visitor Pass {0} does not exist.").format(self.visitor_pass),
				title=_("Invalid Visitor Pass"),
			)

		# event_time defaults to now if not provided.
		if not self.event_time:
			self.event_time = now_datetime()
