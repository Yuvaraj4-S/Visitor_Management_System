# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class VisitorBlacklist(Document):
	def validate(self):
		# Reason is mandatory — can't blacklist someone without stating why (audit requirement).
		if not (self.reason or "").strip():
			frappe.throw(
				_("Reason is required for every blacklist entry (audit/compliance requirement)."),
				title=_("Reason Required"),
			)

		# Either ID proof number or visitor name + mobile must be present for lookup.
		if not self.id_proof_number and not (self.visitor_name and self.id_proof_type):
			frappe.throw(
				_("Provide either ID Proof Number or Visitor Name + ID Proof Type to identify the blacklisted person."),
				title=_("Identification Required"),
			)

		# Stamp who blocked and when, if not already set.
		if not self.blocked_by:
			self.blocked_by = frappe.session.user
		if not self.blocked_on:
			self.blocked_on = now_datetime().date()

		# Prevent duplicate active blacklist for the same ID proof number.
		if self.id_proof_number and self.is_active:
			existing = frappe.db.sql(
				"""
				SELECT name FROM `tabVisitor Blacklist`
				WHERE id_proof_number = %(id)s
				  AND is_active = 1
				  AND name != %(self_name)s
				LIMIT 1
				""",
				{"id": self.id_proof_number, "self_name": self.name or "NEW"},
			)
			if existing:
				frappe.throw(
					_("An active blacklist entry ({0}) already exists for this ID Proof Number.").format(
						existing[0][0]
					),
					title=_("Duplicate Blacklist"),
				)

	@staticmethod
	def find_active_match(id_proof_number=None, visitor_name=None, id_proof_type=None):
		"""Return the name of an active Visitor Blacklist entry matching either
		(a) the given id_proof_number, or (b) visitor_name + id_proof_type
		(case-insensitive, trimmed) when no number is provided / no number match.
		Returns None if no match. Used by VP submit, Security Log Check-In, and the gate API."""
		if id_proof_number:
			match = frappe.db.exists(
				"Visitor Blacklist",
				{"id_proof_number": id_proof_number, "is_active": 1},
			)
			if match:
				return match
		if visitor_name and id_proof_type:
			rows = frappe.db.sql(
				"""
				SELECT name FROM `tabVisitor Blacklist`
				WHERE LOWER(TRIM(visitor_name)) = LOWER(TRIM(%(name)s))
				  AND id_proof_type = %(type)s
				  AND is_active = 1
				LIMIT 1
				""",
				{"name": visitor_name, "type": id_proof_type},
			)
			if rows:
				return rows[0][0]
		return None
