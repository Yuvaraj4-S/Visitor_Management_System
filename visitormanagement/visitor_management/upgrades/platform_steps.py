"""Platform upgrade steps: settings, permissions, the blacklist, form tours.

Run on install and on every migrate by `upgrades.run_all()`. Each step is
idempotent; the one-time ones record a `vms_...` marker (a Frappe default) and
never act again, so whatever an administrator changes afterwards stays.
"""

import json

import frappe
from frappe.permissions import setup_custom_perms
from frappe.utils import cint

from visitormanagement.visitor_management.upgrades import is_fresh_install

SETTINGS = "VMS Settings"

# Settings added after 1.0.0 whose default is not "off / empty". A Single's
# default reaches a site only when the Single is first created, so an upgraded
# site would read these as 0 / blank.
NEW_SETTING_DEFAULTS = {
	"require_portal_consent": 1,
	"privacy_notice_version": "1",
}

# The three checks 1.0.0 applied to every Check-In and Check-Out whatever the
# settings said. From 1.1.0 the Security Log follows these switches, which
# default to off.
GATE_CHECK_SWITCHES = (
	"qr_scan_required_at_gate",
	"require_visitor_photo",
	"block_check_in_without_verification",
)

_GATE_CHECKS_MARKER = "vms_gate_checks_carried_over"
_WALK_IN_MARKER = "vms_walk_in_default_carried_over"
_BLACKLIST_INACTIVE_MARKER = "vms_blacklist_inactive_reported"
_PASS_EXPORT_MARKER = "vms_pass_export_restricted"
_BLACKLIST_VERSION_SCRUB_MARKER = "vms_blacklist_version_id_scrub_done"
_UI_TOURS_MARKER = "vms_ui_tours_removed"

# The only role that keeps `export` on Visitor Pass.
PASS_EXPORT_ROLE = "System Manager"

# This app's modules (their Form Tours are the only ones these steps look at).
APP_MODULES = ("Visitor Management", "Conference Room")

# The Form Tours this app ships. They are ordinary form tours, started from the
# "Set up Visitor Management" onboarding block and from nowhere else.
SHIPPED_FORM_TOURS = ("Visitor Type Form Tour", "Visitor Invitation Form Tour")

# Shipped by an earlier build and no longer part of the app: a tour of the
# workspace, which Frappe can only run as a "UI tour" (see _retire_ui_tours).
REMOVED_FORM_TOURS = ("VMS Setup Tour",)


def run():
	_seed_new_setting_defaults()
	_carry_gate_checks_over()
	_carry_walk_in_over()
	_report_inactive_blacklist_entries()
	_restrict_visitor_pass_export()
	_mask_blacklist_id_numbers()
	_retire_ui_tours()


# ─────────────────────────────────────────────────────────
# VMS Settings
# ─────────────────────────────────────────────────────────
def _has_setting(fieldname):
	return bool(frappe.get_meta(SETTINGS).has_field(fieldname))


def _stored_setting(fieldname):
	"""The value stored for a setting, or None when the site never stored one."""
	# `tabSingles` has no `modified` column, which get_value sorts by unless told not to.
	return frappe.db.get_value("Singles", {"doctype": SETTINGS, "field": fieldname}, "value", order_by=None)


def _set_setting(fieldname, value):
	frappe.db.set_single_value(SETTINGS, fieldname, value, update_modified=False)
	# settings.py reads the cached document.
	frappe.clear_document_cache(SETTINGS, SETTINGS)


def _seed_new_setting_defaults():
	"""Give an existing site the defaults of settings it has never stored.

	Only a setting with no stored value is written, so a value an administrator
	chose — including an unticked box, which is stored as 0 — is never changed.
	"""
	seeded = []
	for fieldname, value in NEW_SETTING_DEFAULTS.items():
		if not _has_setting(fieldname) or _stored_setting(fieldname) is not None:
			continue
		_set_setting(fieldname, value)
		seeded.append(fieldname)
	if seeded:
		print(f"  set the default for new VMS Settings: {', '.join(seeded)}")


def _carry_gate_checks_over():
	"""Keep the gate as strict as 1.0.0 had it, on a site upgraded from 1.0.0. Once.

	1.0.0 refused every Check-In and Check-Out without a scanned QR code, a gate
	photo and both identity confirmations. From 1.1.0 those are three switches
	in VMS Settings that default to off, so an upgrade would have relaxed the
	gate without anyone deciding to. They are switched on here for an upgraded
	site; a new installation keeps the defaults and chooses for itself.

	Decided once, the first time this step runs on a site, and recorded: after
	that the switches belong to the administrator, who may turn any of them off.
	"""
	if frappe.db.get_default(_GATE_CHECKS_MARKER):
		return
	if is_fresh_install():
		frappe.db.set_default(_GATE_CHECKS_MARKER, "fresh install")
		return

	turned_on = []
	for fieldname in GATE_CHECK_SWITCHES:
		if not _has_setting(fieldname) or cint(_stored_setting(fieldname)):
			continue
		_set_setting(fieldname, 1)
		turned_on.append(frappe.get_meta(SETTINGS).get_label(fieldname))
	if turned_on:
		print(
			"  upgrade from 1.0.0: switched on the gate checks 1.0.0 always applied: "
			f"{', '.join(turned_on)}. Review them in VMS Settings > Gate & Security."
		)
	frappe.db.set_default(_GATE_CHECKS_MARKER, "upgraded")


def _carry_walk_in_over():
	"""Keep the public form open without an invitation on a site upgraded from 1.0.0. Once.

	In 1.0.0 anyone could open the pre-registration form. The new switch
	"Allow Pre-Registration Without an Invitation" defaults to off, which would
	silently close a form a site may have printed on a QR code at reception.
	A new installation keeps the default.
	"""
	fieldname = "allow_walk_in_pre_registration"
	if frappe.db.get_default(_WALK_IN_MARKER) or not _has_setting(fieldname):
		return
	if is_fresh_install():
		frappe.db.set_default(_WALK_IN_MARKER, "fresh install")
		return
	if not cint(_stored_setting(fieldname)):
		_set_setting(fieldname, 1)
		print(
			"  upgrade from 1.0.0: kept the public pre-registration form open without an "
			"invitation (VMS Settings > Allow Pre-Registration Without an Invitation). "
			"Untick it to accept invited visitors only."
		)
	frappe.db.set_default(_WALK_IN_MARKER, "upgraded")


# ─────────────────────────────────────────────────────────
# Visitor Blacklist
# ─────────────────────────────────────────────────────────
def _report_inactive_blacklist_entries():
	"""Say how many blacklist entries are inactive. Never activate one.

	Earlier builds switched every inactive entry on at upgrade, because 1.0.0
	created entries inactive by default and an inactive entry stops nobody. But
	unticking "Is Active" is also how a site clears a person after review, and
	the two cannot be told apart, so nothing is changed: the count is printed
	once and the administrator decides.
	"""
	if frappe.db.get_default(_BLACKLIST_INACTIVE_MARKER):
		return
	if not frappe.db.table_exists("Visitor Blacklist"):
		return
	inactive = frappe.db.count("Visitor Blacklist", {"is_active": 0})
	if inactive:
		print(
			f"  NOTE: {inactive} Visitor Blacklist entr{'y is' if inactive == 1 else 'ies are'} inactive "
			"and will not stop anyone at the gate. In 1.0.0 a new entry was inactive unless "
			'"Is Active" was ticked. Nothing was changed: open Visitor Blacklist, filter on '
			"Is Active = No, and tick the entries that should be in force."
		)
	frappe.db.set_default(_BLACKLIST_INACTIVE_MARKER, "1")


def _mask_blacklist_id_numbers():
	"""Fill the masked ID number on existing blacklist entries, and mask their history.

	The masked field is refreshed on every run (only rows that differ are
	written), which also picks up a changed "Visible Trailing Characters" on an
	ID Proof Type. The history is cleaned once: Version rows written before
	the number was hidden hold it in full, and the form loads them as they are.
	"""
	from visitormanagement.visitor_management.id_masking import backfill_masked, scrub_versions

	masked = backfill_masked("Visitor Blacklist")
	if masked:
		print(f"  masked the ID number on {masked} Visitor Blacklist entr{'y' if masked == 1 else 'ies'}")

	if frappe.db.get_default(_BLACKLIST_VERSION_SCRUB_MARKER):
		return
	scrubbed = scrub_versions("Visitor Blacklist", ("id_proof_number",))
	scrubbed += _mask_deleted_blacklist_copies()
	if scrubbed:
		print(f"  masked full ID numbers in {scrubbed} Visitor Blacklist history row(s)")
	frappe.db.set_default(_BLACKLIST_VERSION_SCRUB_MARKER, "1")


def _mask_deleted_blacklist_copies():
	"""Take the full ID number out of the copies Frappe kept of deleted blacklist entries.

	Deleting a document leaves its JSON in Deleted Document, which a System
	Manager can open — the full number, with no record of it being viewed. An
	entry deleted from now on is stored without the number
	(VisitorBlacklist.as_dict); this brings the older copies in line: the number
	is removed and its masked form put in its place. Restoring such a copy
	brings the entry back without its ID number, which then has to be typed in
	again.
	"""
	import json

	from visitormanagement.visitor_management.id_masking import mask_id

	changed = 0
	for row in frappe.get_all(
		"Deleted Document",
		filters={"deleted_doctype": "Visitor Blacklist", "data": ("like", '%"id_proof_number"%')},
		fields=["name", "data"],
	):
		try:
			data = json.loads(row.data)
		except (ValueError, TypeError):
			continue
		if not isinstance(data, dict) or "id_proof_number" not in data:
			continue
		number = data.pop("id_proof_number")
		data.pop("id_proof_number_entry", None)
		data["id_proof_number_masked"] = mask_id(data.get("id_proof_type"), number) or None
		frappe.db.set_value("Deleted Document", row.name, "data", frappe.as_json(data), update_modified=False)
		changed += 1
	return changed


# ─────────────────────────────────────────────────────────
# Permissions (this app's own DocTypes only)
# ─────────────────────────────────────────────────────────
def _restrict_visitor_pass_export():
	"""Leave `export` on Visitor Pass with System Manager only. Once.

	1.0.0 shipped export for nine roles, so a host or a guard could download
	every visitor's record as a spreadsheet. The DocType now ships it for System
	Manager alone, but a site's stored permissions (Custom DocPerm, which
	Visitor Pass always has: setup._grant copies them) outrank what the DocType
	ships, so the right is taken away here.

	One-time and recorded, unlike setup._revoke: an administrator who grants
	export back to a role through Role Permission Manager keeps that decision.
	"""
	if frappe.db.get_default(_PASS_EXPORT_MARKER):
		return
	doctype = "Visitor Pass"
	if not frappe.db.exists("DocType", doctype):
		return

	# The site's own copy of the DocType's permissions, if it has none yet.
	setup_custom_perms(doctype)
	revoked = set()
	for row in frappe.get_all(
		"Custom DocPerm",
		filters={"parent": doctype, "export": 1, "role": ("!=", PASS_EXPORT_ROLE)},
		fields=["name", "role"],
	):
		frappe.db.set_value("Custom DocPerm", row.name, "export", 0)
		revoked.add(row.role)
	if revoked:
		frappe.clear_cache(doctype=doctype)
		print(
			f"  Visitor Pass: export is now for {PASS_EXPORT_ROLE} only; removed it from "
			f"{', '.join(sorted(revoked))}"
		)
	frappe.db.set_default(_PASS_EXPORT_MARKER, "1")


# ─────────────────────────────────────────────────────────
# Form Tours
# ─────────────────────────────────────────────────────────
def _retire_ui_tours():
	"""Make sure none of this app's Form Tours is a "UI tour".

	Frappe 15 puts EVERY Form Tour with `ui_tour = 1` into the boot data of EVERY
	desk user (frappe/desk/doctype/form_tour/form_tour.py get_onboarding_ui_tours:
	no role, module or route filter). While a user has not completed all of them
	— and somebody who never opens Visitor Management never does — the desk
	loads the onboarding-tour script on every page and runs it on every route
	change (frappe/public/js/frappe/desk.js, onboarding_tours.js). On a slow page
	load that script reads the route before the router has one and throws, on
	whichever page the user is looking at: Sales Invoice, Employee, another
	app's DocType. No other installed app ships such a tour, so the three an
	earlier build of this app shipped were the reason that code ran at all.

	The two form tours are ordinary Form Tours now (their files say `ui_tour` 0,
	and migrate re-imports them); they are started from the "Set up Visitor
	Management" onboarding block and nowhere else. This step covers what the
	import cannot:

	* a shipped tour that was edited on the site keeps its own, newer copy
	  (frappe/modules/import_file.py skips a record whose `modified` is not older
	  than the file's), so `ui_tour` is switched off on it directly. Every run:
	  it changes nothing once it is off;
	* "VMS Setup Tour", a tour of the workspace, can only exist as a UI tour.
	  It has no file any more, and migrate never deletes the record of a file
	  that is gone. Deleted here, once;
	* what users had recorded as seen or completed for these tours
	  (User.onboarding_status, one key per tour name). Removed once: the keys
	  mean nothing for a tour that is gone or is no longer a UI tour.

	Only records of this app's own modules, by the names it shipped them under.
	"""
	if not frappe.db.table_exists("Form Tour"):
		return

	switched_off = frappe.get_all(
		"Form Tour",
		filters={"name": ("in", SHIPPED_FORM_TOURS), "module": ("in", APP_MODULES), "ui_tour": 1},
		pluck="name",
	)
	for name in switched_off:
		frappe.db.set_value(
			"Form Tour",
			name,
			{"ui_tour": 0, "page_route": None, "view_name": None, "new_document_form": 0},
			update_modified=False,
		)
		for step in frappe.get_all(
			"Form Tour Step", filters={"parent": name, "parenttype": "Form Tour", "ui_tour": 1}, pluck="name"
		):
			frappe.db.set_value("Form Tour Step", step, "ui_tour", 0, update_modified=False)
	if switched_off:
		# What Form Tour's own on_update does: every user's boot data lists the UI tours.
		frappe.cache.delete_key("bootinfo")
		print(f"  Form Tour: no longer shown as a site-wide UI tour: {', '.join(sorted(switched_off))}")

	if frappe.db.get_default(_UI_TOURS_MARKER):
		return

	removed = []
	for name in REMOVED_FORM_TOURS:
		if frappe.db.get_value("Form Tour", name, "module") not in APP_MODULES:
			continue  # not there, or not this app's
		frappe.delete_doc("Form Tour", name, force=True, ignore_permissions=True, delete_permanently=True)
		removed.append(name)
	if removed:
		print(
			f"  Form Tour: removed {', '.join(removed)} (it ran Frappe's tour code on every page for every "
			'user). The "Set up Visitor Management" onboarding block covers the same steps.'
		)

	forgotten = _forget_tour_status((*REMOVED_FORM_TOURS, *SHIPPED_FORM_TOURS))
	if forgotten:
		print(f"  Form Tour: cleared the UI-tour progress {forgotten} user(s) had recorded for these tours")
	frappe.db.set_default(_UI_TOURS_MARKER, "1")


def _forget_tour_status(tour_names) -> int:
	"""Drop these tours' entries from every user's UI-tour progress. Returns the users changed.

	The same field, written the same way, as Frappe's own "Reset" on a Form Tour
	(form_tour.reset_tour): User.onboarding_status is a JSON object keyed by tour
	name. Every other key in it is left as it is, and `modified` is not touched.
	"""
	changed = 0
	for user in frappe.get_all(
		"User",
		or_filters=[["onboarding_status", "like", f"%{name}%"] for name in tour_names],
		fields=["name", "onboarding_status"],
	):
		try:
			status = json.loads(user.onboarding_status or "{}")
		except ValueError:
			continue
		if not isinstance(status, dict):
			continue
		kept = {key: value for key, value in status.items() if key not in tour_names}
		if len(kept) == len(status):
			continue
		frappe.db.set_value("User", user.name, "onboarding_status", json.dumps(kept), update_modified=False)
		frappe.cache.hdel("bootinfo", user.name)
		changed += 1
	return changed
