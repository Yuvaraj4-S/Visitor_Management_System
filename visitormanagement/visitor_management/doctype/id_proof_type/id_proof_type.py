# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint


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
					_("Validation Regex is not a valid pattern: {0}").format(str(exc)),
					title=_("Invalid Regex"),
				)

		self._validate_visible_trailing_chars()

	def _validate_visible_trailing_chars(self):
		"""0 to MAX characters of a number may stay readable; blank means the default.

		A blank value arrives from an import or an API call that does not send the
		field. It is filled in rather than read as 0, because 0 ("show nothing") is
		a deliberate choice an administrator can make.
		"""
		from visitormanagement.visitor_management.id_masking import (
			DEFAULT_VISIBLE_TRAILING_CHARS,
			MAX_VISIBLE_TRAILING_CHARS,
		)

		if self.visible_trailing_chars in (None, ""):
			self.visible_trailing_chars = DEFAULT_VISIBLE_TRAILING_CHARS
			return
		value = cint(self.visible_trailing_chars)
		if not 0 <= value <= MAX_VISIBLE_TRAILING_CHARS:
			frappe.throw(
				_("Visible Trailing Characters must be between 0 and {0}.").format(
					MAX_VISIBLE_TRAILING_CHARS
				),
				title=_("Invalid Masking Setting"),
			)
		self.visible_trailing_chars = value

	def on_update(self):
		# validators.py caches the master; drop it so edits take effect at once.
		frappe.cache.delete_value("vms_id_proof_types")

	def on_trash(self):
		frappe.cache.delete_value("vms_id_proof_types")
