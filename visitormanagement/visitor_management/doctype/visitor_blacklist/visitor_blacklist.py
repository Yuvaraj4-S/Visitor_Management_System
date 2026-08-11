# For license information, please see license.txt

import re

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
	def find_active_match(
		id_proof_number=None, visitor_name=None, id_proof_type=None, mobile_number=None
	):
		"""Name of an active blacklist entry matching this person, or None.

		Matching is deliberately about the *person*, not the paperwork they
		happen to present. An exact-string comparison let a barred visitor walk
		back in by changing something cosmetic — presenting a Passport instead of
		the PAN they were barred on, or typing their ID with a leading space or
		their name with a double space, all of which still pass format
		validation. So the ID is compared in a normalised form, the name fallback
		no longer requires the document type to match, and the mobile number is
		accepted as a third key.

		Used by Visitor Pass submit, Security Log check-in, and the gate API.
		"""
		if id_proof_number:
			rows = frappe.db.sql(
				"""
				SELECT name FROM `tabVisitor Blacklist`
				WHERE is_active = 1
				  AND REPLACE(REPLACE(REPLACE(UPPER(id_proof_number), ' ', ''), '-', ''), '/', '')
				      = %(id)s
				LIMIT 1
				""",
				{"id": _normalise_id(id_proof_number)},
			)
			if rows:
				return rows[0][0]

		if visitor_name:
			# Collapse runs of whitespace on both sides so "Banned  Person"
			# cannot slip past an entry stored as "Banned Person".
			rows = frappe.db.sql(
				"""
				SELECT name FROM `tabVisitor Blacklist`
				WHERE is_active = 1
				  AND LOWER(TRIM(REGEXP_REPLACE(visitor_name, '[[:space:]]+', ' ')))
				      = %(name)s
				LIMIT 1
				""",
				{"name": _normalise_name(visitor_name)},
			)
			if rows:
				return rows[0][0]

		if mobile_number:
			digits = _digits_only(mobile_number)
			if len(digits) >= 10:
				# Compare on the last 10 digits so a stored local number still
				# matches the same person arriving with a country code.
				rows = frappe.db.sql(
					"""
					SELECT name FROM `tabVisitor Blacklist`
					WHERE is_active = 1
					  AND IFNULL(mobile_number, '') != ''
					  AND RIGHT(REGEXP_REPLACE(mobile_number, '[^0-9]', ''), 10) = %(tail)s
					LIMIT 1
					""",
					{"tail": digits[-10:]},
				)
				if rows:
					return rows[0][0]

		return None


def _normalise_id(value):
	"""Uppercase, with the separators people vary on removed."""
	return re.sub(r"[\s\-/]", "", (value or "").upper())


def _normalise_name(value):
	"""Lowercase, trimmed, with internal whitespace runs collapsed to one space."""
	return re.sub(r"\s+", " ", (value or "").strip().lower())


def _digits_only(value):
	return re.sub(r"\D", "", value or "")
