"""Gate API — resolves a QR scan to the Security Log the officer must complete.

Design note
-----------
These endpoints deliberately do NOT insert a Security Log. `SecurityLog.before_save`
requires `qr_code_scanned`, `photo_at_gate`, `id_proof_match` and `pass_photo_match`
on every Check-In / Check-Out, and those are captured by the officer at the gate.
A server-side insert with only `visitor_pass` + `event_type` therefore always
raises, which is exactly what the previous implementation did.

Instead each endpoint validates that the movement is legal *now* and returns the
event type plus a prefilled `/desk/security-log/new` route. The officer completes
verification on that form and the existing `SecurityLog` hooks drive the pass
status, contact trace and host notification.
"""

import frappe
from frappe import _
from frappe.utils import cint, now_datetime, urlencode

from visitormanagement.visitor_management import gate_timing
from visitormanagement.visitor_management import settings as vms_settings
from visitormanagement.visitor_management.workflow_builder import APPROVED_STATES

ENTRY_STATUSES = ("Approved", "Items Verified")

# APPROVED_STATES -- the workflow states that genuinely represent an approved
# pass, checked alongside docstatus so the gate corroborates `status` against
# what the workflow engine itself recorded -- is imported from
# workflow_builder, the module that actually generates the "Approved" state,
# rather than redefined here. It used to be a separate `("Approved",)` literal
# in this file AND another one in visitor_pass.py (as GATE_APPROVED_STATES);
# changing the state name would have had to touch three places to stay
# correct, with nothing forcing that.


def _assert_gate_permission():
	"""Only staff allowed to create Security Logs (Security / System Manager)
	may operate the gate.

	These endpoints are @frappe.whitelist() (login required, NOT guest), but
	that alone only proves the caller is authenticated — it does not enforce a
	role. Without this check, any logged-in user (e.g. a plain Employee, who has
	no `create` permission on Security Log) could probe passes and drive the
	gate flow.
	"""
	if not frappe.has_permission("Security Log", "create"):
		frappe.throw(
			_("You are not permitted to record gate entry/exit. Security role required."),
			frappe.PermissionError,
		)


def _valid_today(vp):
	"""True if today falls inside the days this pass is good for.

	The same window SecurityLog.before_save enforces: the visit date, or for a
	multi-day pass visit_date..pass_valid_until. Checking out does not end that
	window — a visitor who steps out for lunch comes back on the same pass — so
	the gate must offer re-entry for a Checked-Out pass while it is still valid.
	Without this the Security Log allowed re-entry but nothing could reach it:
	the endpoint and the QR scan both refused a Checked-Out pass outright.

	An overnight visitor scanned after midnight (00:30 on a 22:00-06:00 shift)
	is on the night that began on a pass day, and is valid: reading the calendar
	date alone turned them away at the barrier (gate_timing.on_a_pass_day).
	"""
	return gate_timing.on_a_pass_day(vp, now_datetime())


def _default_gate(visitor_type):
	"""Same resolution the Security Log controller uses, so the prefilled gate
	matches what would be auto-assigned on save — the guard's own gate where VMS
	Settings restricts Security to their assigned gates."""
	from visitormanagement.visitor_management.doctype.security_log.security_log import (
		default_gate_for_user,
	)

	return default_gate_for_user(visitor_type)


def _members_inside(visitor_pass):
	"""How many of a group pass's members the gate has recorded as inside."""
	return frappe.db.count(
		"Visitor Group Member",
		{"parenttype": "Visitor Pass", "parent": visitor_pass, "checked_in": 1},
	)


def _security_log_route(visitor_pass, event_type, gate_name, scanned_qr_token=None):
	"""Relative desk route to a new Security Log with the gate fields prefilled.

	Relative (not `get_url_to_form`) so the link works on whatever host the
	gate terminal is using, without depending on `host_name` in site_config.

	A scan's token rides along, so the log the officer saves is recorded as
	scanned: the Security Log decides that from the token itself.
	"""
	values = {
		"visitor_pass": visitor_pass,
		"event_type": event_type,
		"gate_name": gate_name or "",
	}
	if scanned_qr_token:
		values["scanned_qr_token"] = scanned_qr_token
	return f"/desk/security-log/new?{urlencode(values)}"


def _gate_response(visitor_pass, event_type, message, scanned_qr_token=None):
	vp = frappe.db.get_value(
		"Visitor Pass", visitor_pass, ["visitor_type", *gate_timing.PASS_FIELDS], as_dict=True
	)
	gate_name = _default_gate(vp.visitor_type)
	return {
		"visitor_pass": visitor_pass,
		"event_type": event_type,
		"gate_name": gate_name,
		"route": _security_log_route(visitor_pass, event_type, gate_name, scanned_qr_token),
		"message": message,
		# Outside the booked time, on the server's clock: a warning for the guard,
		# never a refusal (gate_timing). The Security Log records the same note.
		"timing_warning": gate_timing.timing_note(vp, event_type, now_datetime()),
	}


# ─────────────────────────────────────────────────────────
# CHECK-IN
# ─────────────────────────────────────────────────────────
@frappe.whitelist()
def visitor_checkin(docname: str):
	"""Validate that this pass may check in, and return the Security Log to open."""
	return _checkin_response(docname)


def _checkin_response(docname, scanned_qr_token=None):
	"""visitor_checkin, carrying the token of the QR scan that led here, if any."""
	_assert_gate_permission()

	pass_state = frappe.db.get_value(
		"Visitor Pass",
		docname,
		["status", "docstatus", "workflow_state", *gate_timing.PASS_FIELDS],
		as_dict=True,
	)
	if not pass_state:
		frappe.throw(_("Visitor Pass {0} does not exist.").format(docname))

	if pass_state.status == "Cancelled" or cint(pass_state.docstatus) == 2:
		frappe.throw(
			_("Pass {0} has been cancelled. Entry refused.").format(docname), title=_("Cancelled Pass")
		)

	if pass_state.status == "Checked-Out":
		if not _valid_today(pass_state):
			frappe.throw(
				_("Visitor has already checked out and pass {0} is no longer valid today.").format(docname)
			)
	elif pass_state.status not in ENTRY_STATUSES:
		frappe.throw(
			_("Pass must be 'Approved' or 'Items Verified' to Check-In. Current status: {0}").format(
				pass_state.status
			)
		)

	# `status` is an ordinary field, so it is only ever as trustworthy as every
	# path that can write it. The gate is the point where a record becomes
	# physical access, so it corroborates against the two things the workflow
	# engine itself owns: the pass must be submitted, and it must be sitting in
	# the state the workflow calls approved. A record claiming to be Approved
	# while still a draft never went through an approver.
	if not (cint(pass_state.docstatus) == 1 and pass_state.workflow_state in APPROVED_STATES):
		frappe.throw(
			_(
				"Pass {0} is marked '{1}' but has not been through approval "
				"(workflow state: {2}). Entry refused — please contact the host."
			).format(docname, pass_state.status, pass_state.workflow_state or _("not set")),
			title=_("Unapproved Pass"),
		)

	return _gate_response(
		docname,
		"Check-In",
		_("Complete gate verification on the Security Log to check this visitor in."),
		scanned_qr_token,
	)


# ─────────────────────────────────────────────────────────
# CHECK-OUT
# ─────────────────────────────────────────────────────────
@frappe.whitelist()
def visitor_checkout(docname: str):
	"""Validate that this pass may check out, and return the Security Log to open."""
	return _checkout_response(docname)


def _checkout_response(docname, scanned_qr_token=None):
	"""visitor_checkout, carrying the token of the QR scan that led here, if any."""
	_assert_gate_permission()

	status = frappe.db.get_value("Visitor Pass", docname, "status")
	if status is None:
		frappe.throw(_("Visitor Pass {0} does not exist.").format(docname))

	# Group members still inside after the lead visitor has left must be able to
	# leave too: the Security Log checks them out on a log of their own.
	if status != "Checked-In" and not (status == "Checked-Out" and _members_inside(docname)):
		frappe.throw(_("Visitor is not currently checked in."))

	return _gate_response(
		docname,
		"Check-Out",
		_("Complete gate verification on the Security Log to check this visitor out."),
		scanned_qr_token,
	)


# ─────────────────────────────────────────────────────────
# QR SCAN LOGIC
# ─────────────────────────────────────────────────────────
@frappe.whitelist()
def scan_qr_checkin(qr_data: str):
	"""
	Gatekeeper QR Scan Logic.
	Resolves the scanned pass and routes to check-in or check-out.

	Example QR:
	PASS:VP-2026-00001|TOKEN:<secret>|VISITOR:John|VISIT_DATE:2026-06-01
	"""

	_assert_gate_permission()

	if not qr_data:
		frappe.throw(_("Invalid QR Data."))

	# Parse QR string into dictionary
	parts = {}
	for p in qr_data.split("|"):
		if ":" in p:
			key, value = p.split(":", 1)
			parts[key.strip()] = value.strip()

	# The pass is identified by PASS alone. A QR without it used to be matched on
	# the visitor's name and visit date — both printed in the clear, so anyone
	# could make a QR that opened somebody else's pass.
	pass_id = parts.get("PASS")
	if not pass_id:
		frappe.throw(_("This QR code is not a visitor pass. Ask the visitor for their pass email."))

	doc_name = frappe.db.exists("Visitor Pass", pass_id)
	if not doc_name:
		frappe.throw(_("No valid Visitor Pass found for this QR code."))

	vp_info = frappe.db.get_value(
		"Visitor Pass",
		doc_name,
		["name", "status", *gate_timing.PASS_FIELDS],
		as_dict=True,
	)
	doc_status = vp_info.status

	# And TOKEN, when the pass has one: the same check the Security Log makes
	# on save (security_log.qr_token_matches), so a copied or superseded QR is
	# refused here rather than after the officer has filled in the form.
	from visitormanagement.visitor_management.doctype.security_log.security_log import (
		QR_SCAN_WITHOUT_TOKEN,
		qr_token_matches,
	)

	scanned_token = parts.get("TOKEN") or QR_SCAN_WITHOUT_TOKEN
	if frappe.get_meta("Visitor Pass").has_field("qr_token"):
		vp_info.qr_token = frappe.db.get_value("Visitor Pass", doc_name, "qr_token")
	qr_token_matches(vp_info, scanned_token)

	# Group members left inside after the lead's check-out are let out even once
	# the pass's day is over: nobody is kept in over a date.
	if doc_status == "Checked-Out" and not _valid_today(vp_info) and _members_inside(doc_name):
		return _checkout_response(doc_name, scanned_token)

	# The pass must be valid today (checkout still allowed for an already Checked-In pass).
	# A multi-day pass is valid on every day of its window, not only the first.
	if vp_info.visit_date and doc_status != "Checked-In":
		if not _valid_today(vp_info):
			frappe.throw(
				_("Visitor Pass {0} is for {1}, not today. QR code not valid for this date.").format(
					doc_name, vp_info.visit_date
				)
			)

	# Checked-Out on a day the pass is still valid is a re-entry (the date check
	# above has already refused one that is not).
	if doc_status in ENTRY_STATUSES or doc_status == "Checked-Out":
		# Re-check blacklist only at entry — a Checked-In blacklisted visitor must still
		# be allowed to check out. The same screening as the Security Log's own
		# check-in: the lead visitor and everyone arriving with them, acted on as
		# VMS Settings says. Where a match does not stop entry, the Security Log
		# the officer goes on to save warns and records it.
		from visitormanagement.visitor_management.doctype.security_log.security_log import (
			gate_blacklist_matches,
			refuse_blacklisted_entry,
		)

		if vms_settings.blacklist_action() == "Block Entry":
			matches = gate_blacklist_matches(frappe.get_doc("Visitor Pass", doc_name))
			if matches:
				label, blacklist, member = matches[0]
				# The refusal rolls this request back, so the Alert record and the
				# mail to the Security Alert Roles are queued outside it first.
				refuse_blacklisted_entry(
					doc_name, blacklist, label, member_row=member.name if member else None
				)
				frappe.throw(
					_("ACCESS DENIED: Visitor Pass {0} matches an active blacklist entry.").format(doc_name),
					title=_("Blacklisted"),
				)
		return _checkin_response(doc_name, scanned_token)

	if doc_status == "Checked-In":
		return _checkout_response(doc_name, scanned_token)

	frappe.throw(
		_(
			"Visitor Pass {0} is in '{1}' state. Expected 'Approved', 'Items Verified', or 'Checked-In'."
		).format(doc_name, doc_status)
	)
