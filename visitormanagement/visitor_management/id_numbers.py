"""A visitor's ID proof number: kept whole in the database, shown masked.

`id_proof_number` holds the real Aadhaar / PAN / passport number, unchanged —
the blacklist, the duplicate-pass check and the returning-visitor lookup all
compare it. Only Security, who check the card at the gate, and System Manager
can see it (FULL_ID_READERS). Two things keep it from everyone else:

- It sits at permission level 1 (FULL_ID_PERMLEVEL), and only those two roles
  hold a level-1 rule on Visitor Pass. Frappe drops a field outside the user's
  levels from every read that reaches a browser — the form, REST, list and
  report views, exports — and refuses to filter, sort or group by it. Masking
  alone hid the value but still let any reader search on it, so a run of
  "starts with" filters rebuilt the number one character at a time.
- It is a masked field (DocField.mask) and the level-1 rules carry the unmask
  permission, so a role later given level-1 read without it still sees XXXXXXXX.

The change history is kept masked as well (mask_version): Frappe's Version
stores the old and new value of every field as plain text.

A user without level-1 write cannot set the field, so people do not type into
it. They type into `id_proof_masked` ("ID Proof Number"), which after
every save shows only the last four characters, e.g. `XXXXXX234F`. Saving with
a new full number there replaces the real one; leaving the masked value as it
is keeps the real one.

Custom endpoints that return the number to the browser must pass it through
`shown_id`; Frappe's masking cannot reach a raw SQL result or a hand-built dict.
The same applies to accompanying visitors (Visitor Group Member).
"""

import frappe
from frappe import _

from visitormanagement.visitor_management.validators import is_masked_id, mask_id_number

ID_FIELD = "id_proof_number"
# Visitor Pass and Visitor Group Member both keep the field at this level.
FULL_ID_PERMLEVEL = 1
FULL_ID_READERS = ("Security", "System Manager")


def can_see_full_id():
	"""Whether the current user may see full ID numbers (holds the unmask permission)."""
	masked = frappe.get_meta("Visitor Pass").get_masked_fields()
	return not any(df.fieldname == "id_proof_number" for df in masked)


def shown_id(real):
	"""What a custom endpoint may return for an ID number to the current user."""
	if not real:
		return real
	return real if can_see_full_id() else mask_id_number(real)


def take_typed_id(row, source_full=None):
	"""The real ID number for `row` (a Visitor Pass or Visitor Group Member) in validate().

	`id_proof_masked` is what the user typed or left as it was; `id_proof_number`
	is the real number — for an existing record Frappe has already restored it
	from the database for users who only ever saw it masked. `source_full` is the
	real number of the pass a returning visitor's details were copied from.
	"""
	shown = (row.get("id_proof_masked") or "").strip()
	real = (row.get("id_proof_number") or "").strip()
	if is_masked_id(real):
		real = ""  # the XXXXXXXX placeholder, carried in by a copy

	if shown and not is_masked_id(shown):
		return shown  # a full number typed by a person

	if not shown:
		# Set directly by code — the portal, the candidate flow, an import.
		if real:
			return real
		if source_full:
			return source_full
		return None

	# The masked form: unchanged, or carried over from another pass.
	for candidate in (real, source_full):
		if candidate and mask_id_number(candidate) == shown:
			return candidate

	frappe.throw(
		_(
			"Please enter the full ID proof number. Only its last four characters "
			"are shown once a pass is saved, so it cannot be copied from one."
		),
		title=_("ID Proof Number Needed"),
	)


def set_shown_id(row):
	"""At the end of validate(): show only the last four of the real number."""
	real = row.get("id_proof_number")
	row.id_proof_masked = mask_id_number(real) if real else None


def mask_version(version):
	"""Mask every ID number in a Version's diff before it is stored.

	`version.data` is what Version.update_version_info() built: "changed" holds
	[fieldname, old, new]; "row_changed" holds [table, idx, row name, changes];
	"added" and "removed" hold [table, row]. The pass's own number and its group
	members' are all `id_proof_number`.
	"""
	data = frappe.parse_json(version.data or "{}")

	def mask(value):
		return mask_id_number(value) if isinstance(value, str) and value else value

	def mask_changes(changes):
		return [
			[change[0], mask(change[1]), mask(change[2])] if change[0] == ID_FIELD else change
			for change in changes or []
		]

	if data.get("changed"):
		data["changed"] = mask_changes(data["changed"])
	if data.get("row_changed"):
		data["row_changed"] = [
			[table, idx, row_name, mask_changes(changes)]
			for table, idx, row_name, changes in data["row_changed"]
		]
	for key in ("added", "removed"):
		for _table, row in data.get(key) or []:
			if row.get(ID_FIELD):
				row[ID_FIELD] = mask(row[ID_FIELD])

	version.data = frappe.as_json(data, indent=None, separators=(",", ":"))


def grant_full_id_level():
	"""Give FULL_ID_READERS their level-1 rule on a site that keeps its own rules.

	visitor_pass.json ships the level-1 rules, and a site reads them — until the
	first change made through the Role Permission Manager or by setup._grant,
	which copies the shipped rules into Custom DocPerm; from then on only Custom
	DocPerm is read. A site whose copy was taken before the field moved to level
	1 has no level-1 rule in it, so nobody there could see a full number.

	Adds the missing rules to such a copy and nothing else: a rule that exists is
	left as the site set it, and a site with no copy is left reading the shipped
	rules. Safe to call on every migrate.
	"""
	if not frappe.db.exists("Custom DocPerm", {"parent": "Visitor Pass"}):
		return
	added = False
	for role in FULL_ID_READERS:
		if not frappe.db.exists("Role", role) or frappe.db.exists(
			"Custom DocPerm", {"parent": "Visitor Pass", "role": role, "permlevel": FULL_ID_PERMLEVEL}
		):
			continue
		frappe.get_doc(
			{
				"doctype": "Custom DocPerm",
				"parent": "Visitor Pass",
				"parenttype": "DocType",
				"parentfield": "permissions",
				"role": role,
				"permlevel": FULL_ID_PERMLEVEL,
				"read": 1,
				"mask": 1,
				# As shipped: an administrator's import or integration may set
				# the number directly; Security only reads it.
				"write": int(role == "System Manager"),
			}
		).insert(ignore_permissions=True)
		added = True
	if added:
		frappe.clear_cache(doctype="Visitor Pass")
