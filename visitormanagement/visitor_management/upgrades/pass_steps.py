"""Upgrade steps for Visitor Pass, its group members and its change history.

Called by `upgrades.run_all()` on install and on every migrate. Everything here
can run any number of times; the two history clean-ups are one-time and carry a
`vms_` marker.

What it does for a site that held passes before ID numbers were masked:

1. fills `id_proof_number_masked` on existing passes and group member rows, so
   every screen has something to show the moment the full number disappears
   from it;
2. masks the full ID numbers that the change history (Version) of those passes
   already holds — the form sends Version rows to the browser as they are;
3. takes full ID numbers out of the Deleted Document copies of passes, which a
   System Manager could otherwise read without the reveal being recorded.
"""

import json

import frappe

from visitormanagement.visitor_management.id_masking import mask_id, scrub_versions

# Rows read per query. Small enough to keep memory flat on a site with years of
# passes, large enough that the step is a few hundred queries at most.
BATCH_SIZE = 1000

PRIVATE_ID_FIELDS = ("id_proof_number", "id_proof_number_entry")

# The DocTypes in this area that carry the three ID number fields.
MASKED_DOCTYPES = ("Visitor Pass", "Visitor Group Member")

VERSION_SCRUB_MARKER = "vms_pass_version_id_scrub_done"
DELETED_SCRUB_MARKER = "vms_pass_deleted_document_id_scrub_done"


def run():
	for doctype in MASKED_DOCTYPES:
		changed = backfill_masked_numbers(doctype)
		if changed:
			print(f"  filled the masked ID number on {changed} {doctype} row(s)")

	if not frappe.db.get_default(VERSION_SCRUB_MARKER):
		scrubbed = scrub_pass_versions()
		frappe.db.set_default(VERSION_SCRUB_MARKER, "1")
		print(f"  masked full ID numbers in {scrubbed} Visitor Pass change-history row(s)")

	if not frappe.db.get_default(DELETED_SCRUB_MARKER):
		scrubbed = scrub_deleted_passes()
		frappe.db.set_default(DELETED_SCRUB_MARKER, "1")
		print(f"  removed full ID numbers from {scrubbed} deleted Visitor Pass record(s)")


def _ready(doctype):
	"""The masked column exists (DocType sync has run for this version)."""
	return (
		frappe.db.table_exists(doctype)
		and frappe.db.has_column(doctype, "id_proof_number")
		and frappe.db.has_column(doctype, "id_proof_number_masked")
	)


def backfill_masked_numbers(doctype, batch_size=BATCH_SIZE) -> int:
	"""Set `id_proof_number_masked` wherever it differs from the stored number's mask.

	Idempotent: a row that is already right is not written, so a second run
	changes nothing. Also corrects a masked value left behind after the stored
	number was cleared (retention purge), and blanks a typed number that somehow
	stayed in the entry field. `modified` is not touched: this derives a column,
	it is not an edit of the record. Walks the table by name, `batch_size` rows
	at a time.
	"""
	if not _ready(doctype):
		return 0
	has_entry = frappe.db.has_column(doctype, "id_proof_number_entry")
	fields = ["name", "id_proof_type", "id_proof_number", "id_proof_number_masked"]
	or_filters = [["id_proof_number", "is", "set"], ["id_proof_number_masked", "is", "set"]]
	if has_entry:
		fields.append("id_proof_number_entry")
		or_filters.append(["id_proof_number_entry", "is", "set"])

	changed = 0
	last = None
	while True:
		rows = frappe.get_all(
			doctype,
			filters={"name": [">", last]} if last is not None else {},
			or_filters=or_filters,
			fields=fields,
			order_by="name asc",
			limit=batch_size,
		)
		if not rows:
			break
		for row in rows:
			values = {}
			expected = mask_id(row.id_proof_type, row.id_proof_number) or None
			if (row.id_proof_number_masked or None) != expected:
				values["id_proof_number_masked"] = expected
			if has_entry and row.id_proof_number_entry:
				values["id_proof_number_entry"] = None
			if values:
				frappe.db.set_value(doctype, row.name, values, update_modified=False)
				changed += 1
		last = rows[-1].name
	return changed


def scrub_pass_versions(batch_size=500) -> int:
	"""Mask the ID numbers already written to the passes' Version rows.

	Works through the passes that have history, `batch_size` documents at a
	time, and leaves the masking itself to id_masking.scrub_versions (which
	rewrites only the two ID fields, in the pass's own changes and in its group
	member rows).
	"""
	scrubbed = 0
	last = None
	while True:
		filters = {"ref_doctype": "Visitor Pass"}
		if last is not None:
			filters["docname"] = [">", last]
		docnames = frappe.get_all(
			"Version",
			filters=filters,
			pluck="docname",
			distinct=True,
			order_by="docname asc",
			limit=batch_size,
		)
		if not docnames:
			break
		scrubbed += scrub_versions("Visitor Pass", PRIVATE_ID_FIELDS, names=docnames)
		last = docnames[-1]
	return scrubbed


def _strip_private_ids(record) -> bool:
	"""Drop the two private ID fields from a serialised pass or member row."""
	touched = False
	if not isinstance(record, dict):
		return touched
	for fieldname in PRIVATE_ID_FIELDS:
		if record.pop(fieldname, None):
			touched = True
	for value in record.values():
		if isinstance(value, list):
			for row in value:
				touched = _strip_private_ids(row) or touched
	return touched


def scrub_deleted_passes(batch_size=BATCH_SIZE) -> int:
	"""Take full ID numbers out of the Deleted Document copies of passes.

	Deleting a pass used to archive the whole document, ID number included, in
	Deleted Document. From this version a serialised pass never carries the
	number (VisitorPass.as_dict), so new copies are clean; this cleans the old
	ones. The masked number and everything else in the copy stay.
	"""
	scrubbed = 0
	last = None
	while True:
		filters = {"deleted_doctype": "Visitor Pass"}
		if last is not None:
			filters["name"] = [">", last]
		rows = frappe.get_all(
			"Deleted Document",
			filters=filters,
			fields=["name", "data"],
			order_by="name asc",
			limit=batch_size,
		)
		if not rows:
			break
		for row in rows:
			if not row.data or "id_proof_number" not in row.data:
				continue
			try:
				record = json.loads(row.data)
			except (TypeError, ValueError):
				continue
			if _strip_private_ids(record):
				frappe.db.set_value(
					"Deleted Document", row.name, "data", frappe.as_json(record), update_modified=False
				)
				scrubbed += 1
		last = rows[-1].name
	return scrubbed
