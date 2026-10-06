"""Uploads made on a form before the form was saved.

Desk uploads on an unsaved form are filed against its temporary "new-..." name.
Frappe moves them onto the record when it saves, but only if the upload is less
than 60 minutes old (frappe/core/doctype/file/utils.py:relink_files). A
receptionist who photographs the visitor, then finishes the pass an hour later,
leaves the photo's File row on a name that never existed. The pass still shows
the photo (its field holds the URL), but:
  - Frappe's private-file check reads one File row per URL
    (File.validate_private_file_access, limit=1). When that row was the stray one,
    the guard saving the gate log was told "You do not have permission to access
    this file";
  - nothing ever owned the row, so it could not be told apart from a truly
    abandoned upload.
"""

import re

import frappe
from frappe import _
from frappe.permissions import push_perm_check_log
from frappe.utils.caching import request_cache
from werkzeug.exceptions import Forbidden

from visitormanagement.visitor_management.workflow_builder import DRAFT, REJECTED, STATE_FIELD

ATTACH_TYPES = ("Attach", "Attach Image")


def _attach_fieldnames(doctype):
	return [df.fieldname for df in frappe.get_meta(doctype).fields if df.fieldtype in ATTACH_TYPES]


# The records whose documents are kept once they are final: a Visitor Pass from
# the moment it leaves Draft, a Security Log from the moment it is recorded (its
# before_save refuses every later edit but a System Manager's correction). And
# the permission types that change or remove a File row.
VISITOR_PASS = "Visitor Pass"
SECURITY_LOG = "Security Log"
KEPT_DOCTYPES = (VISITOR_PASS, SECURITY_LOG)
_FILE_CHANGING_PTYPES = ("write", "delete")

# Owner decision D2 (FX-080). The visitor's identity files: the ID scan, the
# photo and the visa copy on the pass, and the gate log's copies of the first two.
# Security and System Manager may always open them; the approver of the step a
# pass is waiting at may open that pass's files while it waits. Nobody else —
# not the host, not the uploader, not the other readers of the pass.
IDENTITY_FILE_FIELDS = {
	VISITOR_PASS: ("id_proof_scan", "visitor_photo", "custom_visa_copy"),
	SECURITY_LOG: ("id_proof_scan", "visitor_photo"),
}
IDENTITY_FILE_ROLES = ("Security", "System Manager")
# Every permission type that hands the file, or a copy of it, to the user.
_FILE_ACCESS_PTYPES = ("read", "select", "print", "email", "export", "report", "share")


def has_file_permission(doc, ptype=None, user=None, debug=False):
	"""File has_permission: kept documents, and the identity files of D2.

	A permission hook can only refuse, and this one answers True at once for every
	File that is not attached to a Visitor Pass or a Security Log: Frappe's own
	rules decide those, exactly as without this app.

	1. A kept document cannot be changed or removed. The ID scan, the photographs
	   and the visa copy are what the approver and the gate checked the visitor
	   against; a gate log's photos are what the gate saw. Frappe lets a File be
	   deleted by whoever uploaded it and by anyone who can write to the record it
	   is attached to (frappe/core/doctype/file/file.py:has_permission), whatever
	   state that record is in: the host could remove the ID scan of an approved
	   pass from the sidebar, and a guard the photos of a recorded gate log
	   (R3-F06) — the item photo then existed nowhere else.

	   `write` is refused as well because the two go together: a File row that can
	   be edited can be moved off the record (`attached_to_name`) or made public,
	   and a File that is no longer on the record can be deleted by its uploader.
	   For the same reason the row is judged by what is stored, not by the values
	   of the document being saved, which are the caller's.

	   Why a permission hook and not an `on_trash` event: File.on_trash removes the
	   bytes from disk before any doc_events handler runs, so a handler that
	   refused there would keep the row and still lose the image.

	2. An identity file is opened only by the people D2 names
	   (`is_identity_file_refused`). This covers the File record itself — the File
	   form and /api/resource/File. The bytes are served by paths that never ask
	   this hook (see `guard_identity_file_download`). Editing such a row is
	   refused to the same people at any stage, Draft included: made public, the
	   file would be served to anyone.

	Not stopped, on purpose:
	  - the retention purge and the removal of a record together with its files.
	    Both delete with ignore_permissions (tasks._delete_record_files, Frappe's
	    remove_all), which never asks this hook;
	  - deleting a document of a pass back in Draft (Reapply): its owner corrects
	    the documents there by removing one and uploading another;
	  - Administrator, whom Frappe asks no permission of.
	"""
	if not doc.get("name"):
		return True

	if ptype in _FILE_CHANGING_PTYPES:
		return _may_change(doc.get("name"), ptype, user, debug)

	if ptype not in _FILE_ACCESS_PTYPES:
		return True
	if doc.get("attached_to_doctype") not in KEPT_DOCTYPES or not doc.get("attached_to_name"):
		return True
	if not is_identity_file_refused(doc.get("file_url"), user):
		return True
	_log_identity_refusal(debug)
	return False


def _may_change(file_name, ptype, user, debug):
	stored = frappe.db.get_value(
		"File",
		file_name,
		["attached_to_doctype", "attached_to_name", "attached_to_field", "file_url"],
		as_dict=True,
	)
	if not stored or stored.attached_to_doctype not in KEPT_DOCTYPES or not stored.attached_to_name:
		return True

	if _is_kept_document(stored):
		if stored.attached_to_doctype == SECURITY_LOG:
			message = _(
				"This file is a document of Security Log {0}, which has been recorded. "
				"It cannot be changed or removed."
			)
		else:
			message = _(
				"This file is a document of Visitor Pass {0}, which is no longer a Draft. "
				"It cannot be changed or removed."
			)
		push_perm_check_log(message.format(frappe.bold(stored.attached_to_name)), debug=debug)
		return False

	if ptype == "write" and is_identity_file_refused(stored.file_url, user):
		_log_identity_refusal(debug)
		return False
	return True


def _is_kept_document(file_row):
	if file_row.attached_to_doctype == SECURITY_LOG:
		return _is_recorded_log_document(file_row)
	return _is_final_pass_document(file_row)


def _is_final_pass_document(file_row):
	"""Is this File row one of the documents of a pass that has left Draft?

	A document is a file one of the pass's Attach fields holds, or was uploaded
	into. Anything else attached to the pass from the sidebar (an agenda, a
	letter) is not evidence of who the visitor is and stays under Frappe's rules.
	"""
	fieldnames = _attach_fieldnames(VISITOR_PASS)
	visitor_pass = frappe.db.get_value(
		VISITOR_PASS,
		file_row.attached_to_name,
		["docstatus", STATE_FIELD, *fieldnames],
		as_dict=True,
	)
	if not visitor_pass:
		# An unsaved form's "new-..." name, or a pass that no longer exists.
		return False
	if not visitor_pass.docstatus and (visitor_pass.get(STATE_FIELD) or DRAFT) == DRAFT:
		return False

	if file_row.attached_to_field in fieldnames:
		return True
	return bool(file_row.file_url) and file_row.file_url in {visitor_pass.get(f) for f in fieldnames}


def _is_recorded_log_document(file_row):
	"""Is this File row a file that a recorded Security Log holds?

	The log's own Attach fields — the gate photo, the copies of the pass's photo
	and ID scan — and the item photos in its verification rows. Judged by the
	values the saved log holds: an upload into a log that was already locked never
	reached the log (before_save refused it) and is not its evidence.
	"""
	if not file_row.file_url:
		return False
	log_name = file_row.attached_to_name
	fieldnames = _attach_fieldnames(SECURITY_LOG)
	log = frappe.db.get_value(SECURITY_LOG, log_name, ["name", *fieldnames], as_dict=True)
	if not log:
		# An unsaved form's "new-..." name, or a log that no longer exists.
		return False
	if file_row.file_url in {log.get(f) for f in fieldnames}:
		return True
	for table in frappe.get_meta(SECURITY_LOG).get_table_fields():
		for fieldname in _attach_fieldnames(table.options):
			if frappe.db.exists(
				table.options,
				{
					"parent": log_name,
					"parenttype": SECURITY_LOG,
					"parentfield": table.fieldname,
					fieldname: file_row.file_url,
				},
			):
				return True
	return False


# ─────────────────────────────────────────────────────────
# Identity files (owner decision D2)
# ─────────────────────────────────────────────────────────
def is_identity_file_refused(file_url, user=None):
	"""May `user` NOT open the file at `file_url`, under owner decision D2?

	True only when the file is an identity file — one of IDENTITY_FILE_FIELDS of
	a Visitor Pass or Security Log it is attached to — and the user is not
	Administrator, holds neither Security nor System Manager, and cannot act on the
	approval step of a pass that holds the file and is waiting for approval.

	It only ever refuses. Whether the user may open the file at all is still
	Frappe's own rule (the record the file is attached to), applied as before.

	Judged by URL, not by File row: Frappe serves a private file when ANY row for
	its URL is readable (frappe/core/doctype/file/utils.py:find_file_by_url), and
	one file has a row per record that shows it — the pass, its gate logs, a
	returning visitor's next pass.
	"""
	user = user or frappe.session.user
	if not file_url or user == "Administrator":
		return False
	if set(IDENTITY_FILE_ROLES) & set(frappe.get_roles(user)):
		return False

	is_identity, passes = _identity_holders(file_url)
	if not is_identity:
		return False
	return not any(_can_act_on_pending_pass(name, user) for name in passes)


def _identity_holders(file_url):
	"""(is it an identity file, the passes that hold it as one).

	Only the records the file is attached to are looked at: they are the only way
	Frappe's own rule lets anyone but the uploader open it, and the File index on
	file_url keeps this to one cheap query for every other file. The gate logs that
	hold it make it an identity file but give no approver access: an approver has
	no business on a gate log (D2).
	"""
	attached = frappe.get_all(
		"File",
		filters={"file_url": file_url, "attached_to_doctype": ("in", KEPT_DOCTYPES)},
		fields=["attached_to_doctype", "attached_to_name"],
		order_by=None,
	)
	names = {doctype: set() for doctype in KEPT_DOCTYPES}
	for row in attached:
		if row.attached_to_name:
			names[row.attached_to_doctype].add(row.attached_to_name)

	holders = {}
	for doctype, fieldnames in IDENTITY_FILE_FIELDS.items():
		holders[doctype] = (
			frappe.get_all(
				doctype,
				filters={"name": ("in", sorted(names[doctype]))},
				or_filters=[[fieldname, "=", file_url] for fieldname in fieldnames],
				pluck="name",
				order_by=None,
			)
			if names[doctype]
			else []
		)
	passes = holders[VISITOR_PASS]
	return bool(passes or holders[SECURITY_LOG]), passes


def _can_act_on_pending_pass(pass_name, user):
	"""Is the pass waiting for approval at a step `user` can act on?

	Waiting = still docstatus 0 and in a workflow state other than Draft and
	Rejected: the generated workflow's "Pending <role>" lanes. Access ends the
	moment the pass is approved, rejected or moves to the next lane.
	"""
	if user != frappe.session.user:
		# Transition conditions are written for the session user (the "not the
		# host" check in workflow_builder); asked about anyone else, say no.
		return False
	visitor_pass = frappe.db.get_value(
		VISITOR_PASS, pass_name, ["docstatus", STATE_FIELD, "modified"], as_dict=True
	)
	if not visitor_pass or visitor_pass.docstatus != 0:
		return False
	state = visitor_pass.get(STATE_FIELD)
	if not state or state in (DRAFT, REJECTED):
		return False
	return _has_transition_from(pass_name, state, str(visitor_pass.modified), user)


@request_cache
def _has_transition_from(pass_name, state, modified, user):
	"""Does the workflow offer `user` an action on this pass in `state`?

	The same test Frappe's get_transitions and apply_workflow apply (role, the
	transition's condition, self-approval), without its permission check and
	throw. `modified` is part of the cache key, so a pass that was approved
	earlier in the same request is judged again.
	"""
	from frappe.model.workflow import (
		get_workflow,
		get_workflow_name,
		has_approval_access,
		is_transition_condition_satisfied,
	)

	if not get_workflow_name(VISITOR_PASS):
		return False
	roles = set(frappe.get_roles(user))
	transitions = [
		t for t in get_workflow(VISITOR_PASS).transitions if t.state == state and t.allowed in roles
	]
	if not transitions:
		return False
	doc = frappe.get_doc(VISITOR_PASS, pass_name)
	return any(
		has_approval_access(user, doc, t) and is_transition_condition_satisfied(t, doc) for t in transitions
	)


def _identity_refusal_message():
	return _("This file is a visitor's identity document. You do not have permission to open it.")


def _log_identity_refusal(debug=False):
	push_perm_check_log(_identity_refusal_message(), debug=debug)


# The whitelisted methods that send a file's bytes (frappe/handler.py:download_file,
# frappe/core/api/file.py:zip_files), by their own name, which every alias and the
# v2 "/api/v2/method/File/<method>" form resolve to.
_BYTE_METHOD_NAMES = ("download_file", "zip_files")
_API_V2_DOCTYPE_METHOD = re.compile(r"^/api/v2/method/([^/]+)/([^/]+)/?$")
_API_METHOD = re.compile(r"^/api/(?:v1/|v2/)?method/([^/]+)")


def guard_identity_file_download():
	"""auth_hooks: refuse the bytes of an identity file to the people D2 keeps them from.

	Frappe hands out a private file's bytes through /private/files/<name>,
	`download_file` and `zip_files`. None of them asks the File has_permission
	hooks: they call File.is_downloadable / the File controller's own
	has_permission directly (frappe/utils/response.py:download_private_file,
	frappe/handler.py:download_file, frappe/core/doctype/file/file.py:is_downloadable
	and File.zip_files), which grants a file to everyone who can read the record it
	is attached to — every reader of the pass. `has_file_permission` alone would
	leave the image in the form, the sidebar link and the URL open to all of them.

	An auth hook runs on every request once the user is known — from the session
	cookie, an API key or an OAuth token — and before the request is dispatched,
	so it is the one place the app can see these requests. It only refuses: any
	other request returns at the first lines, and a file this does not refuse is
	still judged by Frappe as before. The refusal is Frappe's own, word for word,
	so it tells nobody whether the file exists.
	"""
	request = getattr(frappe.local, "request", None)
	if not request or request.method == "OPTIONS":
		return
	path = request.path or ""

	# Checked first: frappe/app.py runs a form `cmd` before it looks at the path,
	# so "/private/files/x?cmd=...download_file&file_url=..." is a download_file call.
	method = _requested_byte_method(path)
	if method:
		if method is _download_file():
			file_urls = [frappe.form_dict.get("file_url")]
		else:
			file_urls = _zip_file_urls(frappe.form_dict.get("files"))
		if any(is_identity_file_refused(url) for url in file_urls if isinstance(url, str)):
			raise frappe.PermissionError(_identity_refusal_message())
		return
	if frappe.form_dict.get("cmd"):
		return

	if path.startswith("/private/files/") and is_identity_file_refused(path):
		raise Forbidden(_("You don't have permission to access this file"))


def _download_file():
	from frappe.handler import download_file

	return download_file


def _zip_files():
	from frappe.core.api.file import zip_files

	return zip_files


def _requested_byte_method(path):
	"""The function this request runs, if it is `download_file` or `zip_files`.

	Named the way Frappe resolves it: a form `cmd` first (frappe/app.py runs it
	whatever the path), then the /api/method/ path, then any
	override_whitelisted_methods alias; compared as the function itself, so
	another module that re-exports either one is caught as well.
	"""
	method = frappe.form_dict.get("cmd")
	if not method:
		match = _API_V2_DOCTYPE_METHOD.match(path)
		if match:
			doctype, name = match.groups()
			if name not in _BYTE_METHOD_NAMES:
				return None
			from frappe.modules.utils import load_doctype_module

			try:
				method = f"{load_doctype_module(doctype).__name__}.{name}"
			except Exception:
				return None
		else:
			match = _API_METHOD.match(path)
			if not match:
				return None
			method = match.group(1)

	method = frappe.override_whitelisted_method(str(method))
	if method.rsplit(".", 1)[-1] not in _BYTE_METHOD_NAMES:
		return None
	try:
		if "." in method:
			function = frappe.get_attr(method)
		else:
			# A bare name is looked up in frappe.handler (frappe/handler.py:get_attr).
			from frappe import handler

			function = getattr(handler, method, None)
	except Exception:
		return None
	if function is _download_file() or function is _zip_files():
		return function
	return None


def _zip_file_urls(files):
	try:
		files = frappe.parse_json(files)
	except Exception:
		return []
	if not isinstance(files, list):
		return []
	names = [f if isinstance(f, str) else (f or {}).get("name") for f in files if isinstance(f, str | dict)]
	names = [name for name in names if isinstance(name, str)]
	if not names:
		return []
	return frappe.get_all("File", filters={"name": ("in", names)}, pluck="file_url", order_by=None)


def refuse_identity_file_copy(doc, method=None):
	"""File before_insert: an identity file is not copied onto another record.

	The copy carries the same bytes under a new row that the identity rule does
	not cover. E-mailing a pass from the desk with its ID scan ticked as an
	attachment does exactly that: Frappe copies the File onto the Communication
	(frappe/core/doctype/communication/email.py:add_attachments, ignore_permissions)
	and the mail sends the bytes to any address. Refused to the people D2 keeps the
	file from; a copy onto a Visitor Pass or Security Log is under the rule itself,
	and an unattached upload (the guest portal, the file manager) is the uploader's
	own bytes, so neither is looked at.
	"""
	if doc.get("is_folder") or not doc.get("file_url"):
		return
	if not doc.get("attached_to_doctype") or doc.get("attached_to_doctype") in KEPT_DOCTYPES:
		return
	if is_identity_file_refused(doc.file_url):
		frappe.throw(
			_("This file is a visitor's identity document. It cannot be attached to another record."),
			frappe.PermissionError,
		)


def adopt_stray_uploads(doc):
	"""Move this record's own uploads off the unsaved form's name, whatever their age.

	Called from on_update, which runs before Frappe's attach_files_to_document
	hook, so the hook then finds the file already attached instead of making a
	second row for it.

	Only the record's creator's own upload into this same field is adopted. A
	stray is the creator's upload on the unsaved form; matching on the URL alone
	let anyone who can edit a pass paste another user's unsaved upload URL (File
	rows are listable) and take that row — and with it read access to the file.
	"""
	for fieldname in _attach_fieldnames(doc.doctype):
		file_url = doc.get(fieldname)
		if not file_url:
			continue
		stray = frappe.get_all(
			"File",
			filters={
				"file_url": file_url,
				"attached_to_doctype": doc.doctype,
				"attached_to_name": ("like", "new-%"),
				"attached_to_field": fieldname,
				"owner": doc.owner,
			},
			pluck="name",
			limit=1,
		)
		if stray:
			frappe.db.set_value(
				"File",
				stray[0],
				{"attached_to_name": doc.name, "attached_to_field": fieldname},
				update_modified=False,
			)


def attach_existing_file(file_url, doctype, name, fieldname):
	"""Give `doctype/name` its own File row for a file that already exists.

	Same bytes, no copy on disk (Frappe reuses the stored file for a known URL);
	it only makes the file readable to whoever can read that record.
	"""
	if not file_url or frappe.db.exists(
		"File", {"file_url": file_url, "attached_to_doctype": doctype, "attached_to_name": name}
	):
		return
	frappe.get_doc(
		{
			"doctype": "File",
			"file_url": file_url,
			"is_private": 1 if file_url.startswith("/private/") else 0,
			"attached_to_doctype": doctype,
			"attached_to_name": name,
			"attached_to_field": fieldname,
		}
	).insert(ignore_permissions=True)


def copy_file_row(source_file, doc, fieldname=None):
	"""Attach an existing File to `doc` by writing a copy of its row.

	Same bytes and hash — nothing is stored twice. Written directly because a
	normal insert re-checks private-file access against ONE File row per URL,
	the newest (File.validate_private_file_access, limit=1); for a visitor who
	went through the gate that is a Security Log an approver or host cannot read,
	so a legitimate copy was refused. Callers check the user may read the record
	the file comes from, so no one gains a file they could not already open.
	"""
	src = frappe.db.get_value(
		"File",
		source_file,
		["file_name", "file_url", "is_private", "file_size", "content_hash", "attached_to_field"],
		as_dict=True,
	)
	if not src or frappe.db.exists(
		"File", {"file_url": src.file_url, "attached_to_doctype": doc.doctype, "attached_to_name": doc.name}
	):
		return
	row = frappe.new_doc("File")
	row.update(
		{
			"file_name": src.file_name,
			"file_url": src.file_url,
			"is_private": src.is_private,
			"file_size": src.file_size,
			"content_hash": src.content_hash,
			"folder": "Home/Attachments",
			"attached_to_doctype": doc.doctype,
			"attached_to_name": doc.name,
			"attached_to_field": fieldname or src.attached_to_field,
		}
	)
	row.set_new_name()
	row.set_user_and_timestamp()
	row.db_insert()


def share_files_from_source_pass(doc):
	"""A returning visitor's pass reuses the earlier pass's photo / ID scan / visa.

	Frappe's attach hook would re-check access against the newest File row for the
	URL (see copy_file_row) and refuse the host. Copy the earlier pass's own row
	instead — only for a value identical to that pass's, and only when the user can
	read that pass. Runs from on_update, before Frappe's hook, which then finds it.
	"""
	source = doc.get("existing_visitor_pass")
	if not source or not frappe.has_permission("Visitor Pass", "read", source):
		return
	for fieldname in _attach_fieldnames(doc.doctype):
		url = doc.get(fieldname)
		if not url or not url.startswith("/private/"):
			continue
		original = frappe.db.get_value(
			"File", {"file_url": url, "attached_to_doctype": doc.doctype, "attached_to_name": source}, "name"
		)
		if original:
			copy_file_row(original, doc, fieldname)


def saved_record_using(doctype, file_url):
	"""(name, fieldname) of a saved `doctype` record that uses file_url, or None.

	Looks in the record's own Attach fields and in its child tables' (a Security
	Log's item photos), so a file still in use is never mistaken for a stray one.
	"""
	if not file_url:
		return None
	for fieldname in _attach_fieldnames(doctype):
		name = frappe.db.get_value(doctype, {fieldname: file_url}, "name")
		if name:
			return name, fieldname
	for table in frappe.get_meta(doctype).get_table_fields():
		for fieldname in _attach_fieldnames(table.options):
			parent = frappe.db.get_value(
				table.options, {fieldname: file_url, "parenttype": doctype}, "parent"
			)
			if parent:
				return parent, None
	return None
