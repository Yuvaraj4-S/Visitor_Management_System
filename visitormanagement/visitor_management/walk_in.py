"""Register a visitor who arrives at the gate without a pass, in one short form.

The guard opens "New Walk-In" (public/js/walk_in.js), asks the person in front of
them for what the form shows, takes their photo and scans their ID. This creates
the Visitor Pass as the guard, with the guard's own permissions, for today and at
the server's time, and sends it for approval through the Visitor Type's normal
lane. Nothing about who approves changes: the guard cannot approve it, and the
host decides only if the lane lets them (workflow_builder).

Switched on and off by VMS Settings > Optional Features > "Let Security register
walk-in visitors" (settings.feature_on("enable_walk_in_registration")).
"""

import datetime

import frappe
from frappe import _
from frappe.model.workflow import apply_workflow
from frappe.utils import cint, cstr, get_time, now_datetime, strip_html, today

from visitormanagement.visitor_management import settings as vms_settings
from visitormanagement.visitor_management.workflow_builder import ACTION_SUBMIT, GATE_ROLE, WALK_IN_CHANNEL

# The page the gate (and the Gate Desk workspace) opens the form from:
# /desk/visitor-pass?walk_in=1 opens the Visitor Pass list with the form on top.
WALK_IN_ROUTE = "/desk/visitor-pass?walk_in=1"

# What the short form may set on the pass, and nothing else. The ID number is
# typed into id_proof_masked, as on the full form (id_numbers.take_typed_id).
WALK_IN_FIELDS = (
	"visitor_type",
	"visitor_full_name",
	"mobile_number",
	"company__organisation",
	"id_proof_type",
	"id_proof_masked",
	"person_to_visit",
	"purpose_of_visit",
)
REQUIRED_FIELDS = (
	"visitor_type",
	"visitor_full_name",
	"mobile_number",
	"id_proof_type",
	"id_proof_masked",
	"person_to_visit",
	"purpose_of_visit",
)
# The identity files the guard takes at the gate.
UPLOAD_FIELDS = ("visitor_photo", "id_proof_scan")

# How long a walk-in usually stays, when VMS Settings' default check-out time
# has already passed or is too close.
DEFAULT_STAY = datetime.timedelta(hours=2)
LAST_MINUTE = datetime.time(23, 59)


def _switched_on():
	return vms_settings.feature_on("enable_walk_in_registration")


def _is_gate_user():
	"""The walk-in form is the gate's: Security registers the visitor standing there.

	Anyone else who may raise a pass (a host, reception) uses the full Visitor Pass
	form, so a pass marked Walk-In always came from the gate.
	"""
	return GATE_ROLE in frappe.get_roles()


@frappe.whitelist()
def walk_in_enabled() -> bool:
	"""Show the New Walk-In button? Only when switched on, to Security, and the user may raise a pass."""
	return bool(_switched_on() and _is_gate_user() and frappe.has_permission("Visitor Pass", "create"))


@frappe.whitelist(methods=["POST"])
def create_walk_in_pass(values: str | dict) -> dict:
	"""Create a walk-in Visitor Pass for today and send it for approval.

	Returns the pass name and whether it went for approval. If the approval step
	refuses it (the visitor is on the blacklist, say) the pass is kept as a Draft
	and the reason is returned, so the guard can see what to do next.
	"""
	if not _switched_on():
		frappe.throw(
			_("Walk-in registration is switched off in VMS Settings. Ask the host to raise a Visitor Pass."),
			title=_("Walk-Ins Switched Off"),
		)
	if not _is_gate_user():
		frappe.throw(
			_("New Walk-In is for Security at the gate. Raise a Visitor Pass from the full form instead."),
			frappe.PermissionError,
			title=_("Not Permitted"),
		)
	frappe.has_permission("Visitor Pass", "create", throw=True)
	values = frappe.parse_json(values) or {}
	if not isinstance(values, dict):
		frappe.throw(_("The walk-in form sent nothing to save."))

	missing = [fieldname for fieldname in REQUIRED_FIELDS if not cstr(values.get(fieldname)).strip()]
	if missing:
		meta = frappe.get_meta("Visitor Pass")
		frappe.throw(
			_("Fill in {0}.").format(", ".join(_(meta.get_label(f)) for f in missing)),
			title=_("Missing Details"),
		)
	files = {fieldname: _own_upload(values.get(fieldname), fieldname) for fieldname in UPLOAD_FIELDS}

	doc = frappe.new_doc("Visitor Pass")
	for fieldname in WALK_IN_FIELDS:
		value = values.get(fieldname)
		if value not in (None, ""):
			doc.set(fieldname, strip_html(cstr(value)).strip())
	for fieldname, file_doc in files.items():
		doc.set(fieldname, file_doc.file_url)
	# The server says when and how: today, now, through the gate.
	doc.flags.vms_walk_in = True
	doc.request_channel = WALK_IN_CHANNEL
	doc.entry_type = "New"
	doc.visit_date = today()
	doc.expected_checkin, doc.expected_checkout = _visit_times()
	doc.insert()
	_attach_uploads(doc, files)

	frappe.db.savepoint("vms_walk_in_submit")
	try:
		apply_workflow(doc, ACTION_SUBMIT)
	except Exception as exc:
		frappe.db.rollback(save_point="vms_walk_in_submit")
		frappe.clear_messages()
		if not isinstance(exc, frappe.ValidationError):
			frappe.log_error(title=f"VMS Walk-In: {doc.name} not sent for approval")
		return {
			"name": doc.name,
			"submitted": False,
			"message": strip_html(cstr(exc)) or _("It could not be sent for approval."),
		}
	return {
		"name": doc.name,
		"submitted": True,
		"workflow_state": frappe.db.get_value("Visitor Pass", doc.name, "workflow_state"),
	}


def _own_upload(file_url, fieldname):
	"""The File the guard has just uploaded for `fieldname`, made private.

	Only an upload of the guard's own that no record holds yet is taken. The form
	sends a URL, and taking any URL would let a request put a file from another
	record (another visitor's ID scan) on this pass. An identity file stays private
	(uploads.IDENTITY_FILE_FIELDS), so a public upload is made private first.
	"""
	label = _(frappe.get_meta("Visitor Pass").get_label(fieldname))
	file_url = cstr(file_url).strip()
	if not file_url:
		frappe.throw(
			_("Take the {0} before sending the walk-in for approval.").format(label),
			title=_("Missing Documents"),
		)
	name = frappe.db.get_value(
		"File",
		{
			"file_url": file_url,
			"owner": frappe.session.user,
			"is_folder": 0,
			"attached_to_doctype": ("is", "not set"),
		},
		"name",
		order_by="creation desc",
	)
	if not name:
		frappe.throw(
			_("Upload the {0} again from this form.").format(label),
			title=_("Upload Again"),
		)
	file_doc = frappe.get_doc("File", name)
	if not cint(file_doc.is_private):
		file_doc.is_private = 1
		file_doc.save(ignore_permissions=True)
	return file_doc


def _attach_uploads(doc, files):
	"""File the guard's uploads under the new pass, unless Frappe's attach hook already did."""
	for fieldname, file_doc in files.items():
		if frappe.db.exists(
			"File",
			{"file_url": file_doc.file_url, "attached_to_doctype": doc.doctype, "attached_to_name": doc.name},
		):
			continue
		frappe.db.set_value(
			"File",
			file_doc.name,
			{
				"attached_to_doctype": doc.doctype,
				"attached_to_name": doc.name,
				"attached_to_field": fieldname,
			},
			update_modified=False,
		)


def _visit_times():
	"""(check-in, expected check-out) for a walk-in arriving now, by the server's clock.

	Check-out is the site's default check-out time (VMS Settings) when that is
	still at least half an hour away, else two hours from now; never past
	midnight, since the pass is for today, nor beyond the longest visit allowed.
	"""
	now = now_datetime().replace(microsecond=0)
	end_of_day = datetime.datetime.combine(now.date(), LAST_MINUTE)
	until = now + DEFAULT_STAY
	default_checkout = vms_settings.default_checkout_time()
	if default_checkout:
		usual = datetime.datetime.combine(now.date(), get_time(default_checkout))
		if usual - now >= datetime.timedelta(minutes=30):
			until = usual
	longest = cint(frappe.db.get_single_value("VMS Settings", "max_visit_duration_hrs"))
	if longest > 0:
		until = min(until, now + datetime.timedelta(hours=longest))
	until = min(until, end_of_day)
	if until <= now:
		# Arriving in the day's last minute.
		until = now + datetime.timedelta(minutes=1)
	return now.time().strftime("%H:%M:%S"), until.time().strftime("%H:%M:%S")
