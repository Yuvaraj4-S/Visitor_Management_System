"""Gate API — resolves a pass (or its QR code) to the Security Log the officer must complete.

Design note
-----------
These endpoints deliberately do NOT insert a Security Log. `SecurityLog.before_save`
requires what VMS Settings asks for on a Check-In / Check-Out — the QR scan, a live
gate photo, the two identity confirmations, the declared items — and those are
captured by the officer at the gate. A server-side insert with only `visitor_pass` +
`event_type` cannot satisfy them.

Instead each endpoint decides whether the movement is legal *now* and returns the
event type plus a prefilled `/app/security-log/new` route. The officer completes
verification on that form and the `SecurityLog` hooks drive the pass status,
contact trace and host notification.

Every endpoint answers the same question through the same code (`_movement_for`):
the "Check In" / "Check Out" buttons on a pass, the QR scan, and the Security Log
form when a pass is picked on it. Whatever would be refused when the log is saved
— a pass for another day, an unapproved or cancelled pass, a blacklisted visitor
on a site that blocks entry — is refused here first, so an officer is not walked
through photo, ID and item checks only to be turned back at the last step.

What a caller is told
---------------------
A pass the caller cannot read gets one answer, whether it does not exist, is
still a draft, is waiting for approval, was rejected or was cancelled:
`_refuse_unusable_pass`. Security may read a pass only once it is approved
(permissions.py), so the gate API must not be a way to learn the state of the
others. A pass the caller can read gets the specific reason.
"""

import frappe
from frappe import _
from frappe.utils import cint, formatdate, getdate, today, urlencode

from visitormanagement.visitor_management.doctype.security_log.security_log import (
	_get_default_gate,
	gate_blacklist_verdict,
	gate_policy,
	record_gate_blacklist,
)
from visitormanagement.visitor_management.id_masking import mask_id
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

# Read with frappe.db (no field-level permission), so the full ID number is
# available to the blacklist matcher. It never leaves this module: responses
# are built field by field and carry the masked form only.
_PASS_FIELDS = (
	"name",
	"status",
	"docstatus",
	"workflow_state",
	"visit_date",
	"multi_day_pass",
	"pass_valid_until",
	"visitor_type",
	"visitor_type_layout",
	"visitor_full_name",
	"mobile_number",
	"id_proof_type",
	"id_proof_number",
	"badge_number",
	"visitor_photo",
	"id_proof_scan",
	"person_to_visit",
	"host_name",
)


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


def _refuse_unusable_pass():
	"""The single answer for a pass the caller is not entitled to know about."""
	frappe.throw(
		_("This pass cannot be used at the gate. Ask the visitor to contact their host or the front office."),
		title=_("Pass Not Valid"),
	)


def _load_gate_pass(docname):
	"""The pass's gate fields, for a pass that exists and the caller may read."""
	vp = None
	if docname and isinstance(docname, str):
		vp = frappe.db.get_value("Visitor Pass", docname, list(_PASS_FIELDS), as_dict=True)
	if not vp or not frappe.has_permission("Visitor Pass", "read", doc=vp.name):
		_refuse_unusable_pass()
	return vp


def _valid_today(vp):
	"""True if today falls inside the days this pass is good for.

	The same window SecurityLog.before_save enforces: the visit date, or for a
	multi-day pass visit_date..pass_valid_until. Checking out does not end that
	window — a visitor who steps out for lunch comes back on the same pass — so
	the gate must offer re-entry for a Checked-Out pass while it is still valid.
	Without this the Security Log allowed re-entry but nothing could reach it:
	the endpoint and the QR scan both refused a Checked-Out pass outright.
	"""
	if not vp.visit_date:
		return False
	today_date = getdate(today())
	first_day = getdate(vp.visit_date)
	last_day = getdate(vp.pass_valid_until) if cint(vp.multi_day_pass) and vp.pass_valid_until else first_day
	return first_day <= today_date <= last_day


def _not_today_message(vp):
	if cint(vp.multi_day_pass) and vp.pass_valid_until:
		return _(
			"Visitor Pass {0} is valid from {1} to {2}, not today. It cannot be used at the gate today."
		).format(vp.name, formatdate(vp.visit_date), formatdate(vp.pass_valid_until))
	return _("Visitor Pass {0} is for {1}, not today. It cannot be used at the gate today.").format(
		vp.name, formatdate(vp.visit_date)
	)


def _default_gate(visitor_type):
	"""Same resolution the Security Log controller uses, so the prefilled gate
	matches what would be auto-assigned on save."""
	return _get_default_gate(visitor_type)


def _security_log_route(visitor_pass, event_type, gate_name, qr_scanned: bool = False):
	"""Relative desk route to a new Security Log with the gate fields prefilled.

	Relative (not `get_url_to_form`) so the link works on whatever host the
	gate terminal is using, without depending on `host_name` in site_config.

	`qr_scanned` is set only when the officer reached the gate through
	`scan_qr_checkin`, i.e. the visitor's QR has already been read at this gate.
	It carries `qr_code_scanned=1` onto the new log so an officer on a site with
	VMS Settings `qr_scan_required_at_gate` is not made to scan the same code a
	second time. The "Check In" / "Check Out" buttons on Visitor Pass never set
	it — no QR was read there, so the officer must still scan on the log.
	"""
	params = {
		"visitor_pass": visitor_pass,
		"event_type": event_type,
		"gate_name": gate_name or "",
	}
	if qr_scanned:
		params["qr_code_scanned"] = 1
	return f"/app/security-log/new?{urlencode(params)}"


def _checkin_refusal(vp):
	"""Why this pass may not check in right now, as (message, title), or None.

	The same rules, in the same order, as SecurityLog._validate_movement — the
	log's own check stays the authority; this is the early answer.
	"""
	if vp.status == "Cancelled" or cint(vp.docstatus) == 2:
		return _("Pass {0} has been cancelled. Entry refused.").format(vp.name), _("Cancelled Pass")

	if vp.status == "Checked-Out":
		if not _valid_today(vp):
			return (
				_("Visitor has already checked out and pass {0} is no longer valid today.").format(vp.name),
				_("Pass Not Valid Today"),
			)
	elif vp.status not in ENTRY_STATUSES:
		return (
			_("Pass must be 'Approved' or 'Items Verified' to Check-In. Current status: {0}").format(
				vp.status
			),
			_("Pass Not Approved"),
		)

	# `status` is an ordinary field, so it is only ever as trustworthy as every
	# path that can write it. The gate is the point where a record becomes
	# physical access, so it corroborates against the two things the workflow
	# engine itself owns: the pass must be submitted, and it must be sitting in
	# the state the workflow calls approved. A record claiming to be Approved
	# while still a draft never went through an approver.
	if not (cint(vp.docstatus) == 1 and vp.workflow_state in APPROVED_STATES):
		return (
			_(
				"Pass {0} is marked '{1}' but has not been through approval "
				"(workflow state: {2}). Entry refused — please contact the host."
			).format(vp.name, vp.status, vp.workflow_state or _("not set")),
			_("Unapproved Pass"),
		)

	# The Security Log refuses a check-in dated outside the pass's days. The
	# "Check In" button used to skip this, so a guard opened a pass for tomorrow,
	# took the photo, ticked the identity boxes, and was refused only on save.
	if not _valid_today(vp):
		return _not_today_message(vp), _("Pass Not Valid Today")

	return None


def _movement_for(vp):
	"""What may happen to this pass at the gate right now.

	Returns a dict: `event_type` ("Check-In", "Check-Out" or None), `refusal`
	((message, title) or None), `warning` ((message, title) or None) and
	`blacklist` (the verdict, for logging by the caller).

	A visitor who is inside may always leave: a Checked-In pass is a Check-Out,
	with no date or blacklist test (the goal is to keep a barred person out, not
	to trap them in).
	"""
	result = {"event_type": None, "refusal": None, "warning": None, "blacklist": None}

	if vp.status == "Checked-In":
		result["event_type"] = "Check-Out"
		return result

	refusal = _checkin_refusal(vp)
	if refusal:
		result["refusal"] = refusal
		return result

	# Blacklisting can happen after approval, so it is re-checked on arrival, and
	# VMS Settings "Blacklist Action" decides what a match means — the same
	# evaluation the Security Log applies when the check-in is saved.
	verdict = gate_blacklist_verdict(vp)
	result["blacklist"] = verdict
	if verdict and verdict["outcome"] == "block":
		result["refusal"] = (verdict["message"], verdict["title"])
		return result
	if verdict and verdict["outcome"] == "warn":
		result["warning"] = (verdict["message"], verdict["title"])

	result["event_type"] = "Check-In"
	return result


def _gate_response(vp, event_type, message, qr_scanned: bool = False, warning=None):
	gate_name = _default_gate(vp.visitor_type)
	response = {
		"visitor_pass": vp.name,
		"event_type": event_type,
		"gate_name": gate_name,
		"route": _security_log_route(vp.name, event_type, gate_name, qr_scanned=qr_scanned),
		"message": message,
	}
	if warning:
		response["warning"] = warning[0]
	return response


def _route_movement(vp, expected=None, qr_scanned: bool = False):
	"""Refuse, or return the Security Log route for the movement this pass allows.

	`expected` is the movement the caller asked for (a button), or None to take
	whichever applies (a scan).
	"""
	movement = _movement_for(vp)

	if expected == "Check-Out" and movement["event_type"] != "Check-Out":
		frappe.throw(_("Visitor is not currently checked in."))
	if expected == "Check-In" and movement["event_type"] == "Check-Out":
		frappe.throw(_("Visitor {0} is already Checked-In.").format(vp.visitor_full_name))

	if movement["refusal"]:
		message, title = movement["refusal"]
		if movement["blacklist"]:
			record_gate_blacklist(movement["blacklist"], refused=True)
		frappe.throw(msg=message, title=title)

	if movement["warning"]:
		message, title = movement["warning"]
		frappe.msgprint(msg=message, title=title, indicator="orange")

	if movement["event_type"] == "Check-In":
		text = _("Complete gate verification on the Security Log to check this visitor in.")
	else:
		text = _("Complete gate verification on the Security Log to check this visitor out.")
	return _gate_response(
		vp, movement["event_type"], text, qr_scanned=qr_scanned, warning=movement["warning"]
	)


# ─────────────────────────────────────────────────────────
# CHECK-IN
# ─────────────────────────────────────────────────────────
@frappe.whitelist()
def visitor_checkin(docname: str):
	"""Validate that this pass may check in, and return the Security Log to open."""
	return _checkin(docname)


def _checkin(docname, qr_scanned: bool = False):
	"""Body of `visitor_checkin`."""
	_assert_gate_permission()
	return _route_movement(_load_gate_pass(docname), expected="Check-In", qr_scanned=qr_scanned)


# ─────────────────────────────────────────────────────────
# CHECK-OUT
# ─────────────────────────────────────────────────────────
@frappe.whitelist()
def visitor_checkout(docname: str):
	"""Validate that this pass may check out, and return the Security Log to open."""
	return _checkout(docname)


def _checkout(docname, qr_scanned: bool = False):
	"""Body of `visitor_checkout`."""
	_assert_gate_permission()
	return _route_movement(_load_gate_pass(docname), expected="Check-Out", qr_scanned=qr_scanned)


# ─────────────────────────────────────────────────────────
# QR SCAN LOGIC
# ─────────────────────────────────────────────────────────
@frappe.whitelist()
def scan_qr_checkin(qr_data: str):
	"""
	Gatekeeper QR Scan Logic.
	Resolves the scanned pass and routes to check-in or check-out.

	Example QR:
	PASS:VP-2026-00001|VISITOR:John|VISIT_DATE:2026-06-01
	"""

	_assert_gate_permission()

	if not qr_data or not isinstance(qr_data, str):
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

	if not pass_id:
		# An older code without the pass number. Name + date identifies a pass
		# only if exactly one such pass is at the gate stage; two visitors of the
		# same name on the same day must not be told apart by guesswork.
		matches = frappe.get_all(
			"Visitor Pass",
			filters={
				"visitor_full_name": visitor_name,
				"visit_date": visit_date,
				"status": ["in", [*ENTRY_STATUSES, "Checked-In", "Checked-Out"]],
			},
			pluck="name",
			limit=2,
		)
		if len(matches) > 1:
			frappe.throw(
				_(
					"More than one pass matches this QR code. "
					"Select the visitor's pass by its Pass ID on the Security Log."
				),
				title=_("Pass Not Identified"),
			)
		pass_id = matches[0] if matches else None

	return _route_movement(_load_gate_pass(pass_id), qr_scanned=True)


# ─────────────────────────────────────────────────────────
# SECURITY LOG FORM
# ─────────────────────────────────────────────────────────
@frappe.whitelist()
def get_gate_context(visitor_pass: str):
	"""Everything the Security Log form shows about a pass picked on it.

	The form used to read the Visitor Pass itself (frappe.db.get_value, and the
	whole document for its items). That made the browser the place where the ID
	number was masked — the full number had already travelled to it — and where
	the movement was decided. This returns only what the officer needs:

	- the visitor's name, type, badge, the photo and ID scan to compare against,
	  the ID type, and the ID number MASKED (never the full number);
	- the host by name;
	- the declared items, to be verified one by one;
	- the gate this visitor type is routed to;
	- the movement the pass allows now (`event_type`), or why none (`refusal`),
	  and any blacklist warning — the same decision the other gate endpoints
	  and the log's own save make;
	- what the site requires before the event may be recorded (`policy`).

	A refusal is returned, not raised: the officer may still need to record an
	Alert against the pass. A pass the caller cannot read is refused outright
	with the uniform message.
	"""
	_assert_gate_permission()
	vp = _load_gate_pass(visitor_pass)
	movement = _movement_for(vp)

	items = frappe.get_all(
		"Visitor Item",
		filters={"parent": vp.name, "parenttype": "Visitor Pass", "parentfield": "visitor_items"},
		fields=["name", "item_name", "item_category", "quantity", "unit_of_measure", "serial_number"],
		order_by="idx asc",
	)

	context = {
		"visitor_pass": vp.name,
		"status": vp.status,
		"visit_date": vp.visit_date,
		"visitor_full_name": vp.visitor_full_name,
		"visitor_type": vp.visitor_type,
		"visitor_type_layout": vp.visitor_type_layout,
		"badge_number": vp.badge_number,
		"visitor_photo": vp.visitor_photo,
		"id_proof_scan": vp.id_proof_scan,
		"id_proof_type": vp.id_proof_type,
		"id_proof_number_masked": mask_id(vp.id_proof_type, vp.id_proof_number),
		"host_name": vp.host_name or _employee_name(vp.person_to_visit),
		"default_gate": _default_gate(vp.visitor_type),
		"items": items,
		"event_type": movement["event_type"],
		"refusal": None,
		"warning": None,
		"policy": gate_policy(),
	}
	if movement["refusal"]:
		context["refusal"] = {"message": movement["refusal"][0], "title": movement["refusal"][1]}
	if movement["warning"]:
		context["warning"] = {"message": movement["warning"][0], "title": movement["warning"][1]}
	return context


def _employee_name(employee):
	if not employee:
		return None
	return frappe.db.get_value("Employee", employee, "employee_name")
