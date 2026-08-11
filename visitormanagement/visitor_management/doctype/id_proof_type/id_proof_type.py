# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document


class IDProofType(Document):
	def validate(self):
		self.id_proof_type_name = (self.id_proof_type_name or "").strip()
		if not self.id_proof_type_name:
			frappe.throw(_("ID Proof Type is required."))

		if self.validation_method == "Regex":
			if not (self.validation_regex or "").strip():
				frappe.throw(
					_("A Validation Regex is required when the method is Regex."),
					title=_("Missing Pattern"),
				)
			try:
				re.compile(self.validation_regex)
			except re.error as exc:
				frappe.throw(
					_("Validation Regex is not a valid pattern: {0}").format(exc),
					title=_("Invalid Regex"),
				)

	def on_update(self):
		# validators.py caches the master; drop it so edits take effect at once.
		frappe.cache.delete_value("vms_id_proof_types")

	def on_trash(self):
		frappe.cache.delete_value("vms_id_proof_types")
