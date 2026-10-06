# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_datetime


class ContactTraceRecord(Document):
	def validate(self):
		self._validate_time_window()
		self._validate_linked_records_agree()
		self._set_status()

	def _validate_time_window(self):
		"""A reversed window silently corrupts the thing this doctype exists for.

		Contact tracing answers "who was in this area between X and Y". A record
		whose time_out precedes its time_in either matches nobody or matches an
		absurd range, and nothing downstream would flag it.
		"""
		if not (self.time_in and self.time_out):
			return

		time_in, time_out = get_datetime(self.time_in), get_datetime(self.time_out)
		if time_out < time_in:
			frappe.throw(
				_("Time Out ({0}) cannot be before Time In ({1}).").format(time_out, time_in),
				title=_("Invalid Time Window"),
			)

	def _validate_linked_records_agree(self):
		"""The Security Log must belong to the Visitor Pass named on this record.

		Both links are free-form pickers, so mismatching them is easy and would
		attribute one visitor's movements to another — the worst possible error
		in a trace record.
		"""
		if not (self.visitor_pass and self.security_log):
			return

		log_pass = frappe.db.get_value("Security Log", self.security_log, "visitor_pass")
		if log_pass and log_pass != self.visitor_pass:
			frappe.throw(
				_("Security Log {0} belongs to Visitor Pass {1}, not {2}.").format(
					self.security_log, log_pass, self.visitor_pass
				),
				title=_("Mismatched Records"),
			)

	def _set_status(self):
		if self.time_out and not self.status:
			self.status = "Closed"
		elif not self.status:
			self.status = "Active"
