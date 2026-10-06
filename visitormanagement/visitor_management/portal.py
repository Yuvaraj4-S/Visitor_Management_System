import base64
import binascii
import contextlib
import json
import re

import frappe
from frappe import _
from frappe.rate_limiter import rate_limit
from frappe.utils import cint, cstr, escape_html, now_datetime, strip_html

from visitormanagement.visitor_management import settings as vms_settings
from visitormanagement.visitor_management.doctype.visitor_invitation.visitor_invitation import (
	lookup_invitation,
)

# Guests may only upload genuine images / PDFs for ID proof and photo. We trust
# neither the file extension nor the client-sent MIME type — the decoded content
# must be a real JPG, PNG or PDF. The policy is defined once in portal_upload.
from visitormanagement.visitor_management.portal_upload import (
	_count_or_throw,
	count_event,
	rate_limit_identity,
	verify_upload,
)
from visitormanagement.visitor_management.validators import (
	id_proof_error_message,
	validate_id,
)

# Ceiling for the identity-keyed limiter below (see
# portal_upload.rate_limit_identity for what "identity" is). It reads
# vms_settings.max_portal_submissions_per_hour() (default 20) so lowering the
# setting takes effect immediately. Raising it above the decorator's fixed 20
# has no effect — see the comment on submit_pre_registration for why that half
# cannot be made dynamic.
DEFAULT_MAX_SUBMISSIONS_PER_HOUR = 20

# "New pre-registration" mails one host may receive per hour from submissions
# that did not come through an invitation. Anyone can type a host's name into
# the public form; the pass is still created (reception sees it in the list),
# but the host's inbox is not a target. See _throttle_host_alerts.
WALK_IN_HOST_ALERTS_PER_HOUR = 5

# The visitor's attachments: payload key -> name used when the browser sent none.
UPLOAD_FIELDS = {
	"id_proof_scan": "visitor-id-proof.png",
	"visitor_photo": "visitor-photo.png",
	"custom_visa_copy": "visitor-visa.pdf",
}

# Records and decisions that belong to staff. An anonymous caller may not set
# them: the Link fields answered "Could not find Supplier X" (an existence
# check on other DocTypes for anyone), and a meal or a meeting room is the
# host's to ask for — with an invitation they come from the invitation.
STAFF_ONLY_FIELDS = ("supplier_link", "contractor_link", "job_applicant_link", "work_order_ref")
HOST_DECISION_FIELDS = (
	"meal_required",
	"meal_type",
	"assigned_meal_slots",
	"hospitality_type",
	"refreshments_required",
	"conference_room",
)

# Titles Visitor Pass gives the two refusals that say something about OTHER
# records (contract_a_c.md). See _neutral_refusal.
_NEUTRAL_FOR_GUEST_TITLES = ("Access Denied — Blacklisted Visitor", "Duplicate Pass")

DEFAULT_PRIVACY_NOTICE_VERSION = "1"

_SAVEPOINT = "vms_portal_submit"


def default_privacy_notice():
	return _(
		"We collect your name, contact details, a copy of your ID and your photo to register your visit "
		"and for the safety and security of the site. They are seen only by the people who handle your "
		"visit, used for no other purpose, and kept only as long as our records policy requires. "
		"Ask your host if you want to see or correct your details."
	)


# -- settings ----------------------------------------------------------------


def _setting(fieldname, missing=None):
	"""A VMS Settings value, or `missing` when the field does not exist yet.

	Read from the stored rows rather than the cached document: a Single has no
	row for a field nobody has saved since it was added, and the document then
	reports a Check as 0 whatever its default is (BaseDocument._fix_numeric_types).
	Such a field takes its DocField default here, as it shows on the form.
	"""
	try:
		df = frappe.get_meta("VMS Settings").get_field(fieldname)
		if not df:
			return missing
		value = frappe.db.get_singles_dict("VMS Settings").get(fieldname)
	except Exception:
		# Very early in an install / migrate.
		return missing
	return df.default if value is None else value


def consent_required():
	"""Must the visitor acknowledge the privacy notice? On unless switched off."""
	value = _setting("require_portal_consent", missing=1)
	return bool(cint(1 if value is None else value))


def privacy_notice_version():
	return cstr(_setting("privacy_notice_version") or "").strip() or DEFAULT_PRIVACY_NOTICE_VERSION


_NOTICE_URL = re.compile(r"https?://[^\s<>\"']+[^\s<>\"'.,;:!?)]")


def privacy_notice_text():
	"""The site's notice (VMS Settings), else the standard one. Plain text."""
	getter = getattr(vms_settings, "privacy_notice_text", None)
	text = cstr(getter() if callable(getter) else _setting("privacy_notice_text")).strip()
	return text or default_privacy_notice()


def privacy_notice_html():
	"""The notice as HTML that is safe on a public page.

	The setting is plain text: it is escaped whole, keeps its line breaks, and a
	web address in it (the full privacy policy, usually) becomes a link.
	"""
	text = privacy_notice_text()
	parts = []
	position = 0
	for match in _NOTICE_URL.finditer(text):
		url = escape_html(match.group(0))
		parts.append(escape_html(text[position : match.start()]))
		parts.append(f'<a href="{url}" target="_blank" rel="noopener noreferrer">{url}</a>')
		position = match.end()
	parts.append(escape_html(text[position:]))
	return "".join(parts).replace("\n", "<br>")


def walk_in_allowed():
	"""May the form be used without an invitation?

	VMS Settings "Allow Pre-Registration Without an Invitation". A site whose
	settings do not have the switch behaves as before it existed.
	"""
	return bool(cint(_setting("allow_walk_in_pre_registration", missing=1)))


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
	if not frappe.request:
		# A script, a job or a test calling the function: not a web caller.
		return
	_count_or_throw(
		f"vms:portal-submit:{rate_limit_identity()}",
		_max_submissions_per_hour(),
		_("Too many pre-registrations from this connection. Please wait a while and try again."),
	)


# -- small helpers -------------------------------------------------------------


def _is_guest():
	return frappe.session.user == "Guest"


def _clean_text(value):
	"""Visitor-typed text with any markup removed.

	Stripped rather than sanitised: `sanitize_html` keeps `<a>` and `<img>`, and
	nothing a visitor types about themselves has a use for either. Visitor Pass
	does the same on save; doing it here as well covers the rows and comments
	this module writes itself.
	"""
	if not isinstance(value, str):
		return value
	value = value.strip()
	if "<" in value:
		value = strip_html(value).strip()
	return value


def _load_payload(payload):
	data = payload or frappe.form_dict
	if isinstance(data, str):
		try:
			data = json.loads(data)
		except ValueError:
			data = None
	if not isinstance(data, dict):
		frappe.throw(_("The form could not be read. Please reload the page and try again."))
	return data


def _looks_like_file_url(payload):
	"""True when the form sent a reference to a stored File instead of its bytes.

	The portal sends every new attachment as a data URI (Frappe 15: guest uploads
	stay off, see portal_upload.py). A file URL is only legitimate when it is the
	value already on the visitor's own saved draft, which `submit_pre_registration`
	keeps before ever calling `_prepare_upload`; any other URL is refused there, so
	a guest can never name somebody else's stored file.
	"""
	value = (payload or "").strip()
	if value.startswith(("/files/", "/private/files/")):
		return True
	return value.startswith("http") and "/files/" in value[:200]


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


# -- attachments -------------------------------------------------------------


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


def _prepare_upload(payload, fallback_filename):
	"""Decode and check one attachment. Writes nothing.

	Returns (filename, content), or None when the visitor sent no file. Every
	attachment of a submission goes through here before the first one is stored,
	so a bad second file cannot leave the first one behind.
	"""
	if not payload:
		return None
	if not isinstance(payload, str):
		frappe.throw(_("Uploaded file is corrupted or in an unsupported format. Please re-upload."))

	if _looks_like_file_url(payload):
		frappe.throw(
			_("Please attach the file again from your device."),
			frappe.PermissionError,
		)

	filename, content = _extract_file_payload(payload, fallback_filename)
	if not content:
		# A payload that decodes to nothing ("data:...;base64,") must not count as
		# the required document.
		frappe.throw(_("The uploaded file is empty. Please upload it again."))

	# Path separators have no business in a name; Frappe refuses them with a
	# message about file paths that means nothing to a visitor.
	filename = cstr(filename or fallback_filename).replace("\\", "/").rsplit("/", 1)[-1].strip()
	return verify_upload(filename or fallback_filename, content), content


def _store_file(filename, content):
	"""Store a checked attachment as a PRIVATE File and return the File document.

	is_private=1 keeps sensitive ID documents out of the public /files directory.
	The caller links the file to its Visitor Pass once the pass exists (see
	_attach_file_to_pass) so that staff with read access to the pass can still
	view it — a private *standalone* file would otherwise be visible only to its
	owner and System Managers.
	"""
	file_doc = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": filename,
			"is_private": 1,
			"content": content,
		}
	)
	# Checked by _prepare_upload; lets portal_upload.guard_guest_upload know this
	# guest File is the portal's own (a client cannot set document flags).
	file_doc.flags.vms_portal_submission = True
	file_doc.insert(ignore_permissions=True)
	return file_doc


def _read_upload(payload, fallback_filename):
	"""Check and store one attachment the portal sent as a data URI.

	Returns (file_url, file_name). The bytes travel inside the submission (as on
	1.0.0), so there is no earlier guest upload to look up or claim.
	"""
	prepared = _prepare_upload(payload, fallback_filename)
	if not prepared:
		return None, None
	file_doc = _store_file(*prepared)
	return file_doc.file_url, file_doc.name


def _discard_stored_files(file_docs):
	"""Remove from disk the files of a submission that did not go through.

	Their File rows are already gone (the savepoint was rolled back). Frappe
	deletes the bytes itself when a whole request is rolled back
	(File.on_rollback, registered in frappe.db.after_rollback) — but not on a
	savepoint rollback, and not when the caller catches the error and carries
	on. `on_rollback` is the File's own clean-up: it leaves bytes another File
	row still points to, and clears the flag so the later callback is a no-op.
	"""
	for file_doc in file_docs:
		with contextlib.suppress(Exception):
			file_doc.on_rollback()


def _attach_file_to_pass(file_doc, pass_name, fieldname):
	"""Link a stored private File to the Visitor Pass it belongs to, so the
	framework's file-permission check grants access to anyone who can read the
	pass (frappe/core/doctype/file/file.py:has_permission)."""
	if not file_doc:
		return
	frappe.db.set_value(
		"File",
		file_doc.name,
		{
			"attached_to_doctype": "Visitor Pass",
			"attached_to_name": pass_name,
			"attached_to_field": fieldname,
		},
		update_modified=False,
	)


# -- field values --------------------------------------------------------------


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


def _resolve_host(text):
	"""The one ACTIVE Employee a walk-in visitor means, or None.

	The visitor types whom they have come to see: a name or a work email. It
	resolves only when exactly one active employee matches. Anything else — no
	match, two people with that name, someone who has left — is left for
	reception to decide, and the caller answers the visitor the same way in
	every case: the endpoint is public, and "must be a valid Employee" told
	anyone who asked whether a name works here.

	All four lookups always run, so the time taken does not depend on which one
	matched.
	"""
	value = _clean_text(text) if isinstance(text, str) else None
	if not value:
		return None

	found = None
	for fieldname in ("name", "employee_name", "user_id", "company_email"):
		matches = frappe.get_all(
			"Employee",
			filters={fieldname: value, "status": "Active"},
			pluck="name",
			limit=2,
		)
		if found is None and matches:
			found = matches[0] if len(matches) == 1 else False
	return found or None


def _normalize_id_proof_type(id_proof_type):
	"""Canonical ID Proof Type name, used both for storage and for validation.

	validators._canonical_type() resolves every alias configured on the ID Proof
	Type master (not just record names, including customer-defined aliases). The
	value is stored on the Visitor Pass's id_proof_type Link field, where an
	unrecognised alias would fail Link validation instead of resolving to the
	real ID Proof Type record.
	"""
	from visitormanagement.visitor_management.validators import _canonical_type

	value = (id_proof_type or "").strip()
	if not value:
		return value
	return _canonical_type(value) or value


# More rows than any visitor carries; the form itself sends one.
MAX_VISITOR_ITEM_ROWS = 50


def _parse_visitor_items(items):
	if not items:
		return []

	if isinstance(items, str):
		try:
			items = json.loads(items)
		except ValueError:
			return []
	if not isinstance(items, list):
		return []

	parsed_items = []
	for row in items[:MAX_VISITOR_ITEM_ROWS]:
		if not isinstance(row, dict):
			continue

		item_name = _clean_text(cstr(row.get("item_name")))
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

		parts = VisitorPass._parse_items_carried(item_name) or [{"item_name": item_name, "quantity": 1}]

		for part in parts:
			# A quantity typed on the row only makes sense when that row turned
			# out to describe a single item; "(x2)" inside the text always wins.
			quantity = part["quantity"]
			if quantity == 1 and len(parts) == 1:
				quantity = cint(row.get("quantity")) or 1

			single = len(parts) == 1
			parsed_items.append(
				{
					"item_code": _clean_text(row.get("item_code")),
					"item_name": part["item_name"],
					"item_category": _clean_text(row.get("item_category")),
					"quantity": quantity,
					"unit_of_measure": _clean_text(row.get("unit_of_measure")),
					"description": _clean_text(row.get("description")),
					"is_new_item": cint(row.get("is_new_item")),
					"serial_number": _clean_text(row.get("serial_number")) if single else None,
					"estimated_value": row.get("estimated_value") if single else None,
					# verification_remarks is the gate officer's column: a visitor
					# does not get to write the result of their own item check.
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
		piece = (row.get("item_name") or "").strip()
		if not piece:
			continue
		qty = cint(row.get("quantity"))
		if qty > 1:
			piece = f"{piece} (x{qty})"
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


def _typed_id_number(data):
	"""What the visitor typed in the ID number box (either payload key)."""
	return cstr(data.get("id_proof_number_entry") or data.get("id_proof_number") or "").strip()


def _build_visitor_pass_values(
	data, person_to_visit, id_proof_url, visitor_photo_url, invitation=None, nationality=None, visa_url=None
):
	"""The fields a portal submission sets on the Visitor Pass.

	The ID number is not among them: it goes through the pass's write-only entry
	field (see _apply_id_number), never into `update()`.
	"""
	visitor_type = invitation.visitor_type if invitation else data.get("visitor_type")
	submission_action = (data.get("submission_action") or "submit").strip().lower()
	target_state = _get_portal_submission_state(visitor_type, submission_action)
	guest = _is_guest()

	def text(fieldname):
		return _clean_text(data.get(fieldname))

	values = {
		"entry_type": "New",
		"visitor_full_name": (
			text("visitor_full_name")
			or text("visitor_name")
			or (invitation.get("visitor_full_name") if invitation else None)
		),
		"mobile_number": _normalize_mobile_number(
			data.get("mobile_number") or (invitation.get("visitor_mobile") if invitation else None),
			data.get("mobile_country_code"),
		),
		"email_id": invitation.visitor_email if invitation else text("email_id"),
		"company__organisation": text("company__organisation"),
		"visit_date": invitation.visit_date if invitation else data.get("visit_date"),
		"expected_checkin": _normalize_time(
			str(invitation.expected_checkin) if invitation else cstr(data.get("expected_checkin"))
		),
		"expected_checkout": _normalize_time(
			str(invitation.expected_checkout) if invitation else cstr(data.get("expected_checkout"))
		),
		"person_to_visit": person_to_visit,
		# Fall back to what the visitor typed when the host left this blank.
		# `purpose_of_visit` is optional on Visitor Invitation but mandatory on
		# Visitor Pass, so an invitation raised without one used to substitute
		# its own empty value over the visitor's answer and then fail the pass's
		# mandatory check — leaving the visitor stuck on a form that showed the
		# field as editable and required, with nothing they typed ever used.
		# A host-set value still wins, which is what keeps the field locked.
		"purpose_of_visit": (invitation.purpose_of_visit if invitation else None) or text("purpose_of_visit"),
		"visitor_type": visitor_type,
		"supplier_visit_mode": data.get("supplier_visit_mode"),
		"meeting_subject": text("meeting_subject"),
		"visit_category": data.get("visit_category"),
		"tools_list": text("tools_list"),
		"multi_day_pass": cint(data.get("multi_day_pass")),
		"pass_valid_until": data.get("pass_valid_until"),
		"position_applied": text("position_applied"),
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
		"interpreter_required": cint(data.get("interpreter_required")),
		"interpreter_language": data.get("interpreter_language"),
		"vehicle_number": text("vehicle_number"),
		# Both resolved by submit_pre_registration: nationality checked against
		# Country, and the visa stored through the same validated, private
		# upload path as the ID scan — never a raw guest-supplied file URL.
		"custom_nationality": nationality,
		"custom_visa_copy": visa_url,
		"id_proof_type": _normalize_id_proof_type(data.get("id_proof_type")),
		"id_proof_scan": id_proof_url,
		"visitor_photo": visitor_photo_url,
		"special_diet": data.get("special_diet"),
		# items_carried is the printable free-text summary. The portal form no
		# longer asks for it directly — it is derived from the structured
		# `visitor_items` rows the visitor entered in the "Visitor Items"
		# section. If the caller still passes items_carried (e.g. internal
		# desk submissions), respect it as a manual override.
		"items_carried": text("items_carried") or _summarise_visitor_items(data.get("visitor_items")),
		"status": target_state,
		"workflow_state": target_state,
		"request_channel": "Portal",
		"visitor_invitation": invitation.name if invitation else None,
	}

	# Host-set hospitality + venue intent — copied from the invitation so the
	# Visitor Pass reflects the full plan even though the visitor cannot edit
	# these on the portal form. Without an invitation only a signed-in caller
	# may state them (HOST_DECISION_FIELDS); for a guest the keys are left out
	# altogether, so a draft keeps whatever staff have put there since.
	for fieldname in HOST_DECISION_FIELDS:
		if invitation is not None and invitation.get(fieldname) is not None:
			values[fieldname] = invitation.get(fieldname)
		elif not guest:
			values[fieldname] = data.get(fieldname)

	if not guest:
		for fieldname in STAFF_ONLY_FIELDS:
			values[fieldname] = data.get(fieldname)

	return values


def _apply_id_number(visitor_pass, typed):
	"""Hand the typed ID number to the pass through its write-only entry field.

	Visitor Pass keeps the full number where no browser can read it, shows a
	masked copy, and takes new input only through `id_proof_number_entry`, which
	its validate() checks, normalises, stores and blanks (contract_a_c.md). An
	empty box means "keep the number already on my draft", and so does a box
	that merely echoes the masked value back.
	"""
	if not typed or typed == cstr(visitor_pass.get("id_proof_number_masked")):
		return
	if visitor_pass.meta.has_field("id_proof_number_entry"):
		visitor_pass.id_proof_number_entry = typed
	else:
		# A site whose Visitor Pass predates the entry field.
		visitor_pass.id_proof_number = typed


def _has_stored_id_number(doc):
	return bool(doc and (doc.get("id_proof_number_masked") or doc.get("id_proof_number")))


def _record_consent(visitor_pass):
	"""Stamp the visitor's acknowledgement on the pass, from the server's clock.

	Returns text for a timeline comment when the pass has nowhere to keep it (a
	site whose Visitor Pass predates the Privacy section), else None. Nothing
	here comes from the request: the time is now, the version is the site's.
	"""
	version = privacy_notice_version()
	record = getattr(visitor_pass, "record_portal_consent", None)
	if callable(record):
		record(version)
		return None

	if visitor_pass.meta.has_field("consent_given"):
		visitor_pass.consent_given = 1
		visitor_pass.consent_timestamp = now_datetime()
		visitor_pass.consent_notice_version = version
		visitor_pass.flags.vms_portal_consent = True
		return None

	return _("Privacy notice (version {0}) acknowledged by the visitor on the pre-registration form.").format(
		version
	)


def _add_info_comment(pass_name, text):
	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Info",
			"reference_doctype": "Visitor Pass",
			"reference_name": pass_name,
			"content": escape_html(text),
		}
	).insert(ignore_permissions=True)


def _throttle_host_alerts(visitor_pass, host):
	"""Keep one host's inbox from being flooded through the public form.

	A submission without an invitation names its own host, and every new portal
	pass mails that host. Past WALK_IN_HOST_ALERTS_PER_HOUR an hour the pass is
	still created — reception sees it with the other web submissions — but the
	"new" notifications for this document are marked as already run, which is
	how Document.run_notifications skips them (flags.notifications_executed).
	"""
	if not host:
		return
	if count_event(f"vms:portal-host-alert:{host}") <= WALK_IN_HOST_ALERTS_PER_HOUR:
		return
	visitor_pass.flags.notifications_executed = frappe.get_all(
		"Notification",
		filters={"document_type": "Visitor Pass", "event": "New", "enabled": 1},
		pluck="name",
	)


def _neutral_refusal():
	"""Answer an anonymous caller without describing anybody else's records.

	Two of Visitor Pass's refusals are about data the caller did not supply:
	"this ID is on the blacklist" and "this ID already has a pass that day". To
	staff at the desk that is exactly what they need to read. To an anonymous
	caller of a public endpoint it is a yes/no lookup on any ID number. The
	blacklist alert to Security has already gone out by the time this runs.
	"""
	titles = set(_NEUTRAL_FOR_GUEST_TITLES) | {_(title) for title in _NEUTRAL_FOR_GUEST_TITLES}
	for message in frappe.get_message_log():
		if isinstance(message, str):
			try:
				message = json.loads(message)
			except ValueError:
				continue
		if cstr((message or {}).get("title")) in titles:
			frappe.clear_messages()
			frappe.throw(
				_(
					"We could not accept this pre-registration online. If you have already registered "
					"for this visit you do not need to do it again; otherwise please contact your host "
					"or the reception."
				),
				title=_("Pre-registration not accepted"),
			)


def _require(condition, label):
	if not condition:
		frappe.throw(_("{0} is required.").format(label))


# Unauthenticated endpoint that inserts a Visitor Pass and stores up to three
# 5 MB files per call. Rate-limited per IP so the pre-registration link cannot be
# used to flood the site with records or fill the disk. Generous enough for a
# real visitor who retries a few times, and for a group registering from behind
# one office or hotel NAT.
#
# The `limit=20` below is a Python literal evaluated once, when this module is
# imported — decorator arguments run before there is a request, a site, or a
# database to read a Setting from, so it CANNOT be wired to
# vms_settings.max_portal_submissions_per_hour(). It is a fixed upper bound on
# CALLS that no VMS Settings value can raise past; the limiter inside the
# function (_enforce_submission_rate_limit) is the configurable one, and counts
# only submissions that got as far as being stored.
# nosemgrep: guest-whitelisted-method - portal submission; rate-limited, allow-listed fields, see threat model
@frappe.whitelist(allow_guest=True)
@rate_limit(limit=20, seconds=60 * 60)
def submit_pre_registration(payload: str | dict | None = None):
	data = _load_payload(payload)

	# -- 1. who is this for ---------------------------------------------------
	token = cstr(data.get("invitation_token")).strip()
	invitation = lookup_invitation(token)
	if token and not invitation:
		frappe.throw(_("The invitation link is invalid, expired, or already used."))
	if not invitation and not walk_in_allowed():
		frappe.throw(
			_("Pre-registration needs an invitation. Please use the link in your invitation email."),
			title=_("Invitation Required"),
		)

	submission_action = (data.get("submission_action") or "submit").strip().lower()
	if submission_action not in {"save", "submit"}:
		submission_action = "submit"

	require_full_submission = submission_action == "submit" or not invitation

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

	# -- 2. everything that can be wrong with the request, before any write ---
	# The privacy notice is acknowledged before anything about the visitor is
	# stored, a saved draft included.
	if consent_required() and not cint(data.get("consent_given")):
		frappe.throw(
			_("Please read the privacy notice and tick the box to confirm before you submit."),
			title=_("Confirmation Needed"),
		)

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
	]

	if require_full_submission:
		for fieldname in required_fields:
			field_value = invitation_field_values.get(fieldname) if invitation else None
			form_value = data.get(fieldname) or (
				data.get("visitor_name") if fieldname == "visitor_full_name" else None
			)
			_require(field_value or form_value, _(frappe.unscrub(fieldname).title()))

		# A number already on the visitor's own draft counts; so does one typed now.
		typed_id_number = _typed_id_number(data)
		_require(typed_id_number or _has_stored_id_number(existing_doc), _("ID Proof Number"))
		_require(
			data.get("id_proof_scan") or (existing_doc and existing_doc.id_proof_scan), _("ID Proof Scan")
		)
		_require(
			data.get("visitor_photo") or (existing_doc and existing_doc.visitor_photo), _("Visitor Photo")
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
	has_visa = data.get("custom_visa_copy") or (existing_doc and existing_doc.custom_visa_copy)
	if require_full_submission and is_foreign and not has_visa:
		frappe.throw(_("Visa Copy is required for visitors from another country."))

	typed_id_number = _typed_id_number(data)
	if typed_id_number:
		canonical_type = _normalize_id_proof_type(data.get("id_proof_type"))
		if canonical_type and not validate_id(canonical_type, typed_id_number):
			frappe.throw(
				id_proof_error_message(canonical_type),
				title=_("Invalid ID Proof"),
			)

	# Decode and check every attachment now; none is stored until all have passed.
	prepared = {}
	kept_urls = {}
	for fieldname, fallback_filename in UPLOAD_FIELDS.items():
		if fieldname == "custom_visa_copy" and not is_foreign:
			# A home-country visitor has no use for a visa copy; one sent anyway
			# is not stored.
			continue
		upload = data.get(fieldname)
		stored_url = existing_doc.get(fieldname) if existing_doc else None
		if stored_url and (not upload or upload == stored_url):
			# The file already on this visitor's own draft (the draft is bound to
			# their invitation token) stays as it is.
			kept_urls[fieldname] = stored_url
			continue
		prepared[fieldname] = _prepare_upload(
			upload, _clean_text(data.get(f"{fieldname}_filename")) or fallback_filename
		)

	# With an invitation the host is the one who sent it — never the request.
	# Without one it is whoever the typed text identifies, if anybody.
	if invitation:
		person_to_visit = invitation.host_employee
	else:
		person_to_visit = _resolve_host(data.get("person_to_visit"))
	requested_host = (
		None if (invitation or person_to_visit) else _clean_text(cstr(data.get("person_to_visit")))
	)

	visitor_items = _parse_visitor_items(data.get("visitor_items"))

	doc_values = _build_visitor_pass_values(
		data,
		person_to_visit,
		kept_urls.get("id_proof_scan"),
		kept_urls.get("visitor_photo"),
		invitation=invitation,
		nationality=nationality,
		visa_url=kept_urls.get("custom_visa_copy"),
	)

	# -- 3. count it ------------------------------------------------------------
	# Frappe's own @rate_limit above has already counted this call. This second,
	# configurable limit counts only requests that are about to store something,
	# so a stream of malformed requests cannot use up the allowance of the real
	# visitors who share an address with whoever sends them.
	_enforce_submission_rate_limit()

	# -- 4. store: files, pass and invitation together, or none of them -------
	stored_files = {}
	frappe.db.savepoint(_SAVEPOINT)
	try:
		for fieldname, upload in prepared.items():
			if upload:
				stored_files[fieldname] = _store_file(*upload)

		for fieldname, target in (
			("id_proof_scan", "id_proof_scan"),
			("visitor_photo", "visitor_photo"),
			("custom_visa_copy", "custom_visa_copy"),
		):
			if fieldname in stored_files:
				doc_values[target] = stored_files[fieldname].file_url

		visitor_pass = existing_doc or frappe.new_doc("Visitor Pass")
		visitor_pass.update(doc_values)
		_apply_id_number(visitor_pass, typed_id_number)
		visitor_pass.set("visitor_items", [])
		for item in visitor_items:
			visitor_pass.append("visitor_items", item)

		consent_comment = _record_consent(visitor_pass) if cint(data.get("consent_given")) else None

		# Public/guest submissions always land as "Draft". The state is set in
		# `_build_visitor_pass_values` and persisted through the normal insert/save
		# path below, so it passes through validate(), permissions and the workflow
		# engine. We deliberately do NOT db_set() status/workflow_state here: a raw
		# write would bypass those checks, and a public API must never manipulate
		# workflow state directly. Staff advance the pass from the desk UI.
		#
		# Claim the invitation *before* creating anything from it. Consumption used
		# to be an unconditional write after the insert, so two redemptions of one
		# token arriving together both passed the "is it still open?" read and both
		# created a pass — a single-use link that is not single-use. Making the
		# claim a conditional UPDATE means the database decides the winner: exactly
		# one caller sees a row change, and the loser is turned away before any
		# record exists.
		if invitation and submission_action == "submit":
			_claim_invitation(invitation)

		if not invitation:
			_throttle_host_alerts(visitor_pass, person_to_visit)

		# A walk-in whose host could not be identified is saved without one, for
		# reception to complete; the portal's own required fields were checked
		# above, so nothing else is being waved through.
		ignore_mandatory = not require_full_submission or bool(requested_host)
		try:
			if visitor_pass.is_new():
				visitor_pass.insert(ignore_permissions=True, ignore_mandatory=ignore_mandatory)
			else:
				visitor_pass.flags.ignore_mandatory = ignore_mandatory
				visitor_pass.save(ignore_permissions=True)
		except frappe.ValidationError:
			if _is_guest():
				_neutral_refusal()
			raise

		# Link the private ID/photo files to the now-saved pass so staff who can
		# read the pass can view them, while they stay out of the public files
		# directory.
		for fieldname, file_doc in stored_files.items():
			_attach_file_to_pass(file_doc, visitor_pass.name, fieldname)

		if requested_host:
			_add_info_comment(
				visitor_pass.name,
				_(
					"Visitor asked to meet: {0}. The form could not match this to one active employee — "
					"set Person to Visit before sending the pass for approval."
				).format(requested_host[:140]),
			)
		if consent_comment:
			_add_info_comment(visitor_pass.name, consent_comment)

		if invitation:
			invitation_updates = {"visitor_pass": visitor_pass.name}
			if submission_action == "save":
				# A "save" is a draft, not a redemption, so it does not consume the
				# token — only a real submission does, and _claim_invitation already
				# recorded that above.
				invitation_updates["invitation_status"] = "Saved"
				invitation_updates["form_saved_on"] = now_datetime()
			if not invitation.link_opened_on:
				invitation_updates["link_opened_on"] = now_datetime()
			invitation.db_set(invitation_updates, update_modified=False)
	except Exception:
		# Not one row of a refused submission stays: the File rows, the pass and
		# the invitation claim go with the savepoint, the bytes are removed here.
		with contextlib.suppress(Exception):
			frappe.db.rollback(save_point=_SAVEPOINT)
		_discard_stored_files(stored_files.values())
		raise

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
