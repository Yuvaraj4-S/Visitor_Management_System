# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from visitormanagement.visitor_management import phone as vms_phone

# Floor below which a digit tail is too short to trust as a subscriber-number
# match — a bare 5-6 digit tail would collide across many unrelated numbers.
# National significant numbers for real mobile ranges run about 7-10 digits,
# so this stays under every country's minimum without risking false positives.
MIN_MOBILE_MATCH_DIGITS = 7


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

		THREAT MODEL — why a single weak field is never enough to block someone:
		A government ID number is a strong, near-unique identifier, so an exact
		match on it (normalised for case/spacing/separator evasion) is trusted
		on its own. Visitor name and mobile number are NOT strong on their own —
		this app runs in a country where thousands of people share a name, and
		phone numbers get reassigned, shared within a family, or mistyped. The
		previous version OR'd three independent checks together, so a barred
		person's namesake — a different person, different phone, different ID —
		was refused entry with no way for the receptionist to see it was a false
		match (reported live: "Ravi Kumar" / a different PAN / a different
		mobile got blocked by an entry for a different "Ravi Kumar"). Phone-only
		matching had the identical problem.

		So a weak identifier only ever blocks *here* when corroborated by a
		second one: name AND mobile must both agree with the same blacklist row
		before that counts as a match strong enough to stop the pass outright.
		Name-alone and mobile-alone no longer match anything in this method —
		but they are not simply dropped. A name-blacklisted person is real data
		an operator entered on purpose (the DocType accepts "Visitor Name + ID
		Proof Type" with no ID number and no mobile), and silently never acting
		on it again is worse than the over-blocking this replaced: over-blocking
		is visible and annoying, a match that vanishes is invisible and
		dangerous. `find_weak_match()` below is the companion for exactly that
		case — callers that want to warn a human instead of silently doing
		nothing should call it whenever this method returns None.

		Case/spacing evasion of a *genuine* match is still caught: the ID
		comparison strips separators and case as before, and the name half of
		the corroborated pair still collapses whitespace/case the same way.

		Used by Visitor Pass submit, Security Log check-in, and the gate API.
		Signature and return type are unchanged on purpose — those three
		callers keep hard-blocking on exactly what this returns, with no edits
		needed on their side.
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

		# Corroborated weak match: name AND mobile must both match the SAME
		# row. Either one alone is dropped silently on purpose — see the
		# threat-model note above.
		if visitor_name and mobile_number:
			tail = _mobile_match_tail(mobile_number)
			if tail:
				# Compare mobile on the trailing `len(tail)` digits (the
				# country-appropriate subscriber-number length, not a
				# hardcoded 10) so a stored local number still matches the
				# same person arriving with a country code. Collapse runs of
				# whitespace on both sides of the name so "Banned  Person"
				# still matches an entry stored as "Banned Person".
				rows = frappe.db.sql(
					"""
					SELECT name FROM `tabVisitor Blacklist`
					WHERE is_active = 1
					  AND LOWER(TRIM(REGEXP_REPLACE(visitor_name, '[[:space:]]+', ' ')))
					      = %(name)s
					  AND IFNULL(mobile_number, '') != ''
					  AND RIGHT(REGEXP_REPLACE(mobile_number, '[^0-9]', ''), %(len)s) = %(tail)s
					LIMIT 1
					""",
					{"name": _normalise_name(visitor_name), "tail": tail, "len": len(tail)},
				)
				if rows:
					return rows[0][0]

		return None

	@staticmethod
	def find_weak_match(visitor_name=None, mobile_number=None):
		"""A SINGLE weak identifier hit — name alone or mobile alone — that
		`find_active_match()` deliberately does not block on, as
		``{"name": <Visitor Blacklist name>, "matched_on": "name" | "mobile"}``,
		or None if neither matches.

		This is the other half of the fix in `find_active_match()`: a hit here
		must not hard-block (an unrelated namesake would be turned away with no
		way to tell it was a false match) but it must not disappear either (a
		security team that blacklisted someone by name believes that entry is
		live, and it would otherwise never fire again). Intended use is a
		visible, non-blocking warning — "this name/mobile matches an active
		blacklist entry, verify their ID" — that lets a human at the desk
		decide, instead of a hard `frappe.throw` and instead of silence.

		Deliberately a SEPARATE method rather than a change to
		`find_active_match()`'s return shape: that method's three existing
		callers (Visitor Pass, Security Log, the gate API) all treat any
		truthy return as "block", so folding a "weak, needs review" outcome
		into the same return value would either weaken their hard block or
		require editing all three call sites. Only Visitor Pass currently calls
		this one (see `_warn_weak_blacklist_match` in visitor_pass.py); Security
		Log's check-in and the gate API do not yet surface this warning — see
		FINDINGS.md for what that follow-up would look like.

		Checks name first, then mobile, and returns on the first hit — a
		caller that needs to know about a match on *both* identifiers
		independently is not the caller this exists for; find_active_match()
		already covers "both agree" as a strong, blocking match.
		"""
		if visitor_name:
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
				return {"name": rows[0][0], "matched_on": "name"}

		if mobile_number:
			tail = _mobile_match_tail(mobile_number)
			if tail:
				rows = frappe.db.sql(
					"""
					SELECT name FROM `tabVisitor Blacklist`
					WHERE is_active = 1
					  AND IFNULL(mobile_number, '') != ''
					  AND RIGHT(REGEXP_REPLACE(mobile_number, '[^0-9]', ''), %(len)s) = %(tail)s
					LIMIT 1
					""",
					{"tail": tail, "len": len(tail)},
				)
				if rows:
					return {"name": rows[0][0], "matched_on": "mobile"}

		return None


def _normalise_id(value):
	"""Uppercase, with the separators people vary on removed."""
	return re.sub(r"[\s\-/]", "", (value or "").upper())


def _normalise_name(value):
	"""Lowercase, trimmed, with internal whitespace runs collapsed to one space."""
	return re.sub(r"\s+", " ", (value or "").strip().lower())


def _digits_only(value):
	return re.sub(r"\D", "", value or "")


def _mobile_match_tail(mobile_number):
	"""Country-aware subscriber-number digits to match on, or None if untrustworthy.

	The previous logic assumed every country's mobile number is 10 digits and
	compared on a fixed last-10-digit tail. Outside that assumption (see
	phone.py's module docstring for why it does not hold) the length gate was
	never satisfied and the phone-match check silently never fired — a security
	control failing open with no warning.

	This parses the number with the same libphonenumber-backed helper the rest
	of the app already uses (visitormanagement.visitor_management.phone), and
	matches on the actual national significant number for that number's country
	— still stripped of country code, so a stored local number keeps matching
	the same person arriving with one. When the number cannot be parsed at all
	(garbage input, or a pre-existing row that never went through validation),
	this falls back to the raw digit string so those rows are not silently
	excluded, gated by MIN_MOBILE_MATCH_DIGITS to avoid matching on a
	near-meaningless short tail.

	Only the incoming number is parsed here — once per call, not once per
	Visitor Blacklist row — so this keeps the same cost profile as the query it
	replaces, which is called on every Visitor Pass submit and gate check-in.
	"""
	parsed = vms_phone.parse(mobile_number)
	digits = str(parsed.national_number) if parsed else _digits_only(mobile_number)
	return digits if len(digits) >= MIN_MOBILE_MATCH_DIGITS else None
