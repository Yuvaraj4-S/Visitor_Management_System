# Copyright (c) 2026, Harthesh and contributors
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
			self.blocked_on = str(now_datetime().date())

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
