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

import frappe

ATTACH_TYPES = ("Attach", "Attach Image")


def _attach_fieldnames(doctype):
	return [df.fieldname for df in frappe.get_meta(doctype).fields if df.fieldtype in ATTACH_TYPES]


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
