# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from visitormanagement.visitor_management.lifecycle import generate_emergency_muster_records


class EmergencyEvent(Document):
	def validate(self):
		if not self.triggered_on:
			self.triggered_on = now_datetime()

		if not self.status:
			self.status = "Draft"

		self._validate_closure()

	def _validate_closure(self):
		"""Block closing an emergency while muster accounting is still pending."""
		if self.status not in ("Closed", "Contained"):
			return

		# Only check on update (not on initial insert)
		if self.is_new():
			return

		pending = frappe.db.count(
			"Evacuation Muster",
			{"emergency_event": self.name, "accounted_status": ["in", ["Pending", "Missing"]]},
		)
		if pending:
			frappe.throw(
				_("Cannot mark this emergency as {0} — {1} visitor(s) are still unaccounted for. "
				  "Resolve all Evacuation Muster records (mark Accounted or Excused) first.").format(
					self.status, pending
				),
				title=_("Muster Incomplete"),
			)

	def after_insert(self):
		if self.status == "Active":
			generate_emergency_muster_records(self)

	def on_update(self):
		if self.status == "Active":
			generate_emergency_muster_records(self)

	@frappe.whitelist()
	def regenerate_muster(self):
		return generate_emergency_muster_records(self)
