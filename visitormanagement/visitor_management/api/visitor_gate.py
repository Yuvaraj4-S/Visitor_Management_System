"""Gate API — resolves a QR scan to the Security Log the officer must complete.

Design note
-----------
These endpoints deliberately do NOT insert a Security Log. `SecurityLog.before_save`
requires `qr_code_scanned`, `photo_at_gate`, `id_proof_match` and `pass_photo_match`
on every Check-In / Check-Out, and those are captured by the officer at the gate.
A server-side insert with only `visitor_pass` + `event_type` therefore always
raises, which is exactly what the previous implementation did.

Instead each endpoint validates that the movement is legal *now* and returns the
event type plus a prefilled `/app/security-log/new` route. The officer completes
verification on that form and the existing `SecurityLog` hooks drive the pass
status, contact trace and host notification.
"""

import frappe
from frappe import _
from frappe.utils import cint, getdate, today, urlencode

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


def _default_gate(visitor_type):
	"""Same resolution the Security Log controller uses, so the prefilled gate
	matches what would be auto-assigned on save."""
	from visitormanagement.visitor_management.doctype.security_log.security_log import (
		_get_default_gate,
	)

	return _get_default_gate(visitor_type)


def _security_log_route(visitor_pass, event_type, gate_name):
	"""Relative desk route to a new Security Log with the gate fields prefilled.

	Relative (not `get_url_to_form`) so the link works on whatever host the
	gate terminal is using, without depending on `host_name` in site_config.
	"""
	query = urlencode(
		{
			"visitor_pass": visitor_pass,
			"event_type": event_type,
			"gate_name": gate_name or "",
		}
	)
	return f"/app/security-log/new?{query}"


def _gate_response(visitor_pass, event_type, message):
	visitor_type = frappe.db.get_value("Visitor Pass", visitor_pass, "visitor_type")
	gate_name = _default_gate(visitor_type)
	return {
		"visitor_pass": visitor_pass,
		"event_type": event_type,
		"gate_name": gate_name,
		"route": _security_log_route(visitor_pass, event_type, gate_name),
		"message": message,
	}


# ─────────────────────────────────────────────────────────
# CHECK-IN
# ─────────────────────────────────────────────────────────
@frappe.whitelist()
def visitor_checkin(docname):
	"""Validate that this pass may check in, and return the Security Log to open."""

	_assert_gate_permission()

	pass_state = frappe.db.get_value(
		"Visitor Pass", docname, ["status", "docstatus", "workflow_state"], as_dict=True
	)
	if not pass_state:
		frappe.throw(_("Visitor Pass {0} does not exist.").format(docname))

	if pass_state.status not in ENTRY_STATUSES:
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
	)


# ─────────────────────────────────────────────────────────
# CHECK-OUT
# ─────────────────────────────────────────────────────────
@frappe.whitelist()
def visitor_checkout(docname):
	"""Validate that this pass may check out, and return the Security Log to open."""

	_assert_gate_permission()

	status = frappe.db.get_value("Visitor Pass", docname, "status")
	if status is None:
		frappe.throw(_("Visitor Pass {0} does not exist.").format(docname))

	if status != "Checked-In":
		frappe.throw(_("Visitor is not currently checked in."))

	return _gate_response(
		docname,
		"Check-Out",
		_("Complete gate verification on the Security Log to check this visitor out."),
	)


# ─────────────────────────────────────────────────────────
# QR SCAN LOGIC
# ─────────────────────────────────────────────────────────
@frappe.whitelist()
def scan_qr_checkin(qr_data):
	"""
	Gatekeeper QR Scan Logic.
	Resolves the scanned pass and routes to check-in or check-out.

	Example QR:
	PASS:VP-2026-00001|VISITOR:John|VISIT_DATE:2026-06-01
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

	pass_id = parts.get("PASS")
	visitor_name = parts.get("VISITOR")
	visit_date = parts.get("VISIT_DATE")

	if not pass_id and (not visitor_name or not visit_date):
		frappe.throw(_("QR Code missing required information (PASS or VISITOR+DATE)."))

	# Find matching Visitor Pass
	if pass_id:
		doc_name = frappe.db.exists("Visitor Pass", pass_id)
	else:
		doc_name = frappe.db.get_value(
			"Visitor Pass",
			{
				"visitor_full_name": visitor_name,
				"visit_date": visit_date,
				"status": ["in", list(ENTRY_STATUSES)],
			},
			"name",
		)

	if not doc_name:
		frappe.throw(_("No valid Visitor Pass found for this QR code."))

	vp_info = frappe.db.get_value(
		"Visitor Pass",
		doc_name,
		["status", "visit_date", "id_proof_number", "visitor_full_name", "id_proof_type", "mobile_number"],
		as_dict=True,
	)
	doc_status = vp_info.status

	# Visit date must match today (checkout still allowed for an already Checked-In pass)
	if vp_info.visit_date and doc_status != "Checked-In":
		if getdate(vp_info.visit_date) != getdate(today()):
			frappe.throw(
				_("Visitor Pass {0} is for {1}, not today. QR code not valid for this date.").format(
					doc_name, vp_info.visit_date
				)
			)

	if doc_status in ENTRY_STATUSES:
		# Re-check blacklist only at entry — a Checked-In blacklisted visitor must still
		# be allowed to check out. Match by ID number first, fall back to name + ID type.
		from visitormanagement.visitor_management.doctype.visitor_blacklist.visitor_blacklist import (
			VisitorBlacklist,
		)

		blocked = VisitorBlacklist.find_active_match(
			id_proof_number=vp_info.id_proof_number,
			visitor_name=vp_info.visitor_full_name,
			id_proof_type=vp_info.id_proof_type,
			mobile_number=vp_info.mobile_number,
		)
		if blocked:
			frappe.throw(
				_("ACCESS DENIED: Visitor Pass {0} matches an active blacklist entry.").format(doc_name),
				title=_("Blacklisted"),
			)
		return visitor_checkin(doc_name)

	if doc_status == "Checked-In":
		return visitor_checkout(doc_name)

	if doc_status == "Checked-Out":
		frappe.throw(
			_("Visitor Pass {0} has already been used (Checked-Out). This QR code is now inactive.").format(
				doc_name
			)
		)

	frappe.throw(
		_("Visitor Pass {0} is in '{1}' state. Expected 'Approved', 'Items Verified', or 'Checked-In'.").format(
			doc_name, doc_status
		)
	)
