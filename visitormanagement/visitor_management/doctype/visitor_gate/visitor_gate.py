# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class VisitorGate(Document):
	def validate(self):
		self.gate_name = (self.gate_name or "").strip()
		if not self.gate_name:
			frappe.throw(_("Gate Name is required."))
