"""Masking of government ID numbers, and the one audited way to read a full one.

The full number stays in the database, because matching needs it: the blacklist,
the returning-visitor lookup, the duplicate same-day check and format validation
all compare whole numbers. What must not happen is the full number reaching a
browser. So every DocType that carries one keeps three fields:

* ``<field>`` (for example ``id_proof_number``) holds the full number at
  permlevel 1, and no role is granted permlevel 1, so Frappe strips it from
  every form load, list, report, export and API read (Administrator excepted);
* ``<field>_masked`` is what people see. It is maintained on every save with
  `set_masked`;
* ``<field>_entry`` is where a person types a number. The controller moves it
  into the stored field in ``validate`` (`consume_entry`) and blanks it, so the
  full number is never kept in a column a browser can read.

Only a System Manager may read a full number back, through `reveal_id`, and
every such read is recorded (`log_reveal`).

Interface used across the app (keep these names and signatures):
`mask_id`, `can_view_full_id`, `MASKED_FIELD_SUFFIX`, `set_masked`, `reveal_id`,
`log_reveal`. The other helpers here (`consume_entry`, `hide_from_version`,
`backfill_masked`, `scrub_versions`) are optional conveniences for the owners
of those DocTypes.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, get_fullname

MASKED_FIELD_SUFFIX = "_masked"
ENTRY_FIELD_SUFFIX = "_entry"

MASK_CHAR = "X"
DEFAULT_VISIBLE_TRAILING_CHARS = 4
MAX_VISIBLE_TRAILING_CHARS = 6

# The one role that may read a full number back (through `reveal_id`).
FULL_ID_ROLE = "System Manager"

# `subject` of the timeline note `log_reveal` writes.
REVEAL_COMMENT_SUBJECT = "VMS full ID number viewed"

# UIDAI's masked-Aadhaar layout: two hidden groups, the last four digits shown.
_AADHAAR_LENGTH = 12
_AADHAAR_GROUP = 4
_AADHAAR_METHOD = "Aadhaar (Verhoeff)"

# What `mask_id` returns when a value cannot be masked safely: nothing of it.
_FULLY_MASKED = MASK_CHAR * 4


# ─────────────────────────────────────────────────────────
# Masking
# ─────────────────────────────────────────────────────────
def _type_config(id_type):
	"""(is_aadhaar, visible_trailing_chars) for an ID Proof Type name or alias.

	Read from the cached ID Proof Type master (validators._load_master). An
	unknown, inactive or deleted type gets the default: a legacy row must still
	mask, never fail.
	"""
	if not id_type:
		return False, DEFAULT_VISIBLE_TRAILING_CHARS
	try:
		from visitormanagement.visitor_management import validators

		canonical = validators._canonical_type(str(id_type))
		config = validators._load_master().get(canonical) if canonical else None
	except Exception:
		return False, DEFAULT_VISIBLE_TRAILING_CHARS

	if not config:
		return canonical == "Aadhaar", DEFAULT_VISIBLE_TRAILING_CHARS

	visible = config.get("visible_trailing")
	if visible is None:  # cached before the setting existed
		visible = DEFAULT_VISIBLE_TRAILING_CHARS
	visible = min(max(cint(visible), 0), MAX_VISIBLE_TRAILING_CHARS)
	return config.get("method") == _AADHAAR_METHOD or canonical == "Aadhaar", visible


def _mask(raw, visible, aadhaar=False):
	positions = [i for i, ch in enumerate(raw) if ch.isalnum()]
	count = len(positions)
	if not count:
		# Only separators or symbols: there is nothing recognisable to keep.
		return _FULLY_MASKED

	if aadhaar and count == _AADHAAR_LENGTH:
		characters = "".join(raw[i] for i in positions)
		# Digits, or an already masked value (so masking twice changes nothing).
		if all(ch.isdigit() or ch in "Xx" for ch in characters):
			keep = min(visible, _AADHAAR_GROUP)
			tail = MASK_CHAR * (_AADHAAR_GROUP - keep) + (
				characters[_AADHAAR_LENGTH - keep :] if keep else ""
			)
			hidden = MASK_CHAR * _AADHAAR_GROUP
			return f"{hidden}-{hidden}-{tail}"

	# Never show more than half of a short value: a 4-character number masked to
	# "its last 4" would not be masked at all.
	keep = min(visible, count // 2)
	shown = set(positions[count - keep :]) if keep else set()
	return "".join(ch if (i in shown or not ch.isalnum()) else MASK_CHAR for i, ch in enumerate(raw))


def mask_id(id_type: str | None, number: str | None) -> str:
	"""The masked form of an ID number. `""` for an empty value; never raises.

	Aadhaar  2345 6789 0123   ->  XXXX-XXXX-0123   (UIDAI masked-Aadhaar style)
	PAN      AABPR2345T       ->  XXXXXX345T
	Passport P1234567         ->  XXXX4567
	DL       TN05 20210012345 ->  XXXX XXXXXXX2345

	Every other type shows its last N letters or digits and keeps separators
	where they were. N is the type's "Visible Trailing Characters" (ID Proof
	Type, default 4, 0 to 6), and never more than half of the value. Masking an
	already masked value returns it unchanged.
	"""
	try:
		raw = "" if number is None else str(number).strip()
		if not raw:
			return ""
		is_aadhaar, visible = _type_config(id_type)
		return _mask(raw, visible, aadhaar=is_aadhaar)
	except Exception:
		# A value that cannot be masked is shown as nothing at all, never as itself.
		return _FULLY_MASKED


def can_view_full_id(user: str | None = None) -> bool:
	"""True only for a System Manager (and Administrator)."""
	user = user or frappe.session.user
	if not user or user == "Guest":
		return False
	if user == "Administrator":
		return True
	return FULL_ID_ROLE in frappe.get_roles(user)


def set_masked(doc, source_field="id_proof_number", type_field="id_proof_type") -> None:
	"""Refresh `doc.<source_field>_masked` from the stored full value.

	Call it on every save, after the full value is final. Works on a child row
	as well as on a document.
	"""
	id_type = doc.get(type_field) if type_field else None
	doc.set(source_field + MASKED_FIELD_SUFFIX, mask_id(id_type, doc.get(source_field)))


def consume_entry(doc, source_field="id_proof_number", type_field="id_proof_type", normalise=True) -> bool:
	"""Move a typed number from `<source_field>_entry` into the stored field.

	Returns True when a new number was taken. The entry field is always blanked,
	so the full number is never saved in a permlevel-0 column, and the masked
	field is refreshed either way. Run it in `validate`: Frappe restores the
	stored (permlevel 1) value before `validate` runs and does not touch it
	afterwards (Document.validate_higher_perm_levels is called before
	run_before_save_methods), so what is set here is what gets saved.

	With `normalise`, the number is normalised by its ID Proof Type's rule
	(validators.normalise_id_number); callers that validate the format do so
	themselves, before or after, on `doc.<source_field>`.
	"""
	entry_field = source_field + ENTRY_FIELD_SUFFIX
	raw = doc.get(entry_field)
	typed = raw.strip() if isinstance(raw, str) else ""
	doc.set(entry_field, None)
	taken = False
	if typed:
		if normalise and type_field and doc.get(type_field):
			from visitormanagement.visitor_management import validators

			typed = validators.normalise_id_number(doc.get(type_field), typed) or typed
		taken = typed != (doc.get(source_field) or "")
		doc.set(source_field, typed)
	set_masked(doc, source_field, type_field)
	return taken


def hide_from_version(doc, *fieldnames) -> None:
	"""Keep a change to these fields out of the document's Version (timeline).

	Version stores the old and new value of every changed field, whatever its
	permlevel, and the form loads those rows as they are
	(frappe.desk.form.load.get_versions). Call this from `on_update` (it runs
	before Document.save_version): the copy Frappe compares against is given
	the new value, so no difference is recorded for these fields. The masked
	field still changes, and that is what the timeline shows.
	"""
	before = doc.get_doc_before_save() if hasattr(doc, "get_doc_before_save") else None
	if not before:
		return
	for fieldname in fieldnames or ("id_proof_number",):
		before.set(fieldname, doc.get(fieldname))


# ─────────────────────────────────────────────────────────
# Reading a full number back (audited)
# ─────────────────────────────────────────────────────────
def _is_app_doctype(doctype):
	from visitormanagement.visitor_management.link_details import APP_MODULES

	return frappe.db.get_value("DocType", doctype, "module") in APP_MODULES


def _is_protected_field(doctype, fieldname):
	"""A stored ID field is one that has a `<fieldname>_masked` companion."""
	meta = frappe.get_meta(doctype)
	return bool(meta.has_field(fieldname) and meta.has_field(fieldname + MASKED_FIELD_SUFFIX))


def _find_row(doc, row_name):
	for table in doc.meta.get_table_fields():
		for row in doc.get(table.fieldname) or []:
			if row.name == row_name:
				return row
	return None


@frappe.whitelist(methods=["POST"])
def reveal_id(
	doctype: str, name: str, fieldname: str = "id_proof_number", row_name: str | None = None
) -> str:
	"""The full ID number of one record, for a System Manager, recorded every time.

	`doctype`/`name` is one of this app's documents; `row_name` picks a row of
	one of its child tables (a group member). The caller must be a System
	Manager AND be able to read the document. The reveal is written to the
	audit trail before the value is returned; if it cannot be recorded, nothing
	is returned.
	"""
	if not can_view_full_id():
		frappe.throw(_("Only a System Manager can view a full ID number."), frappe.PermissionError)
	if not (isinstance(doctype, str) and isinstance(name, str) and isinstance(fieldname, str)):
		frappe.throw(_("Invalid request."), frappe.ValidationError)
	if not frappe.db.exists("DocType", doctype) or not _is_app_doctype(doctype):
		frappe.throw(_("ID numbers cannot be shown for {0}.").format(doctype), frappe.PermissionError)
	if frappe.get_meta(doctype).istable:
		frappe.throw(_("Open the document the row belongs to."), frappe.PermissionError)

	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")

	target = doc
	if row_name:
		target = _find_row(doc, row_name)
		if not target:
			frappe.throw(_("Row {0} was not found in {1}.").format(row_name, name), frappe.DoesNotExistError)
	if not _is_protected_field(target.doctype, fieldname):
		frappe.throw(_("{0} is not an ID number field.").format(fieldname), frappe.PermissionError)

	value = target.get(fieldname)
	if not value:
		return ""
	log_reveal(doctype, name, fieldname, row_name=row_name)
	return str(value)


def log_reveal(doctype, name, fieldname="id_proof_number", row_name=None, user=None) -> None:
	"""Record that `user` read a full ID number: who, when, which document and field.

	Written twice, on purpose:

	* an Activity Log row. System Manager can read Activity Log but has no
	  write or delete right on it (frappe/core/doctype/activity_log), so the
	  person who looked cannot edit or remove the record of having looked.
	  Frappe clears Activity Log after the period set in Log Settings (90 days
	  unless the site changed it);
	* an "Info" comment on the document, which stays in its timeline for as
	  long as the document exists and is visible to everyone who can open it.

	Visitor Event Log was considered and not used: it needs a Visitor Pass (a
	blacklist entry has none) and System Manager can edit and delete its rows.

	Raises if the Activity Log row cannot be written — `reveal_id` then returns
	nothing.
	"""
	user = user or frappe.session.user
	meta = frappe.get_meta(doctype)
	label = fieldname
	df = meta.get_field(fieldname)
	if df and df.label:
		label = df.label
	where = f"{doctype} {name}" + (f" (row {row_name})" if row_name else "")

	frappe.get_doc(
		{
			"doctype": "Activity Log",
			"subject": f"Full ID number viewed: {where}",
			"content": (
				f"{get_fullname(user)} ({user}) viewed the full value of {label} [{fieldname}] on {where}."
			),
			"reference_doctype": doctype,
			"reference_name": name,
			"user": user,
			"ip_address": getattr(frappe.local, "request_ip", None),
		}
	).insert(ignore_permissions=True)

	try:
		frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Info",
				# Tells this note apart from other Info comments: the retention
				# purge removes those and keeps this one.
				"subject": REVEAL_COMMENT_SUBJECT,
				"reference_doctype": doctype,
				"reference_name": name,
				"comment_email": user,
				"comment_by": get_fullname(user),
				"content": _("Full ID number viewed by {0}").format(
					frappe.utils.escape_html(get_fullname(user))
				),
			}
		).insert(ignore_permissions=True)
	except Exception:
		# The tamper-resistant record above is the audit; the timeline note is a
		# convenience and must not undo it.
		frappe.log_error(title="VMS ID reveal: timeline comment failed")


# ─────────────────────────────────────────────────────────
# Existing data (for the upgrade steps)
# ─────────────────────────────────────────────────────────
def backfill_masked(doctype, source_field="id_proof_number", type_field="id_proof_type") -> int:
	"""Fill `<source_field>_masked` on existing rows of `doctype`. Returns the count.

	Idempotent: only rows whose masked value differs from what the stored number
	masks to are written, so a second run changes nothing. `modified` is left
	alone — this derives a column, it is not an edit. Works for child tables.
	"""
	masked_field = source_field + MASKED_FIELD_SUFFIX
	if not frappe.db.table_exists(doctype):
		return 0
	if not (frappe.db.has_column(doctype, source_field) and frappe.db.has_column(doctype, masked_field)):
		return 0
	fields = ["name", source_field, masked_field]
	has_type = bool(type_field and frappe.db.has_column(doctype, type_field))
	if has_type:
		fields.append(type_field)

	changed = 0
	for row in frappe.get_all(
		doctype,
		or_filters=[[source_field, "is", "set"], [masked_field, "is", "set"]],
		fields=fields,
		order_by=None,
	):
		expected = mask_id(row.get(type_field) if has_type else None, row.get(source_field))
		if (row.get(masked_field) or "") == expected:
			continue
		frappe.db.set_value(doctype, row.name, masked_field, expected or None, update_modified=False)
		changed += 1
	return changed


def _mask_untyped(value):
	return mask_id(None, value)


def _scrub_change(change, fieldnames, replace):
	"""Rewrite the old and new value of one `[fieldname, old, new]` Version entry."""
	if not (isinstance(change, list) and len(change) >= 3 and change[0] in fieldnames):
		return False
	scrubbed = [change[0], replace(change[1]), replace(change[2])]
	if scrubbed == change[:3]:
		return False
	change[:3] = scrubbed
	return True


def scrub_versions(doctype, fieldnames=("id_proof_number",), names=None, replace=None) -> int:
	"""Mask full ID numbers already stored in `doctype`'s Version rows. Returns the count.

	Version kept the old and new value of every change to these fields, and the
	form loads the latest rows into the timeline as they are. This rewrites
	those values to their masked form, in the document's own fields and in its
	child table rows; nothing else in a Version row is touched. Idempotent: a
	masked value masks to itself. `names` limits it to some documents.

	`replace` (value -> value) swaps masking for another rewrite; the retention
	purge uses it to blank identity fields. It must return its own output
	unchanged, or the rows are rewritten on every run.
	"""
	replace = replace or _mask_untyped
	fieldnames = set(fieldnames)
	filters = {"ref_doctype": doctype}
	if names is not None:
		if not names:
			return 0
		filters["docname"] = ("in", list(names))

	scrubbed = 0
	for row in frappe.get_all("Version", filters=filters, fields=["name", "data"], order_by=None):
		if not row.data or not any(f'"{fieldname}"' in row.data for fieldname in fieldnames):
			continue
		try:
			data = json.loads(row.data)
		except (ValueError, TypeError):
			continue
		if not isinstance(data, dict):
			continue

		touched = False
		for change in data.get("changed") or []:
			touched = _scrub_change(change, fieldnames, replace) or touched
		for entry in data.get("row_changed") or []:
			# [table_fieldname, row_index, row_name, [[fieldname, old, new], ...]]
			if isinstance(entry, list) and len(entry) >= 4 and isinstance(entry[3], list):
				for change in entry[3]:
					touched = _scrub_change(change, fieldnames, replace) or touched
		for key in ("added", "removed"):
			for entry in data.get(key) or []:
				# [table_fieldname, {row as a dict}]
				if not (isinstance(entry, list) and len(entry) >= 2 and isinstance(entry[1], dict)):
					continue
				for fieldname in fieldnames:
					value = entry[1].get(fieldname)
					if value and replace(value) != value:
						entry[1][fieldname] = replace(value)
						touched = True

		if touched:
			frappe.db.set_value(
				"Version", row.name, "data", frappe.as_json(data, indent=None), update_modified=False
			)
			scrubbed += 1
	return scrubbed
