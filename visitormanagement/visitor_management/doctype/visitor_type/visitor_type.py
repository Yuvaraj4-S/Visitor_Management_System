# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class VisitorType(Document):

	def validate(self):
		self.visitor_type_name = (self.visitor_type_name or "").strip()
		if not self.visitor_type_name:
			frappe.throw(_("Visitor Type Name is required."))
