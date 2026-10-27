"""A visitor's ID proof number: kept whole in the database, shown masked.

`id_proof_number` holds the real Aadhaar / PAN / passport number, unchanged —
the blacklist, the duplicate-pass check, the returning-visitor lookup and the
gate all compare it, reading it with get_doc or a raw query. Nobody is shown it
by default, Security and System Manager included:

- It sits at permission level 1 (FULL_ID_PERMLEVEL) and no role holds a
  level-1 rule, on Visitor Pass or on Visitor Blacklist. Frappe drops a field
  outside the user's levels from every read that reaches a browser — the form,
  REST, list and report views, exports — and refuses to filter, sort or group
  by it. Masking alone hid the value but still let any reader search on it, so
  a run of "starts with" filters rebuilt the number one character at a time.
- It is a masked field (DocField.mask) and hidden on the form, so a role later
  given level-1 read still sees XXXXXXXX, and Administrator, whom Frappe never
  restricts, does not see it on screen either.

Security and System Manager (REVEAL_ROLES) can read one number back when they
need it, with the "Reveal Full ID" button (reveal_id). Every reveal is recorded,
who and when but never the number, in Activity Log and on the record's timeline
(log_reveal).

The change history is kept masked as well (mask_version, save_masked_version):
Frappe's Version stores the old and new value of every field as plain text.

A user without level-1 write cannot set the field, so people do not type into
it. They type into `id_proof_masked` ("ID Proof Number"), which after
every save shows only the last four characters, e.g. `XXXXXX234F`. Saving with
a new full number there replaces the real one; leaving the masked value as it
is keeps the real one.

Custom endpoints that return the number to the browser must pass it through
`shown_id`; Frappe's masking cannot reach a raw SQL result or a hand-built dict.
The same applies to accompanying visitors (Visitor Group Member) and to
Visitor Blacklist entries.
"""

import re

import frappe
from frappe import _
from frappe.utils import escape_html, format_datetime, get_fullname, now_datetime, strip_html

from visitormanagement.visitor_management.validators import is_masked_id, mask_id_number

ID_FIELD = "id_proof_number"
# The same number upper-cased and without separators, kept beside it on Visitor
# Pass and Visitor Group Member for the duplicate-pass check (VisitorPass._set_id_proof_keys).
# It is the number, so it is kept and masked exactly like it.
ID_KEY_FIELD = "id_proof_key"
ID_FIELDS = (ID_FIELD, ID_KEY_FIELD)
# Visitor Pass, Visitor Group Member and Visitor Blacklist keep the field at this
# level, and no role is given a rule on it.
FULL_ID_PERMLEVEL = 1
# Who may read one number back through reveal_id: the guard checking the card at
# the gate, and the administrator.
REVEAL_ROLES = ("Security", "System Manager")
# The records reveal_id answers for. A group member is reached through its pass.
REVEAL_DOCTYPES = ("Visitor Pass", "Visitor Blacklist")
# Reveals per user per rolling hour (portal_upload._count_or_throw's window).
REVEAL_LIMIT_PER_HOUR = 30
# `subject` of the timeline note log_reveal leaves, to tell it from other Info notes.
REVEAL_COMMENT_SUBJECT = "VMS full ID number revealed"
# Longest reason kept with a reveal.
REVEAL_REASON_LENGTH = 140


def shown_id(real):
	"""What a custom endpoint may return for an ID number: always the masked form.

	Nobody is shown a full number by default; it is read back only through
	reveal_id, which records it.
	"""
	if not real:
		return real
	return mask_id_number(real)


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
	members' are all `id_proof_number`, and their match keys `id_proof_key`.
	"""
	data = frappe.parse_json(version.data or "{}")

	def mask(value):
		return mask_id_number(value) if isinstance(value, str) and value else value

	def mask_changes(changes):
		return [
			[change[0], mask(change[1]), mask(change[2])] if change[0] in ID_FIELDS else change
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
			for fieldname in ID_FIELDS:
				if row.get(fieldname):
					row[fieldname] = mask(row[fieldname])

	version.data = frappe.as_json(data, indent=None, separators=(",", ":"))


def save_masked_version(doc, drop_fields=()):
	"""Document.save_version, with every ID number in the diff masked.

	For a DocType that keeps `id_proof_number` (Visitor Pass, Visitor Blacklist):
	call it from the controller's own save_version. The steps are Frappe's own;
	mask_version() is the one addition, between building the diff and storing it.
	`drop_fields` are left out of the history altogether.
	"""
	doc_before_save = doc.get_doc_before_save()
	if (
		not getattr(doc.meta, "track_changes", False)
		or doc.flags.ignore_version
		or frappe.flags.in_install
		or (not doc_before_save and frappe.flags.in_patch)
	):
		return

	doc_to_compare = doc_before_save
	if not doc_to_compare and (amended_from := doc.get("amended_from")):
		doc_to_compare = frappe.get_doc(doc.doctype, amended_from)
	if not doc_to_compare and not doc.flags.updater_reference:
		return

	version = frappe.new_doc("Version")
	if version.update_version_info(doc_to_compare, doc):
		mask_version(version)
		if drop_fields:
			data = frappe.parse_json(version.data)
			data["changed"] = [change for change in data.get("changed") or [] if change[0] not in drop_fields]
			version.data = frappe.as_json(data, indent=None, separators=(",", ":"))
		version.insert(ignore_permissions=True)


def scrub_versions(doctype):
	"""Mask the ID numbers already stored in `doctype`'s change history.

	For records whose history was kept before save_masked_version covered them.
	Rewrites only rows that mention the field, and only when masking changes
	them; a masked value masks to itself, so a second run changes nothing.
	Returns how many Version rows were rewritten.
	"""
	scrubbed = 0
	for row in frappe.get_all(
		"Version",
		filters={"ref_doctype": doctype, "data": ("like", f'%"{ID_FIELD}"%')},
		fields=["name", "data"],
		order_by=None,
	):
		try:
			before = frappe.parse_json(row.data)
		except ValueError:
			continue  # not JSON; leave it alone
		if not isinstance(before, dict):
			continue
		version = frappe._dict(data=row.data)
		mask_version(version)
		if frappe.parse_json(version.data) != before:
			frappe.db.set_value("Version", row.name, "data", version.data, update_modified=False)
			scrubbed += 1
	return scrubbed


# ─────────────────────────────────────────────────────────────
# REVEAL — the one way to read a full number back, recorded
# ─────────────────────────────────────────────────────────────


def can_reveal_full_id(user=None):
	"""Whether `user` (default: the current user) holds a role that may reveal."""
	user = user or frappe.session.user
	if not user or user == "Guest":
		return False
	return bool(set(REVEAL_ROLES) & set(frappe.get_roles(user)))


@frappe.whitelist(methods=["POST"])
def reveal_id(doctype: str, name: str, row: str | None = None, reason: str | None = None) -> str:
	"""The full ID number on one record, for Security or System Manager, recorded each time.

	`doctype`/`name` is a Visitor Pass or a Visitor Blacklist entry; `row` picks
	an accompanying visitor (Visitor Group Member) on a pass. The caller must
	hold one of REVEAL_ROLES and be able to read the record. Who revealed it and
	when is written down before the number is returned (log_reveal), in the
	same transaction: if the record cannot be written, nothing is returned.
	`reason` is optional, so a guard with a queue is not slowed down.
	"""
	if doctype not in REVEAL_DOCTYPES:
		frappe.throw(
			_("A full ID number can only be revealed on a Visitor Pass or a Visitor Blacklist entry."),
			frappe.PermissionError,
		)
	if not can_reveal_full_id():
		frappe.throw(
			_("Only Security and System Manager can reveal a full ID number."),
			frappe.PermissionError,
		)

	from visitormanagement.visitor_management.portal_upload import _count_or_throw

	# Counted before the record is looked up, so trying many names counts too.
	_count_or_throw(
		f"vms:reveal-id:{frappe.session.user}",
		REVEAL_LIMIT_PER_HOUR,
		_("You have revealed too many ID numbers in the last hour. Please wait a while and try again."),
	)

	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")

	member = None
	if row:
		member = next((m for m in doc.get("group_members") or [] if m.name == row), None)
		if not member:
			frappe.throw(
				_("That accompanying visitor is not on {0}.").format(doc.name),
				frappe.DoesNotExistError,
			)

	# get_doc reads the stored value; Frappe's masking applies only to what is sent out.
	number = ((member or doc).get(ID_FIELD) or "").strip()
	if not number:
		frappe.throw(_("No ID number is on record for this person."), title=_("No ID Number"))

	log_reveal(doc, member=member, reason=_clean_reason(reason, number))
	return number


def _clean_reason(reason, number):
	"""The reason as it may be kept: plain text, short, and never holding the number."""
	reason = re.sub(r"\s+", " ", strip_html(reason or "")).strip()[:REVEAL_REASON_LENGTH]
	if not reason:
		return ""

	def key(value):
		return re.sub(r"[^0-9A-Z]", "", value.upper())

	# The reason box is free text: a number typed or pasted into it would be
	# written into the log the reveal exists to keep it out of.
	if key(number) and key(number) in key(reason):
		return _("(reason not kept: it contained the ID number)")
	return reason


def log_reveal(doc, member=None, reason="", user=None):
	"""Record that `user` revealed a full ID number on `doc`: who, when, and whose.

	Written twice, and neither holds the number:

	- an Activity Log row. System Manager can read Activity Log but has no write
	  or delete right on it, so the person who looked cannot remove the record of
	  having looked. Frappe clears Activity Log after the period set in Log
	  Settings;
	- an Info comment on the record's timeline, which stays as long as the record
	  and is seen by everyone who can open it.
	"""
	user = user or frappe.session.user
	who = get_fullname(user)
	when = format_datetime(now_datetime())
	if member:
		text = _("{0} revealed the full ID number of accompanying visitor {1} (row {2}) at {3}").format(
			who, member.visitor_name or "", member.idx, when
		)
	else:
		text = _("{0} revealed the full ID number at {1}").format(who, when)
	if reason:
		text = _("{0}. Reason: {1}").format(text, reason)

	frappe.get_doc(
		{
			"doctype": "Activity Log",
			"subject": _("Full ID number revealed: {0} {1}").format(_(doc.doctype), doc.name),
			"content": escape_html(f"{text} ({user})"),
			"reference_doctype": doc.doctype,
			"reference_name": doc.name,
			"user": user,
			"ip_address": getattr(frappe.local, "request_ip", None),
		}
	).insert(ignore_permissions=True)

	frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Info",
			"subject": REVEAL_COMMENT_SUBJECT,
			"reference_doctype": doc.doctype,
			"reference_name": doc.name,
			"comment_email": user,
			"comment_by": who,
			"content": escape_html(text),
		}
	).insert(ignore_permissions=True)
