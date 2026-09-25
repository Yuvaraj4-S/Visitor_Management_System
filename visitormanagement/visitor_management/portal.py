import base64
import binascii
import json
import os

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, get_datetime, now_datetime

from visitormanagement.visitor_management import settings as vms_settings

# Guests may only upload genuine images / PDFs for ID proof and photo. We trust
# neither the file extension nor the client-sent MIME type alone — the decoded
# content must also start with a matching magic-byte signature. The policy is
# defined once in portal_upload, which enforces the same rules at upload time.
from visitormanagement.visitor_management.portal_upload import (
	ALLOWED_EXTENSIONS as ALLOWED_UPLOAD_EXTENSIONS,
	MAX_BYTES as MAX_UPLOAD_BYTES,
	SIGNATURES as _UPLOAD_SIGNATURES,
)


def _validate_upload(filename, content):
	ext = os.path.splitext(filename or "")[1].lower()
	if ext not in ALLOWED_UPLOAD_EXTENSIONS:
		frappe.throw(_("Only JPG, PNG or PDF files are allowed for ID proof and photo."))
	if len(content) > MAX_UPLOAD_BYTES:
		frappe.throw(_("File is too large. The maximum allowed size is 5 MB."))
	if not any(content.startswith(sig) for sig in _UPLOAD_SIGNATURES):
		frappe.throw(
			_("The uploaded file is not a valid JPG, PNG or PDF. Please re-upload a genuine image or PDF.")
		)

from visitormanagement.visitor_management.doctype.visitor_invitation.visitor_invitation import (
	get_valid_invitation_by_token,
)
from visitormanagement.visitor_management.validators import (
	id_proof_error_message,
	validate_id,
)



# Ceiling for the identity-keyed limiter below (socket peer, unless a declared
# proxy forwarded the request — see portal_upload._rate_limit_identity). This
# used to be an independent hardcoded 20 that only claimed to mirror the
# @rate_limit decorator on submit_pre_registration; raising either one did
# nothing to the other. It now reads vms_settings.max_portal_submissions_per_hour()
# (default 20) so lowering the setting takes effect immediately. Raising it
# above the decorator's fixed 20 still has no effect — see the comment on
# submit_pre_registration for why that half cannot be made dynamic.
DEFAULT_MAX_SUBMISSIONS_PER_HOUR = 20


def _max_submissions_per_hour():
	getter = getattr(vms_settings, "max_portal_submissions_per_hour", None)
	if not callable(getter):
		return DEFAULT_MAX_SUBMISSIONS_PER_HOUR
	try:
		value = cint(getter())
	except Exception:
		return DEFAULT_MAX_SUBMISSIONS_PER_HOUR
	return value if value else DEFAULT_MAX_SUBMISSIONS_PER_HOUR


def _enforce_submission_rate_limit():
	from visitormanagement.visitor_management.portal_upload import (
		_count_or_throw,
		_rate_limit_identity,
	)

	_count_or_throw(
		f"vms:portal-submit:{_rate_limit_identity()}",
		_max_submissions_per_hour(),
		_("Too many pre-registrations from this connection. Please wait a while and try again."),
	)


def _looks_like_file_url(payload):
	"""True when the form sent a reference to a File it already uploaded.

	With `allow_guests_to_upload_files` on (see setup._allow_portal_uploads) the
	attach controls POST to `upload_file` and put the resulting URL in the doc,
	instead of inlining the bytes as a data URI. Both shapes have to be accepted:
	the data URI is still what arrives from any client that could not upload.
	"""
	value = (payload or "").strip()
	if value.startswith(("/files/", "/private/files/")):
		return True
	return value.startswith("http") and "/files/" in value[:200]


def _adopt_uploaded_file(payload, upload_key=None):
	"""Take over a File the attach control already created.

	Returns (file_url, file_name). The bytes are on disk, so validation reads the
	stored File rather than a base64 blob, and the file is forced private — the
	uploader may have created it public, and an ID scan must never sit under a
	guessable public URL.
	"""
	value = (payload or "").strip()
	file_url = value
	if value.startswith("http"):
		file_url = "/" + value.split("/", 3)[3] if len(value.split("/", 3)) > 3 else value

	file_name = frappe.db.get_value("File", {"file_url": file_url}, "name")
	if not file_name:
		frappe.throw(_("The uploaded file could not be found. Please re-upload it."))

	file_doc = frappe.get_doc("File", file_name)
	_assert_file_is_adoptable(file_doc)
	_assert_upload_key(file_doc, upload_key)
	# get_content() decodes to str whenever the bytes happen to be decodable,
	# which breaks the magic-byte check below. Read the file as bytes so the
	# signature comparison sees exactly what was written.
	with open(file_doc.get_full_path(), "rb") as handle:
		content = handle.read(MAX_UPLOAD_BYTES + 1)
	_validate_upload(file_doc.file_name, content)
	if not file_doc.is_private:
		file_doc.db_set("is_private", 1, update_modified=False)
		file_url = file_doc.file_url

	return file_url, file_name


def _claim_invitation(invitation):
	"""Consume a single-use invitation atomically, or refuse the submission.

	The UPDATE carries the precondition, so concurrent redemptions serialise in
	the database instead of racing between a read and a later write. `rowcount`
	is the authority on who won: 0 rows means somebody else already submitted
	this invitation.
	"""
	frappe.db.sql(
		"""
		UPDATE `tabVisitor Invitation`
		SET invitation_status = 'Submitted', form_submitted_on = %(now)s
		WHERE name = %(name)s AND invitation_status != 'Submitted'
		""",
		{"name": invitation.name, "now": now_datetime()},
	)
	if not frappe.db._cursor.rowcount:
		frappe.throw(
			_("This invitation has already been used. Please ask your host for a new link."),
			frappe.ValidationError,
		)


# How long a File the portal itself created may sit unattached before it stops
# being adoptable. A real submission uploads its two files and then submits
# within minutes; anything older is either an abandoned upload or a URL that
# has had time to leak (a proxy log, a browser history entry, a second person
# on a shared reception kiosk). Hardcoded rather than a VMS Settings field —
# it is a security bound on this flow's own shape, not a policy an admin
# should be invited to loosen, matching how portal_upload.py's own MAX_BYTES /
# MAX_GUEST_UPLOADS_PER_HOUR are plain constants for the same reason.
_ADOPTABLE_FILE_MAX_AGE_SECONDS = 30 * 60


def _assert_file_is_adoptable(file_doc):
	"""Refuse to take over a File that is not this submission's own recent upload.

	The submission names its attachments by URL, and the caller is anonymous, so
	the URL is entirely attacker-chosen. Without any check here a guest could
	name another visitor's ID scan: the file would be re-parented onto the
	attacker's own pass — detaching it from the victim's record and making it
	readable to whoever can read the attacker's pass — and any public asset
	could be flipped private and stolen the same way.

	`owner != frappe.session.user` used to be the second half of this check, on
	the theory that a file this flow produced is "owned by the same anonymous
	session that is now submitting". That theory is false: every guest request
	shares Frappe's one literal `Guest` session
	(frappe/sessions.py: "all guests share the same 'Guest' session") — there is
	no anonymous identity to compare, so the comparison was always `'Guest' ==
	'Guest'` and passed for any two unrelated anonymous uploads. It caught
	nothing.

	What is actually enforced now:
	  - unattached (attached_to_doctype/attached_to_name unset) — unchanged,
	    still true of every File this flow legitimately produces, since it is
	    uploaded before the Visitor Pass it will be linked to exists;
	  - recently created — closes the exposure that matters in practice: a
	    stale file_url from an earlier, unrelated session (leaked through a
	    proxy log, a browser history entry, or a second walk-in on a shared
	    reception kiosk reusing the same tab) stops being adoptable once it is
	    older than a normal single sitting at the form.
	These two cannot tell apart two anonymous uploads made within the same short
	window on a shared kiosk — every Guest request is indistinguishable from
	every other. `_assert_upload_key` closes that: the page's own random key,
	sent with the upload and again with the submission.
	"""
	if file_doc.attached_to_doctype or file_doc.attached_to_name:
		frappe.throw(
			_("That file is already attached to another record. Please upload your own copy."),
			frappe.PermissionError,
		)

	age_seconds = (now_datetime() - get_datetime(file_doc.creation)).total_seconds()
	if age_seconds > _ADOPTABLE_FILE_MAX_AGE_SECONDS or age_seconds < 0:
		frappe.throw(
			_("That upload has expired. Please upload your ID proof and photo again and submit."),
			frappe.PermissionError,
		)


def _assert_upload_key(file_doc, upload_key):
	"""Only the submission that made an upload may claim it.

	The recency check above cannot tell two anonymous visitors apart; the upload
	key can — see portal_upload.UPLOAD_KEY_FIELD.
	"""
	from visitormanagement.visitor_management.portal_upload import upload_key_matches

	if file_doc.owner != "Guest":
		# A signed-in user's upload has a real owner to compare against, and the
		# key is only recorded for guest uploads.
		if file_doc.owner != frappe.session.user:
			frappe.throw(
				_("That upload does not belong to you. Please upload your own copy."),
				frappe.PermissionError,
			)
		return

	if not upload_key_matches(file_doc.file_url, upload_key):
		frappe.throw(
			_("That upload does not belong to this form. Please upload your ID proof and photo again and submit."),
			frappe.PermissionError,
		)


def _extract_file_payload(payload, fallback_filename=None):
	if not payload:
		return fallback_filename, None

	filename = fallback_filename
	data = payload

	if "," in payload:
		prefix, remainder = payload.split(",", 1)
		if remainder.startswith("data:"):
			filename = prefix or fallback_filename
			data = remainder
		elif prefix.startswith("data:"):
			data = remainder
		else:
			data = remainder

	if "," in data and data.split(",", 1)[0].startswith("data:"):
		data = data.split(",", 1)[1]

	try:
		return filename, base64.b64decode(data)
	except (binascii.Error, ValueError):
		frappe.throw(_("Uploaded file is corrupted or in an unsupported format. Please re-upload."))


def _store_file(filename, content):
	"""Store an uploaded ID/photo as a PRIVATE File and return (file_url, file_name).

	is_private=1 keeps sensitive ID documents out of the public /files directory.
	The caller links the file to its Visitor Pass once the pass exists (see
	_attach_file_to_pass) so that staff with read access to the pass can still
	view it — a private *standalone* file would otherwise be visible only to its
	owner and System Managers.
	"""
	if not content:
		return None, None

	_validate_upload(filename, content)

	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": filename,
			"is_private": 1,
			"content": content,
		}
	)
	file_doc.insert(ignore_permissions=True)
	return file_doc.file_url, file_doc.name


def _read_upload(payload, fallback_filename, upload_key=None):
	"""Normalise whatever the portal sent for an attachment to (file_url, file_name)."""
	if not payload:
		return None, None
	if not isinstance(payload, str):
		frappe.throw(_("Uploaded file is corrupted or in an unsupported format. Please re-upload."))

	if _looks_like_file_url(payload):
		return _adopt_uploaded_file(payload, upload_key)

	filename, content = _extract_file_payload(payload, fallback_filename)
	if not content:
		# A payload that decodes to nothing ("data:...;base64,") must not count as
		# the required document.
		frappe.throw(_("The uploaded file is empty. Please upload it again."))
	return _store_file(filename, content)


def _attach_file_to_pass(file_name, pass_name, fieldname):
	"""Link a stored private File to the Visitor Pass it belongs to, so the
	framework's file-permission check grants access to anyone who can read the
	pass (frappe/core/doctype/file/file.py:has_permission)."""
	if not file_name:
		return
	frappe.db.set_value(
		"File",
		file_name,
		{
			"attached_to_doctype": "Visitor Pass",
			"attached_to_name": pass_name,
			"attached_to_field": fieldname,
		},
		update_modified=False,
	)


def _normalize_mobile_number(number, country_code=None):
	"""Validate and normalise a number submitted through the public portal.

	This used to assume the last ten digits were the subscriber number and
	everything before them a country code, which turned malformed input into a
	confident-looking result (`999999999999999` became `+99999-9999999999`) and
	rejected valid numbers from countries that do not use ten digits.
	"""
	from visitormanagement.visitor_management import phone

	value = (number or "").strip()
	if not value:
		return value

	region = None
	code = "".join(c for c in (country_code or "") if c.isdigit())
	if code:
		# The portal's ISD selector wins over the site default when the visitor
		# has chosen one.
		region = phonenumbers_region_for_code(code)

	return phone.validate_mobile(value, _("Mobile Number"), region=region)


def phonenumbers_region_for_code(calling_code):
	"""Map a numeric calling code (91) to a region libphonenumber accepts (IN)."""
	import phonenumbers

	try:
		region = phonenumbers.region_code_for_country_code(int(calling_code))
	except (TypeError, ValueError):
		return None
	# ZZ is libphonenumber's "unknown region" sentinel.
	return None if not region or region == "ZZ" else region


def _resolve_employee_link(value):
	value = (value or "").strip()
	if not value:
		return value

	if frappe.db.exists("Employee", value):
		return value

	by_name = frappe.db.get_value("Employee", {"employee_name": value}, "name")
	if by_name:
		return by_name

	by_user = frappe.db.get_value("Employee", {"user_id": value}, "name")
	if by_user:
		return by_user

	by_email = frappe.db.get_value("Employee", {"company_email": value}, "name")
	if by_email:
		return by_email

	return value


def _normalize_id_proof_type(id_proof_type):
	"""Canonical ID Proof Type name, used both for storage and for validation.

	This used to be a hardcoded {"PAN": "PAN Card"} map — the only alias it
	knew — while validators._canonical_type() already resolves every alias
	configured on the ID Proof Type master (not just record names, including
	customer-defined aliases), so a second, narrower copy here only fell behind
	whatever aliases an admin later adds.

	Confirmed before removing it: validate_id() and id_proof_error_message()
	(what the validation call site below feeds this into) both call
	_canonical_type() on their own input regardless of what they are handed, so
	this normalisation was already inert for that call site specifically. It is
	NOT inert for the other call site (~line 500 below), which stores this
	value directly on the Visitor Pass's id_proof_type Link field — an
	unrecognised alias reaching that unmapped would fail Link validation
	instead of resolving to the real ID Proof Type record. That is the
	behaviour this fix actually improves; the validation call site is
	unaffected either way.
	"""
	from visitormanagement.visitor_management.validators import _canonical_type

	value = (id_proof_type or "").strip()
	if not value:
		return value
	return _canonical_type(value) or value


def _parse_visitor_items(items):
	if not items:
		return []

	if isinstance(items, str):
		items = json.loads(items)

	parsed_items = []
	for row in items:
		if not isinstance(row, dict):
			continue

		item_name = (row.get("item_name") or "").strip()
		if not item_name:
			continue

		# The portal offers a single box and its own placeholder invites a list
		# — "e.g. Dell laptop, USB drive, toolkit" — so one submitted value
		# routinely describes several physical items. Kept whole, the gate
		# officer gets one checklist line covering all of them and cannot verify
		# or flag any of them separately. Split it exactly as the desk field is.
		from visitormanagement.visitor_management.doctype.visitor_pass.visitor_pass import (
			VisitorPass,
		)

		parts = VisitorPass._parse_items_carried(item_name) or [
			{"item_name": item_name, "quantity": 1}
		]

		for part in parts:
			# A quantity typed on the row only makes sense when that row turned
			# out to describe a single item; "(x2)" inside the text always wins.
			quantity = part["quantity"]
			if quantity == 1 and len(parts) == 1:
				quantity = row.get("quantity") or 1

			parsed_items.append(
				{
					"item_code": row.get("item_code"),
					"item_name": part["item_name"],
					"item_category": row.get("item_category"),
					"quantity": quantity,
					"unit_of_measure": row.get("unit_of_measure"),
					"description": row.get("description"),
					"is_new_item": row.get("is_new_item") or 0,
					"serial_number": row.get("serial_number") if len(parts) == 1 else None,
					"estimated_value": row.get("estimated_value") if len(parts) == 1 else None,
					"verification_remarks": row.get("verification_remarks"),
				}
			)

	return parsed_items


def _summarise_visitor_items(items):
	"""Return a human-readable text summary of the parsed visitor_items rows.
	Used to populate Visitor Pass.items_carried (a free-text field shown on the
	gate badge / printout) without forcing the visitor to type the same list
	twice on the portal form."""
	parsed = _parse_visitor_items(items)
	if not parsed:
		return None
	parts = []
	for row in parsed:
		piece = row.get("item_name", "").strip()
		if not piece:
			continue
		qty = row.get("quantity")
		if qty and int(qty or 0) > 1:
			piece = f"{piece} (x{int(qty)})"
		parts.append(piece)
	return ", ".join(parts) if parts else None


def _normalize_time(value):
	value = (value or "").strip()
	if not value:
		return value
	# "13:30" → "13:30:00"
	if len(value) == 5 and value[2] == ":":
		return value + ":00"
	return value


def _get_portal_submission_state(visitor_type, submission_action):
	# Both Save Draft and Submit produce Draft state. Staff reviews every
	# pre-registered Visitor Pass in the desk UI before advancing it into the
	# approval workflow. The `visitor_type` / `submission_action` args are kept
	# for signature compatibility; they no longer change the target state.
	return "Draft"


def _build_visitor_pass_values(
	data, person_to_visit, id_proof_url, visitor_photo_url, invitation=None, nationality=None, visa_url=None
):
	visitor_type = invitation.visitor_type if invitation else data.get("visitor_type")
	submission_action = (data.get("submission_action") or "submit").strip().lower()
	target_state = _get_portal_submission_state(visitor_type, submission_action)

	# Hospitality + room intent fields come from the host on the invitation. The
	# guest's portal form doesn't expose these, so without copying them here the
	# food/conference teams would never see what the host requested. When there
	# is no invitation (walk-in / desk entry), fall back to whatever the caller
	# put on the payload.
	def _from_invitation(field):
		if invitation is not None and invitation.get(field) is not None:
			return invitation.get(field)
		return data.get(field)

	return {
		"entry_type": "New",
		"visitor_full_name": (
			data.get("visitor_full_name")
			or data.get("visitor_name")
			or (invitation.get("visitor_full_name") if invitation else None)
		),
		"mobile_number": _normalize_mobile_number(
			data.get("mobile_number")
			or (invitation.get("visitor_mobile") if invitation else None),
			data.get("mobile_country_code"),
		),
		"email_id": invitation.visitor_email if invitation else data.get("email_id"),
		"company__organisation": data.get("company__organisation"),
		"visit_date": invitation.visit_date if invitation else data.get("visit_date"),
		"expected_checkin": _normalize_time(str(invitation.expected_checkin) if invitation else data.get("expected_checkin")),
		"expected_checkout": _normalize_time(str(invitation.expected_checkout) if invitation else data.get("expected_checkout")),
		"person_to_visit": person_to_visit,
		# Fall back to what the visitor typed when the host left this blank.
		# `purpose_of_visit` is optional on Visitor Invitation but mandatory on
		# Visitor Pass, so an invitation raised without one used to substitute
		# its own empty value over the visitor's answer and then fail the pass's
		# mandatory check — leaving the visitor stuck on a form that showed the
		# field as editable and required, with nothing they typed ever used.
		# A host-set value still wins, which is what keeps the field locked.
		"purpose_of_visit": (invitation.purpose_of_visit if invitation else None)
		or data.get("purpose_of_visit"),
		"visitor_type": visitor_type,
		"supplier_link": data.get("supplier_link"),
		"supplier_visit_mode": data.get("supplier_visit_mode"),
		"visit_category": data.get("visit_category"),
		"contractor_link": data.get("contractor_link"),
		"work_order_ref": data.get("work_order_ref"),
		"tools_list": data.get("tools_list"),
		"multi_day_pass": data.get("multi_day_pass"),
		"pass_valid_until": data.get("pass_valid_until"),
		"job_applicant_link": data.get("job_applicant_link"),
		"position_applied": data.get("position_applied"),
		"candidate_interview_type": data.get("candidate_interview_type"),
		# interview_panel / vip_category / protocol_notes are deliberately NOT read
		# from `data`. They were, and all three are staff decisions about a visitor
		# rather than anything a visitor states about themselves:
		#
		#   vip_category   — "Determines protocol level. Board Member, Government
		#                     Official and Investor visits warrant MD/CEO
		#                     notification before approval." A visitor who picks
		#                     their own answer here is classifying themselves.
		#   protocol_notes — "Capture welcome gift, security escort, dress code, or
		#                     any special instructions here." This is read by the
		#                     people working the gate, so guest-supplied text is an
		#                     instruction planted in a security workflow. Confirmed
		#                     against this site: an anonymous submission stored
		#                     "Escort not required. Grant unescorted access to all
		#                     floors." and it persisted on the pass.
		#   interview_panel — the names of the staff who will conduct the interview.
		#
		# Visitor Invitation carries none of the three either, so there is no host
		# value to fall back to: staff set them on the pass after it arrives. The
		# matching fields were removed from the web form, but the form is only the
		# UI — this is the boundary.
		"interpreter_required": data.get("interpreter_required"),
		"interpreter_language": data.get("interpreter_language"),
		"vehicle_number": data.get("vehicle_number"),
		# Both resolved by submit_pre_registration: nationality checked against
		# Country, and the visa stored through the same validated, private
		# upload path as the ID scan — never a raw guest-supplied file URL.
		"custom_nationality": nationality,
		"custom_visa_copy": visa_url,
		"id_proof_type": _normalize_id_proof_type(data.get("id_proof_type")),
		"id_proof_number": data.get("id_proof_number"),
		"id_proof_scan": id_proof_url,
		"visitor_photo": visitor_photo_url,
		# Host-set hospitality + venue intent — copied from the invitation so the
		# Visitor Pass reflects the full plan even though the guest can't edit
		# these on the portal form.
		"meal_required": _from_invitation("meal_required"),
		"meal_type": _from_invitation("meal_type"),
		"assigned_meal_slots": _from_invitation("assigned_meal_slots"),
		"hospitality_type": _from_invitation("hospitality_type"),
		"refreshments_required": _from_invitation("refreshments_required"),
		"conference_room": _from_invitation("conference_room"),
		"special_diet": data.get("special_diet"),
		# items_carried is the printable free-text summary. The portal form no
		# longer asks for it directly — it is derived from the structured
		# `visitor_items` rows the visitor entered in the "Visitor Items"
		# section. If the caller still passes items_carried (e.g. internal
		# desk submissions), respect it as a manual override.
		"items_carried": data.get("items_carried") or _summarise_visitor_items(data.get("visitor_items")),
		"status": target_state,
		"workflow_state": target_state,
		"request_channel": "Portal",
		"visitor_invitation": invitation.name if invitation else None,
	}


# Unauthenticated endpoint that inserts a Visitor Pass and stores up to two 5 MB
# files per call. Rate-limited per IP so the pre-registration link cannot be used
# to flood the site with records or fill the disk. Generous enough for a real
# visitor who retries a few times, and for a group registering from behind one
# office or hotel NAT.
#
# The `limit=20` below is a Python literal evaluated once, when this module is
# imported — decorator arguments run before there is a request, a site, or a
# database to read a Setting from, so it CANNOT be wired to
# vms_settings.max_portal_submissions_per_hour(). It is a fixed upper bound that
# no VMS Settings value can raise past; only the identity-keyed limiter inside
# the function (_enforce_submission_rate_limit, via _max_submissions_per_hour)
# is actually configurable, and only downward from this number.
@frappe.whitelist(allow_guest=True)
@rate_limit(limit=20, seconds=60 * 60)
def submit_pre_registration(payload=None):
	# Frappe's own @rate_limit keys on frappe.local.request_ip, which is read
	# from X-Forwarded-For without trusted-proxy validation — so the decorator
	# above is defeated by rotating that header. This second limit keys on the
	# identity the caller cannot rewrite (see portal_upload._rate_limit_identity).
	_enforce_submission_rate_limit()

	data = payload or frappe.form_dict
	if isinstance(data, str):
		data = json.loads(data)

	invitation = get_valid_invitation_by_token(data.get("invitation_token"))
	if data.get("invitation_token") and not invitation:
		frappe.throw(_("The invitation link is invalid, expired, or already used."))

	submission_action = (data.get("submission_action") or "submit").strip().lower()
	if submission_action not in {"save", "submit"}:
		submission_action = "submit"

	require_full_submission = submission_action == "submit" or not invitation

	invitation_field_values = {}
	if invitation:
		invitation_field_values = {
			"email_id": invitation.visitor_email,
			"visit_date": invitation.visit_date,
			"expected_checkin": invitation.expected_checkin,
			"expected_checkout": invitation.expected_checkout,
			"person_to_visit": invitation.host_employee,
			"purpose_of_visit": invitation.purpose_of_visit,
			"visitor_type": invitation.visitor_type,
		}

	required_fields = [
		"visitor_full_name",
		"mobile_number",
		"email_id",
		"visit_date",
		"expected_checkin",
		"expected_checkout",
		"person_to_visit",
		"purpose_of_visit",
		"visitor_type",
		"id_proof_type",
		"id_proof_number",
	]

	for fieldname in required_fields:
		field_value = invitation_field_values.get(fieldname) if invitation else None
		form_value = data.get(fieldname) or (data.get("visitor_name") if fieldname == "visitor_full_name" else None)
		if require_full_submission and not (field_value or form_value):
			frappe.throw(f"{frappe.unscrub(fieldname).title()} is required.")

	if require_full_submission and not data.get("id_proof_scan"):
		frappe.throw(_("ID Proof Scan is required."))

	if require_full_submission and not data.get("visitor_photo"):
		frappe.throw(_("Visitor Photo is required."))

	existing_doc = None
	if invitation and invitation.visitor_pass and frappe.db.exists("Visitor Pass", invitation.visitor_pass):
		existing_doc = frappe.get_doc("Visitor Pass", invitation.visitor_pass)
		# A guest may only re-edit their pass while it is still an unsubmitted
		# Draft (the Save-Draft → come-back-and-Submit flow). Once staff have
		# advanced it into any approval lane / Approved / Checked-In, the portal
		# must not overwrite it — otherwise an unauthenticated caller could reset
		# an in-progress or cleared pass to Draft and change the identity on it.
		if existing_doc.docstatus != 0 or (existing_doc.workflow_state or "Draft") != "Draft":
			frappe.throw(
				_(
					"This visitor pass is already being processed and can no longer be "
					"edited from the invitation link. Please contact your host."
				)
			)

	# Nationality drives the foreign-visitor rules on the pass (visa copy,
	# passport-type ID). The form defaults it to the home country; a draft the
	# visitor saved earlier keeps the nationality it was saved with.
	home_country = vms_settings.home_country()
	nationality = (
		data.get("custom_nationality")
		or (existing_doc.custom_nationality if existing_doc else None)
		or home_country
	)
	# The stored name, so "india" is the home country rather than a foreign one.
	nationality = isinstance(nationality, str) and frappe.db.get_value("Country", nationality.strip(), "name")
	if not nationality:
		frappe.throw(_("Please choose your nationality from the list."))
	is_foreign = nationality != home_country
	if require_full_submission and is_foreign and not data.get("custom_visa_copy"):
		frappe.throw(_("Visa Copy is required for visitors from another country."))

	if require_full_submission:
		canonical_type = _normalize_id_proof_type(data.get("id_proof_type"))
		id_number = (data.get("id_proof_number") or "").strip()
		if canonical_type and id_number and not validate_id(canonical_type, id_number):
			frappe.throw(
				id_proof_error_message(canonical_type),
				title=_("Invalid ID Proof"),
			)

	def read_upload(fieldname, fallback_filename):
		# Resubmitting a saved draft sends back the files already on it. Those are
		# this visitor's own (the draft is bound to their invitation token), so
		# they are kept as they are — adopting them again was refused as "already
		# attached to another record", which made a saved draft impossible to submit.
		payload = data.get(fieldname)
		if existing_doc and payload and payload == existing_doc.get(fieldname):
			return payload, None
		return _read_upload(
			payload, data.get(f"{fieldname}_filename") or fallback_filename, data.get("upload_key")
		)

	id_proof_upload = read_upload("id_proof_scan", "visitor-id-proof.png")
	visitor_photo_upload = read_upload("visitor_photo", "visitor-photo.png")
	# A home-country visitor has no use for a visa copy; one sent anyway is not stored.
	visa_upload = read_upload("custom_visa_copy", "visitor-visa.pdf") if is_foreign else (None, None)
	person_to_visit = _resolve_employee_link(invitation.host_employee if invitation else data.get("person_to_visit"))
	visitor_items = _parse_visitor_items(data.get("visitor_items"))

	if person_to_visit and not frappe.db.exists("Employee", person_to_visit):
		frappe.throw(_("Person to Visit must be a valid Employee (Employee ID or exact Employee Name)."))

	# Store any newly-uploaded files (validated + private) up front so their URLs
	# are available for the mandatory id_proof_scan / visitor_photo fields at
	# insert time. They are linked to the pass right after it is saved.
	id_proof_url, id_proof_file = id_proof_upload
	id_proof_url = id_proof_url or (existing_doc.id_proof_scan if existing_doc else None)
	visitor_photo_url, visitor_photo_file = visitor_photo_upload
	visitor_photo_url = visitor_photo_url or (existing_doc.visitor_photo if existing_doc else None)
	visa_url, visa_file = visa_upload
	if is_foreign:
		visa_url = visa_url or (existing_doc.custom_visa_copy if existing_doc else None)
		if require_full_submission and not visa_url:
			frappe.throw(_("Visa Copy is required for visitors from another country."))

	doc_values = _build_visitor_pass_values(
		data,
		person_to_visit,
		id_proof_url,
		visitor_photo_url,
		invitation=invitation,
		nationality=nationality,
		visa_url=visa_url,
	)

	visitor_pass = existing_doc or frappe.new_doc("Visitor Pass")
	visitor_pass.update(doc_values)
	visitor_pass.set("visitor_items", [])
	for item in visitor_items:
		visitor_pass.append("visitor_items", item)

	# Public/guest submissions always land as "Draft". The state is set in
	# `_build_visitor_pass_values` and persisted through the normal insert/save
	# path below, so it passes through validate(), permissions and the workflow
	# engine. We deliberately do NOT db_set() status/workflow_state here: a raw
	# write would bypass those checks, and a public API must never manipulate
	# workflow state directly. Staff advance the pass from the desk UI.
	# Claim the invitation *before* creating anything from it. Consumption used to
	# be an unconditional write after the insert, so two redemptions of one token
	# arriving together both passed the "is it still open?" read and both created
	# a pass — a single-use link that is not single-use. Making the claim a
	# conditional UPDATE means the database decides the winner: exactly one caller
	# sees a row change, and the loser is turned away before any record exists.
	if invitation and submission_action == "submit":
		_claim_invitation(invitation)

	if visitor_pass.is_new():
		visitor_pass.insert(ignore_permissions=True, ignore_mandatory=not require_full_submission)
	else:
		visitor_pass.flags.ignore_mandatory = not require_full_submission
		visitor_pass.save(ignore_permissions=True)

	# Link the private ID/photo files to the now-saved pass so staff who can read
	# the pass can view them, while they stay out of the public files directory.
	_attach_file_to_pass(id_proof_file, visitor_pass.name, "id_proof_scan")
	_attach_file_to_pass(visitor_photo_file, visitor_pass.name, "visitor_photo")
	_attach_file_to_pass(visa_file, visitor_pass.name, "custom_visa_copy")

	if invitation:
		invitation_updates = {"visitor_pass": visitor_pass.name}
		if submission_action == "save":
			# A "save" is a draft, not a redemption, so it does not consume the
			# token — only a real submission does, and _claim_invitation already
			# recorded that above.
			invitation_updates["invitation_status"] = "Saved"
		if not invitation.link_opened_on:
			invitation_updates["link_opened_on"] = now_datetime()
		if submission_action == "save":
			invitation_updates["form_saved_on"] = now_datetime()
		invitation.db_set(invitation_updates, update_modified=False)

	saved_values = frappe.db.get_value(
		"Visitor Pass",
		visitor_pass.name,
		["name", "status", "workflow_state"],
		as_dict=True,
	)
	return {
		"name": saved_values.name,
		"status": saved_values.status,
		"workflow_state": saved_values.workflow_state,
		"action": submission_action,
	}
