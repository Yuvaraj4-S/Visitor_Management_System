"""Idempotent bootstrap for Visitor Management — replaces the patch chain.

Why this exists instead of patches
----------------------------------
The app's setup used to live in ~30 `patches.txt` entries. That was the wrong
mechanism twice over:

* `bench install-app` marks every patch complete **without running it**, so a
  fresh site got none of the master data and nothing worked.
* Patches run once, ever. Configuration that should be reasserted on every
  deploy (roles, permissions, notification wiring, the generated workflow)
  silently drifted.

Everything here is idempotent and runs on **both** `after_install` and
`after_migrate`. Re-running only fills in what is missing, so an admin's own
tuning is never overwritten.

`before_migrate` handles the one thing that must happen *before* the DocType
JSON is applied: removing legacy Custom Fields that would otherwise collide
with fields now declared on the DocType itself.
"""

import frappe
from frappe.permissions import add_permission, update_permission_property

from visitormanagement.visitor_management.email_theme import repaint_email_html

# ─────────────────────────────────────────────────────────
# Reference data
# ─────────────────────────────────────────────────────────
ROLES = [
	"Security",
	"Host Employee",
	"Hospitality Manager",
	"Hospitality User",
	"Facility Manager",
	"Transport Coordinator",
	"Front Office Executive",
	"Factory Tour Coordinator",
	"Greeting Staff",
	"HOD",
	"CEO",
]

# Roles that pick an Employee in a VMS link field (host, guard, tour guide, driver).
# SELECT only: READ is the whole HR record — bank account, salary, PAN, date of
# birth, health — and these roles need a name in a dropdown. The few host fields a
# pass copies come from visitor_management/link_details.py instead.
EMPLOYEE_READERS = [
	"Security",
	"Hospitality Manager",
	"Host Employee",
	"Facility Manager",
	"HOD",
	"HR Manager",
	"Sales Manager",
	"CEO",
]

# Roles that fill the cab-vendor / hotel-name Links on Hospitality Request and so
# need to be able to SELECT an existing Supplier. SELECT only — see _ensure_permissions.
SUPPLIER_READERS = ["Hospitality Manager", "Hospitality User", "Facility Manager"]

# Link widgets on Conference Room Booking / Hospitality Request show the
# visitor's title, so these roles need read on Visitor Pass.
VISITOR_PASS_READERS = ["Facility Manager", "Hospitality Manager"]

# The Hospitality Request workflow offers Approved --Cancel--> Cancelled to the
# Hospitality Manager, but no DocPerm row on that doctype grants `cancel` to
# anyone. So the action appeared in the Actions menu and threw "No permission
# for Hospitality Request" every time it was clicked — a button that could never
# work. Conference Room Booking gets this right: Facility Manager holds cancel
# and amend to match its own Cancel transition. Amend comes along because a
# cancelled request that can never be amended is a dead end.
HOSPITALITY_CANCELLERS = ["Hospitality Manager", "System Manager"]

# The same trap, one doctype over. The Visitor Pass workflow now offers
# Approved --Cancel--> Cancelled, but no DocPerm row granted `cancel` on Visitor
# Pass to anyone, so the action would appear in the Actions menu and then be
# refused at `check_permission("cancel")` — a button that can never work, which
# is exactly the defect HOSPITALITY_CANCELLERS above exists to fix.
#
# Granted to the roles that approve a pass, since cancelling one is the same
# authority exercised in reverse. Deliberately NOT Security: Security is
# read-only on Visitor Pass by design (the gate acts through Security Log), and
# handing it `write` purely to enable a cancel would undo that boundary.
# Roles that may cancel an Approved pass, in ADDITION to whichever roles the
# Visitor Types currently name as approvers. Those are read at runtime rather
# than listed here: `workflow_builder` generates one Approved --Cancel-->
# Cancelled transition per approver role, so a literal list here goes stale the
# moment an admin points a Visitor Type at a new role — and the result is a
# Cancel button that appears and is then refused at check_permission("cancel"),
# exactly the defect HOSPITALITY_CANCELLERS above exists to prevent.
VISITOR_PASS_CANCEL_ADMINS = ["System Manager"]

# `visitor_management.settings.DEFAULT_DIGEST_ROLES` emails these roles the
# 07:00 hospitality digest, but the roles held no permission on Hospitality
# Request (or anything else in the module) — a fresh install would mail them a
# summary of records that refuse them on click. Hospitality Manager and Front
# Office Executive are left out here: Hospitality Manager already gets
# read/write on Hospitality Request above, and Front Office Executive's gap
# is a separate, unreported finding, not part of this fix.
HOSPITALITY_DIGEST_READERS = [
	"Transport Coordinator",
	"Factory Tour Coordinator",
	"Greeting Staff",
	"Hospitality User",
]

# Layout-specific link fields on Visitor Pass point at masters owned by other
# apps, and none of the roles that actually raise a pass could select from them:
# `contractor_link`/`supplier_link` -> Supplier, `work_order_ref` ->
# Maintenance Visit, `job_applicant_link` -> Job Applicant. The dropdown threw
# "Insufficient Permission", so linking a pass to an existing record was dead on
# the Contractor, Supplier and Candidate layouts.
#
# `select` rather than `read` on purpose: it makes the doctype pickable in a
# link field without granting the list. add_permission copies the existing
# standard permissions into Custom DocPerm first, so the owning app's own roles
# keep their access.
# System Manager is included deliberately: it is the configured approver_role for
# the Supplier and Contractor visitor types, so it opens those passes routinely —
# and it held no read on Supplier or Maintenance Visit either, because those are
# gated to Purchase/Accounts/Stock and Maintenance roles respectively.
LINK_TARGET_PICKERS = {
	"Supplier": ["Host Employee", "Front Office Executive", "System Manager"],
	"Maintenance Visit": ["Host Employee", "Front Office Executive", "System Manager"],
	"Job Applicant": ["Host Employee", "Front Office Executive", "System Manager"],
}

VISITOR_TYPES = [
	{
		"visitor_type_name": "Contractor",
		"approver_role": "System Manager",
		"badge_prefix": "CON",
		"badge_colour": "Orange",
		"default_gate": "Back Gate",
		"detail_layout": "Contractor",
	},
	{
		"visitor_type_name": "Candidate",
		"approver_role": "HR Manager",
		"badge_prefix": "CAN",
		"badge_colour": "Purple",
		"default_gate": "Main Gate",
		"detail_layout": "Candidate",
	},
	{
		"visitor_type_name": "Customer",
		"approver_role": "Sales Manager",
		"badge_prefix": "CUS",
		"badge_colour": "Green",
		"default_gate": "Main Gate",
		"detail_layout": "Customer",
	},
	{
		"visitor_type_name": "Supplier",
		"approver_role": "System Manager",
		"badge_prefix": "SUP",
		"badge_colour": "Teal",
		"default_gate": "Loading Dock",
		"detail_layout": "Supplier",
	},
	{
		"visitor_type_name": "VIP",
		"approver_role": "HOD",
		"secondary_approver_role": "CEO",
		"badge_prefix": "VIP",
		"badge_colour": "Gold",
		"default_gate": "VIP Entrance",
		"detail_layout": "VIP",
		"requires_executive_notification": 1,
		"issue_badge_at_gate": 1,
	},
]

GATES = [
	{"gate_name": "Main Gate", "location": "Front of building"},
	{"gate_name": "Back Gate", "location": "Rear entrance"},
	{"gate_name": "VIP Entrance", "location": "Executive lobby"},
	{"gate_name": "Loading Dock", "location": "Goods entrance"},
	{"gate_name": "Emergency Exit", "location": "Fire exit"},
]

ID_PROOF_TYPES = [
	{
		"id_proof_type_name": "Aadhaar",
		"aliases": "aadhar\nuid",
		"validation_method": "Aadhaar (Verhoeff)",
		"normalisation": "Strip spaces and hyphens",
		"error_message": "Aadhaar must be exactly 12 digits, must not start with 0 or 1, "
		"and must pass the UIDAI Verhoeff checksum.",
	},
	{
		"id_proof_type_name": "PAN Card",
		"aliases": "pan",
		"validation_method": "Regex",
		"validation_regex": "^[A-Z]{5}[0-9]{4}[A-Z]$",
		"normalisation": "Uppercase and strip spaces",
		"error_message": "PAN must be in the format ABCDE1234F "
		"(5 uppercase letters, 4 digits, 1 uppercase letter).",
	},
	{
		"id_proof_type_name": "Passport",
		"validation_method": "Regex",
		"validation_regex": "^[A-Z][0-9]{7}$",
		"normalisation": "Uppercase and strip spaces",
		"error_message": "Passport must be in the format A1234567 (1 uppercase letter followed by 7 digits).",
	},
	{
		"id_proof_type_name": "Driving License",
		"aliases": "dl\ndriving licence",
		"validation_method": "Regex",
		"validation_regex": "^[A-Z]{2}[0-9]{2} ?[0-9]{11}$",
		"normalisation": "Collapse spaces",
		"error_message": "Driving License must be in the format SS00 00000000000 "
		"(2 letters + 2 digits + optional space + 11 digits).",
	},
	# The Indian passport pattern rejects most real foreign passport numbers, so
	# ship a permissive international type and mark it foreign-eligible.
	{
		"id_proof_type_name": "Foreign Passport",
		"aliases": "international passport",
		"validation_method": "Regex",
		"validation_regex": "^[A-Z0-9]{6,12}$",
		"normalisation": "Uppercase and strip spaces",
		"valid_for_foreign_nationals": 1,
		"error_message": "Foreign Passport must be 6–12 letters or digits.",  # noqa: RUF001
	},
]

MEAL_WINDOWS = [
	{"meal_label": "Breakfast", "start_time": "08:00:00", "end_time": "09:00:00"},
	{"meal_label": "Lunch", "start_time": "13:00:00", "end_time": "14:00:00"},
	{"meal_label": "Dinner", "start_time": "20:00:00", "end_time": "21:30:00"},
]

# Int settings read back as 0 when unset, and 0 means "no limit" for the
# booking ceiling — so an unseeded site would silently lose the guard. The
# same ambiguity applies to max_portal_submissions_per_hour (0 reads as
# unset, not as "block everything") and, in practice, fever_threshold_c —
# see settings.py's `_int`/`_float` helpers, which every accessor here goes
# through. `_seed_settings` below only fills a field that is still blank, so
# none of this overwrites a value an admin has already typed in.
POLICY_DEFAULTS = {
	"max_advance_booking_days": 90,
	"invitation_expiry_days": 7,
	"no_show_grace_hours": 4,
	"default_country_code": "91",
	"home_country": "India",
	"fever_threshold_c": 37.5,
	"max_portal_submissions_per_hour": 20,
	# Only the number, not the switch: `data_retention_enabled` defaults to 0 in
	# the DocType JSON itself and stays there — this only fills in a sane period
	# for whenever an administrator turns the purge on, exactly like every other
	# value in this dict never flips a boolean gate on its own.
	"data_retention_days": 365,
}

# Each of these duplicates an email the app already sends from code — the code
# versions carry attachments and per-type detail the Notification cannot build,
# so the Notification copy is the one that goes. Without this the visitor got
# two "Visit Approved" emails for the same pass. Disabled once at setup time,
# same as everything else `enabled=0` — after that, an admin who re-enables
# one through the Desk keeps it enabled; see `_configure_notifications`.
DISABLED_NOTIFICATIONS = ["VMS Host Alert", "VMS Food Dept Alert", "VMS Approval Email"]

# Records that setup has already had its one say about
# `allow_guests_to_upload_files`, so a later migrate cannot override an
# administrator who deliberately turned it off. Stored as a Frappe default
# rather than a field on VMS Settings because it is a fact about the install,
# not a setting anyone should see or edit. See `_allow_portal_uploads`.
_PORTAL_UPLOADS_MARKER = "vms_portal_uploads_configured"

# A DIFFERENT fact from the one above, and the one uninstall.py actually needs.
# `_PORTAL_UPLOADS_MARKER` only means "setup has considered this setting" — it
# is set whether setup found the flag already on or switched it on itself, so
# it cannot answer "did THIS app turn it on" (a site could have had it on
# already, for another app's reason, or an administrator's own). This marker
# is set in the one branch where setup actually performed the flip, which is
# the only case `uninstall.before_uninstall` may safely undo. A site that
# already had the old marker from a version before this one existed has no
# self-enabled marker to find — uninstall correctly treats that as "cannot
# prove it was us" and leaves the setting alone, per the same conservative
# rule (see uninstall.py).
_PORTAL_UPLOADS_SELF_ENABLED_MARKER = "vms_portal_uploads_self_enabled"

# Set when setup found `allow_guests_to_upload_files` already on: the site allowed
# guest uploads before this app arrived, so its guard must leave every page other
# than the visitor portal exactly as it was (portal_upload.guard_guest_upload).
_PORTAL_UPLOADS_PREEXISTING_MARKER = "vms_portal_uploads_preexisting"

# Same one-time-say pattern as `_PORTAL_UPLOADS_MARKER`, for the historical
# blacklist backfill. See `_activate_blacklist_entries`.
_BLACKLIST_BACKFILL_MARKER = "vms_blacklist_backfill_done"

# Same pattern again, one per notification: each of DISABLED_NOTIFICATIONS
# gets disabled at most once. See `_configure_notifications`.
_NOTIFICATION_DISABLED_MARKER = "vms_notification_disabled_once:{name}"

# Same pattern again, keyed per (doctype, role, ptype): each specific
# privilege is asserted at most once. See `_grant`.
_GRANT_MARKER = "vms_grant_once:{doctype}:{role}:{ptype}"

# Doctypes whose timelines carry alerts this app sends. Used to scope the
# repaint of already-sent messages so no other app's mail is touched.
VMS_ALERT_DOCTYPES = [
	"Visitor Pass",
	"Visitor Invitation",
	"Hospitality Request",
	"Conference Room Booking",
	"Security Log",
]

# Custom Fields promoted into their DocType JSON. A DocField and a Custom Field
# of the same name collide, so these must go before the schema is applied.
SUPERSEDED_CUSTOM_FIELDS = [
	("Visitor Invitation", "reference_job_applicant"),
	("Visitor Invitation", "visitor_full_name"),
	("Visitor Invitation", "visitor_mobile"),
	("VMS Settings", "home_country"),
	# Visitor Pass is this app's own DocType, so its fields belong in the DocType
	# JSON — customizations are for *other* apps' DocTypes. These two were only
	# ever Custom Fields, which meant a mandatory field (Nationality) was
	# invisible to anyone reading the DocType. Same fieldname, so the column and
	# its data carry over untouched.
	("Visitor Pass", "custom_nationality"),
	("Visitor Pass", "custom_visa_copy"),
	# Same reasoning, one step removed. Frappe's workflow engine auto-creates a
	# `workflow_state` Custom Field on any doctype a Workflow is built for, so
	# these two carried a field that appeared nowhere in their DocType JSON —
	# invisible to anyone reading the schema, and owned by a Workflow record
	# rather than by the app. Visitor Pass already declared its own; these now
	# match it. The fieldname is unchanged, so the column and its data carry over.
	("Hospitality Request", "workflow_state"),
	("Conference Room Booking", "workflow_state"),
]

# Custom Fields dropped outright rather than promoted: unused, no data.
OBSOLETE_CUSTOM_FIELDS = [
	("Visitor Pass", "custom_description_"),
	# Renamed to the `custom_` prefix Frappe reserves for fields an app adds to a
	# DocType it does not own — without it, an `interview_mode` shipped by HRMS
	# one day would collide with ours. Renamed while the columns were empty.
	("Job Applicant", "interview_checkin_time"),
	("Job Applicant", "interview_checkout_time"),
	("Job Applicant", "interview_host"),
	("Job Applicant", "interview_mode"),
	("Job Applicant", "interview_visit_date"),
	("Job Applicant", "vms_column_break"),
	("Job Applicant", "vms_section_break"),
]

# Fields converted to Link. A leftover `options` Property Setter holding the old
# Select list makes the control search a doctype that does not exist.
LINK_FIELDS = [
	("Visitor Pass", "id_proof_type"),
	("Visitor Pass", "visitor_type"),
	("Visitor Invitation", "visitor_type"),
	("Security Log", "id_proof_type_verified"),
	("Security Log", "gate_name"),
	("Visitor Blacklist", "id_proof_type"),
]


# ─────────────────────────────────────────────────────────
# before_migrate
# ─────────────────────────────────────────────────────────
def before_migrate():
	"""Clear anything that would block the DocType schema from applying."""
	if not frappe.db.table_exists("Custom Field"):
		return

	for doctype, fieldname in SUPERSEDED_CUSTOM_FIELDS + OBSOLETE_CUSTOM_FIELDS:
		name = frappe.db.get_value("Custom Field", {"dt": doctype, "fieldname": fieldname}, "name")
		if not name:
			continue
		# The column and its data stay; only the Custom Field definition goes, so
		# the DocField takes over the same column.
		frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
		print(f"  removed superseded Custom Field {doctype}-{fieldname}")
		frappe.clear_cache(doctype=doctype)

	# Property Setters this app once layered onto its own DocType. A `field_order`
	# Property Setter outranks the DocType's own, so any field added to
	# visitor_pass.json would silently fail to render while these exist.
	#
	# Only the ones this app is known to have created are removed. Deleting every
	# Property Setter on the doctype would also wipe whatever the site's own
	# admins have done in Customize Form — their work, destroyed on every
	# migrate, with no warning.
	for name in _stale_self_property_setters():
		frappe.delete_doc("Property Setter", name, ignore_permissions=True, force=True)
		print(f"  removed self-applied Property Setter {name}")
	frappe.clear_cache(doctype="Visitor Pass")


def _stale_self_property_setters():
	"""Names of the Property Setters this app shipped against its own DocType.

	Identified by (field, property) rather than by wiping the whole doc_type, and
	only when the DocType now declares the same thing itself — so an admin's own
	Customize Form tweak on an unrelated field is never touched.
	"""
	OURS = {
		("main", "field_order"),
		("visitor_type", "fieldtype"),
		("visitor_type", "options"),
		("badge_colour", "options"),
		("followup_date", "hidden"),
		("food_dept_staff_assigned", "hidden"),
		("food_status", "hidden"),
		("hospitality_overall_status", "hidden"),
		("meeting_minutes", "hidden"),
		("meeting_outcome", "hidden"),
		("products_discussed", "hidden"),
		("section_break_gqft", "hidden"),
		("service_time", "hidden"),
	}
	stale = []
	for row in frappe.get_all(
		"Property Setter",
		filters={"doc_type": "Visitor Pass"},
		fields=["name", "field_name", "property"],
	):
		key = (row.field_name or "main", row.property)
		if key in OURS:
			stale.append(row.name)
	return stale


# ─────────────────────────────────────────────────────────
# after_install / after_migrate
# ─────────────────────────────────────────────────────────
# Shared-name records this app may bring to a site. Frappe creates a role the
# moment a DocType's permissions name it (during sync, before after_install), and
# the workflow builder creates states and actions — so "did this app create it?"
# can only be answered by looking before install.
_PREEXISTING_MARKER = "vms_preexisting:{doctype}"
_CREATED_MARKER = "vms_created:{doctype}:{name}"


def _shared_records():
	from visitormanagement.visitor_management import workflow_builder as wb

	return {
		"Role": list(ROLES),
		"Workflow State": sorted(
			{*wb.STATE_STYLES, "Pending Approval", "Items Verified", "Checked-In", "Checked-Out"}
		),
		"Workflow Action Master": [
			wb.ACTION_SUBMIT,
			wb.ACTION_APPROVE,
			wb.ACTION_REJECT,
			wb.ACTION_REAPPLY,
			wb.ACTION_CANCEL,
		],
	}


def before_install():
	import json

	for doctype, names in _shared_records().items():
		existing = [n for n in names if frappe.db.exists(doctype, n)]
		frappe.db.set_default(_PREEXISTING_MARKER.format(doctype=doctype), json.dumps(existing))


def mark_created(doctype, name):
	"""Record that this app created a shared-name record (see uninstall)."""
	frappe.db.set_default(_CREATED_MARKER.format(doctype=doctype, name=name), "1")


def _mark_install_created_records():
	"""After a fresh install: everything in the list the site did not have is ours."""
	import json

	for doctype, names in _shared_records().items():
		raw = frappe.db.get_default(_PREEXISTING_MARKER.format(doctype=doctype))
		if raw is None:
			continue  # installed by a version without before_install: cannot tell
		preexisting = set(json.loads(raw))
		for name in names:
			if name not in preexisting and frappe.db.exists(doctype, name):
				mark_created(doctype, name)


def after_install():
	setup_visitor_management()
	_mark_install_created_records()


def after_migrate():
	setup_visitor_management()


def setup_visitor_management():
	"""Create/refresh everything the app needs to actually work."""
	_ensure_roles()
	_ensure_permissions()
	_add_performance_indexes()
	_seed_gates()
	_seed_visitor_types()
	_seed_id_proof_types()
	_seed_settings()
	_drop_stale_property_setters()
	_clear_stale_layout_fields()
	_rewrite_event_log_details()
	_narrow_core_link_grants()
	_allow_portal_uploads()
	_configure_notifications()
	_repaint_notification_history()
	_backfill_host_email()
	_backfill_host_name()
	_backfill_mobile_digits()
	_repair_pending_status_drift()
	_repair_cancelled_status()
	_make_gate_photos_private()
	_restore_status_knocked_back_by_badge()
	_activate_blacklist_entries()
	_migrate_item_category_vocabulary()
	_harden_invitation_token_collation()
	_seed_static_workflows()
	_build_workflow()
	# After the Visitor Types are seeded and the workflow is generated, so the
	# approver set these read is the same one the lanes were built from. Same three
	# steps, in the same order, as `Visitor Type.on_update` -> `_sync_approver_wiring`.
	_align_visitor_pass_submit()
	_sync_approval_notification_recipients()
	_grant_visitor_pass_cancel()
	# nosemgrep: frappe-manual-commit - DDL next (add_index) must not implicitly commit unrelated pending writes
	frappe.db.commit()


def _ensure_roles():
	for role in ROLES:
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(
				ignore_permissions=True
			)
			mark_created("Role", role)
			print(f"  created Role {role}")


def _grant(doctype, role, ptype="read"):
	"""Grant one privilege, and only that privilege — and only once per site.

	`add_permission` creates a Custom DocPerm from the Role Permission defaults,
	and `export` defaults to 1 — so a plain `_grant(dt, role)` intended as
	"let them read" silently also let them bulk-export the whole table. Read
	access to a master and the right to download it in full are different
	decisions; callers now have to ask for `export` explicitly.

	Used to re-assert `ptype = 1` unconditionally on every migrate, even when
	the Custom DocPerm row already existed. That silently undid an admin who
	tightened this exact privilege through the Role Permission Manager — the
	permission came back the next deploy with no log line explaining why. Each
	(doctype, role, ptype) triple is now granted at most once per site, the
	same marker pattern as `_allow_portal_uploads`, `_activate_blacklist_entries`
	and `_configure_notifications`: we assert it once, remember that we did,
	and the Role Permission Manager owns it from then on — including a later
	"Reset Permissions" on the doctype, which is as deliberate a site decision
	as unchecking one box.

	`_revoke`, below, is deliberately NOT changed: it is a security floor that
	must keep closing a privilege on every migrate, not a one-time grant.
	"""
	if not frappe.db.exists("Role", role):
		return
	marker = _GRANT_MARKER.format(doctype=doctype, role=role, ptype=ptype)
	if frappe.db.get_default(marker):
		return
	if ptype == "select" and frappe.db.get_value(
		"Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0}, "read"
	):
		# READ already includes picking: nothing to add to a row the owning app
		# (or the site) manages.
		frappe.db.set_default(marker, "1")
		return
	created = False
	if not frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0}):
		# With no ptype, add_permission creates the row with READ — so every
		# `_grant(dt, role, "select")` used to hand out full READ as well.
		add_permission(doctype, role, 0, ptype)
		created = True
	update_permission_property(doctype, role, 0, ptype, 1)
	if created:
		# A new Custom DocPerm row also arrives with the DocType's defaults (print,
		# email, report, share, and export) switched on. A new row grants what was
		# asked for and nothing else.
		from frappe.permissions import rights

		name = frappe.db.get_value(
			"Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0}, "name"
		)
		frappe.db.set_value("Custom DocPerm", name, {p: int(p == ptype) for p in rights})
		frappe.clear_cache(doctype=doctype)
	frappe.db.set_default(marker, "1")


_CORE_GRANTS_NARROWED_MARKER = "vms_core_link_grants_narrowed"


def _core_link_grants():
	"""{core doctype: roles this app grants on it} — only ever to pick a record."""
	grants = {"Employee": set(EMPLOYEE_READERS), "Supplier": set(SUPPLIER_READERS)}
	for doctype, roles in LINK_TARGET_PICKERS.items():
		grants.setdefault(doctype, set()).update(roles)
	return grants


def _narrow_core_link_grants():
	"""Take back the READ (and EXPORT) earlier versions granted on core records.

	`_grant` gave full READ where SELECT was meant — on Employee to eight roles,
	and on Supplier, Job Applicant and Maintenance Visit to hosts and reception —
	and before that EXPORT too. So a guard could open or download every
	employee's bank account, salary, PAN and date of birth, and a host every
	candidate's CV. These roles pick a record in a link field and nothing more.

	Only rows this app created are touched: a role the owning app (HRMS, ERPNext)
	grants itself has a standard DocPerm row, and that row's rights are theirs.
	Once per site, like every grant here — after that the Role Permission
	Manager owns these rows.
	"""
	if frappe.db.get_default(_CORE_GRANTS_NARROWED_MARKER):
		return
	from frappe.permissions import rights

	for doctype, roles in _core_link_grants().items():
		if not frappe.db.exists("DocType", doctype):
			continue
		standard_roles = set(frappe.get_all("DocPerm", filters={"parent": doctype}, pluck="role"))
		for role in sorted(roles - standard_roles):
			row = frappe.db.get_value(
				"Custom DocPerm",
				{"parent": doctype, "role": role, "permlevel": 0, "if_owner": 0},
				["name", *rights],
				as_dict=True,
			)
			wanted = {ptype: int(ptype == "select") for ptype in rights}
			if not row or all((row.get(p) or 0) == v for p, v in wanted.items()):
				continue
			frappe.db.set_value("Custom DocPerm", row.name, wanted)
			print(f"  narrowed {role} on {doctype} to select only")
	frappe.clear_cache()
	frappe.db.set_default(_CORE_GRANTS_NARROWED_MARKER, "1")


def _revoke(doctype, role, ptypes):
	"""Take a privilege away from a role, if it was ever granted."""
	if not frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0}):
		return
	for ptype in ptypes:
		if frappe.db.get_value("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0}, ptype):
			update_permission_property(doctype, role, 0, ptype, 0)
			print(f"  revoked {ptype} on {doctype} from {role}")


def _ensure_permissions():
	for role in EMPLOYEE_READERS:
		_grant("Employee", role, "select")
	# Hospitality Request's `cab_vendor` and `hotel_name` are Links to Supplier,
	# and the Hospitality Manager who fills that screen had no permission on
	# Supplier at all. The field accepted the typed value on screen and the save
	# reported success, but the server stripped both Links and stored NULL — so a
	# coordinator could never actually record which vendor was supplying the cab
	# or which hotel was booked, and nothing told them it had failed. Read only:
	# they need to pick an existing supplier, never to create, edit or export the
	# customer's supplier master.
	for role in SUPPLIER_READERS:
		_grant("Supplier", role, "select")
	for role in HOSPITALITY_CANCELLERS:
		_grant("Hospitality Request", role, "cancel")
		_grant("Hospitality Request", role, "amend")
	# _grant_visitor_pass_cancel() is deliberately NOT called here. It reads the
	# approver roles off the Visitor Type masters, and `_seed_visitor_types()` has
	# not run yet at this point in `setup_visitor_management()` — so on a FRESH
	# install that table is empty and the call granted `cancel` to nothing beyond
	# VISITOR_PASS_CANCEL_ADMINS. Every approver role then had a Cancel button that
	# was refused at check_permission("cancel") until somebody happened to run a
	# second migrate — exactly the defect VISITOR_PASS_CANCEL_ADMINS' own comment
	# says this app is trying to avoid. It now runs after `_build_workflow()`,
	# beside the other two approver-wiring steps that already wait for the same
	# masters.
	for doctype, roles in LINK_TARGET_PICKERS.items():
		for role in roles:
			_grant(doctype, role, "select")
	for role in VISITOR_PASS_READERS:
		_grant("Visitor Pass", role)
	for role in HOSPITALITY_DIGEST_READERS:
		_grant("Hospitality Request", role)
	_restore_core_page_permissions()

	# Visitor Type decides which role approves which visitor — it is access
	# control expressed as data. Earlier versions granted Employee write/create,
	# which let any staff member point approver_role at a role they hold and
	# approve their own visitors (and, as a side effect, strip the real approver
	# of their lane). The doctype now ships read-only for Employee; this revokes
	# the privilege on sites that already installed the permissive version,
	# because a stored Custom DocPerm overrides what the doctype ships.
	_revoke("Visitor Type", "Employee", ("write", "create", "delete", "submit", "cancel"))


def _add_performance_indexes():
	"""Composite indexes for the app's hottest filter+sort patterns.

	Frappe v16 does not auto-index Link/Select/Date columns, and this app
	filters almost exclusively on those — a perf audit that seeded 500k
	Visitor Pass / 1M Security Log / 1.5M Visitor Event Log rows found full
	table scans everywhere: a 21-minute Active Visitors report and a
	~39-hour overstay scheduler run (its per-row `Visitor Event Log` lookup
	in `tasks.flag_overstaying_visitors` is exactly `(visitor_pass,
	event_type)` below).

	`frappe.db.add_index` checks `has_index` before issuing the ALTER, so
	this is idempotent and safe to call on every migrate. It also commits
	internally — never call it inside a transaction you care about.

	Column order is load-bearing: the equality/filter column goes first, the
	sort or range column last, so MariaDB can serve the filter and the
	ORDER BY / range from one index without a filesort. Do not reorder.
	"""
	indexes = [
		("Visitor Pass", ["status", "visit_date"]),
		("Visitor Pass", ["person_to_visit", "modified"]),
		("Visitor Pass", ["owner", "modified"]),
		("Visitor Pass", ["workflow_state", "docstatus"]),
		("Visitor Pass", ["no_show", "status"]),
		("Security Log", ["visitor_pass", "event_type"]),
		("Security Log", ["event_type", "check_in_date_time"]),
		# The gate-wise count filters an OR of two branches — Check-In on
		# check_in_date_time, Check-Out on check_out_date_time — so indexing
		# only the first left the Check-Out half scanning every Check-Out row
		# whatever the date range. Both branches need their own composite.
		("Security Log", ["event_type", "check_out_date_time"]),
		("Visitor Event Log", ["visitor_pass", "event_type"]),
	]
	for doctype, fields in indexes:
		frappe.db.add_index(doctype, fields)


def _backfill_mobile_digits():
	"""Populate `mobile_digits` on passes saved before the column existed.

	The duplicate-visitor check matches on this column now, so a pass whose
	digits were never derived is invisible to it — which would reintroduce, for
	historical rows, exactly the silent "no match" the column was added to fix.

	Deliberately raw SQL rather than a loop of `set_value`: this is one
	statement over the whole table instead of a round trip per pass, and it must
	NOT touch `modified`. The dedupe query orders by `modified DESC`, so
	bumping it here would reorder every visitor's history at migrate time.

	Idempotent by construction — the WHERE clause matches only rows whose stored
	digits disagree with their number, so a second run updates nothing. It also
	self-heals a row edited by raw SQL elsewhere, which bypasses `before_save`.
	"""
	if frappe.db.db_type != "mariadb":
		return  # regexp_replace is MariaDB's; elsewhere each pass fills its digits on save
	digits = "regexp_replace(ifnull(mobile_number, ''), '[^0-9]', '')"
	# nosemgrep: frappe-sql-format-injection - fixed SQL expression, no user input
	frappe.db.sql(
		f"""
		update `tabVisitor Pass`
		set mobile_digits = {digits}
		where ifnull(mobile_number, '') != ''
		  and ifnull(mobile_digits, '') != {digits}
		"""
	)


def _restore_status_knocked_back_by_badge():
	"""Put back passes that minting a badge pulled back to "Items Verified".

	generate_badge_number used to set "Items Verified" whatever the pass's
	state, so a visitor who was inside or had left could end up there — and
	check-out then refused them. The truth is the pass's latest gate event.
	Idempotent: only an "Items Verified" pass whose latest Check-In/Check-Out log
	says otherwise is touched.
	"""
	rows = frappe.db.sql(
		"""
		SELECT vp.name,
		       (SELECT sl.event_type FROM `tabSecurity Log` sl
		        WHERE sl.visitor_pass = vp.name AND sl.event_type IN ('Check-In', 'Check-Out')
		        ORDER BY sl.creation DESC LIMIT 1) AS last_event
		FROM `tabVisitor Pass` vp
		WHERE vp.status = 'Items Verified' AND vp.docstatus = 1
		""",
		as_dict=True,
	)
	for row in rows:
		status = {"Check-In": "Checked-In", "Check-Out": "Checked-Out"}.get(row.last_event)
		if status:
			frappe.db.set_value("Visitor Pass", row.name, "status", status, update_modified=False)
			print(f"  restored {row.name} to {status} (a badge had reset it to Items Verified)")


def _make_gate_photos_private():
	"""Move gate and item photos taken before they were uploaded private.

	The gate camera uploaded them public, so a visitor's face and belongings sat
	under a guessable /files/ URL. Frappe's own File save moves the bytes and
	updates the record the file is attached to (the Security Log); the pass
	fields that copy the URL are updated here, and the pass gets its own File
	row so its readers can still open the photo. Idempotent: only public files
	with these names match.
	"""
	from visitormanagement.visitor_management.uploads import attach_existing_file

	for name in frappe.get_all(
		"File",
		filters={"is_private": 0, "file_name": ["like", "gate_photo_%"], "is_folder": 0},
		pluck="name",
	) + frappe.get_all(
		"File",
		filters={"is_private": 0, "attached_to_doctype": "Security Log", "attached_to_field": "item_image"},
		pluck="name",
	):
		f = frappe.get_doc("File", name)
		old_url = f.file_url
		f.is_private = 1
		f.save(ignore_permissions=True)
		for fieldname in ("gate_verified_photo", "visitor_photo"):
			for vp in frappe.get_all("Visitor Pass", filters={fieldname: old_url}, pluck="name"):
				frappe.db.set_value("Visitor Pass", vp, fieldname, f.file_url, update_modified=False)
				attach_existing_file(f.file_url, "Visitor Pass", vp, fieldname)
		frappe.db.sql(
			"update `tabSecurity Item Verify` set item_image = %s where item_image = %s",
			(f.file_url, old_url),
		)
		print(f"  made gate photo private: {f.file_url}")


def _repair_cancelled_status():
	"""Set `status` to Cancelled on passes that were cancelled before on_cancel did.

	Cancel skips `validate`, so nothing moved `status` off "Approved" /
	"Checked-Out" — a cancelled pass still showed under an "Approved" filter and
	offered the gate a Check In button. Idempotent: only rows that still disagree
	match; update_modified=False because this corrects a derived field.
	"""
	frappe.db.sql(
		"""
		UPDATE `tabVisitor Pass`
		SET status = 'Cancelled'
		WHERE docstatus = 2 AND IFNULL(status, '') != 'Cancelled'
		"""
	)
	if frappe.db._cursor.rowcount > 0:
		print(f"  set status Cancelled on {frappe.db._cursor.rowcount} cancelled visitor pass(es)")


def _repair_pending_status_drift():
	"""Realign `status` on passes still sitting in a pending approval lane.

	`status` is derived from `workflow_state` by
	`visitor_pass._sync_status_with_workflow`, but that runs in `validate` — so
	any path writing `status` through `db_set` skips it and the two fields drift.
	Badge generation did exactly that: it set "Items Verified" via `db_set`, and
	before the docstatus/workflow_state guard now in `generate_badge_number`
	existed, it could do so on a pass that had never been approved.

	The visible symptom was two widgets on one dashboard disagreeing — the
	"Pending Visitor Approvals" card counts `workflow_state like 'Pending%'` and
	read 15, while the "Pending Approvals by Department" chart counted
	`status = 'Pending Approval'` and read 14. A pass reported as approved-enough
	to have its items verified, while still awaiting an approver, is also simply
	wrong on its own terms.

	Only the pending lanes are repaired, and only in that one direction. Once a
	pass is Approved the gate legitimately owns `status` — "Items Verified",
	"Checked-In" and "Checked-Out" are movements, not approval states, and
	rewriting them would erase a real visit. That is why the 77 rows here sitting
	at workflow_state "Approved" with a later status are left untouched.

	Idempotent: the WHERE clause matches only rows that still disagree, so a
	second run updates nothing. `update_modified=False` keeps the repair out of
	the passes' modification history — it corrects a derived field, it is not a
	business event.
	"""
	from visitormanagement.visitor_management.workflow_builder import pending_lanes

	lanes = sorted(pending_lanes())
	if not lanes:
		return

	drifted = frappe.get_all(
		"Visitor Pass",
		filters={
			"workflow_state": ("in", lanes),
			"status": ("!=", "Pending Approval"),
			"docstatus": ("<", 2),
		},
		pluck="name",
	)
	for name in drifted:
		frappe.db.set_value("Visitor Pass", name, "status", "Pending Approval", update_modified=False)

	if drifted:
		print(f"  realigned status on {len(drifted)} pass(es) still awaiting approval")


def _restore_core_page_permissions():
	"""Undo the damage an earlier `_grant("Page", "Employee")` did.

	Frappe uses Custom DocPerm *instead of* the DocType's own permissions the
	moment a single row exists, not in addition to them. Granting Employee read
	on the core `Page` doctype therefore replaced Page's entire permission set
	with that one row: System Manager silently lost write/create/delete on
	Pages, and every Employee gained read plus export of Page source.

	An app must never call `add_permission` on a DocType it does not own.
	`reset_perms` drops the Custom DocPerm rows so core's shipped permissions —
	which already give the desk the read access this was reaching for — take
	over again.
	"""
	if not frappe.db.exists("Custom DocPerm", {"parent": "Page"}):
		return

	from frappe.permissions import reset_perms

	reset_perms("Page")
	frappe.clear_cache(doctype="Page")
	print("  restored core Page permissions (removed this app's stray Custom DocPerm)")


def _align_visitor_pass_submit():
	"""`submit` on Visitor Pass belongs to approvers only.

	The workflow gives Employee exactly one transition — Draft to the pending
	lane — and that target is a `doc_status = 0` state, which `apply_workflow`
	persists with `doc.save()`. So the requestor never needs submit rights.
	Holding them meant a bare `frappe.client.submit` skipped the approver and
	landed the pass directly on `Approved`, the state the gate honours.

	Which roles *do* need it follows the Visitor Type masters, the same source
	the workflow lanes are generated from, so adding a Visitor Type with a new
	approver role keeps working without touching permissions by hand.
	"""
	_revoke("Visitor Pass", "Employee", ("submit",))

	from visitormanagement.visitor_management.workflow_builder import approver_roles

	for role in approver_roles():
		if not frappe.db.exists("Role", role):
			continue
		if not frappe.db.get_value(
			"Custom DocPerm", {"parent": "Visitor Pass", "role": role, "permlevel": 0}, "submit"
		):
			_grant("Visitor Pass", role, "submit")
			print(f"  granted submit on Visitor Pass to approver role {role}")


def _sync_approval_notification_recipients():
	"""Point the approval alert at whoever the Visitor Types actually name.

	The recipient rows were written by hand for the five roles that existed when
	the workflow was static. The workflow is generated from the Visitor Type
	masters now, so a site that points a type at any other role gets a lane with
	nobody watching it — an Auditor pass sat in the Facility Manager's queue and
	no Facility Manager was ever told.

	Rebuilt from the same source the lanes come from, so the two cannot drift.
	"""
	name = "VMS PRR Submitted"
	if not frappe.db.exists("Notification", name):
		return

	from visitormanagement.visitor_management.workflow_builder import approver_roles, lane_for_role

	roles = [r for r in approver_roles() if frappe.db.exists("Role", r)]
	if not roles:
		return

	doc = frappe.get_doc("Notification", name)
	wanted = {(r, f"doc.workflow_state == {lane_for_role(r)!r}") for r in roles}
	current = {(r.receiver_by_role, (r.condition or "").strip()) for r in doc.recipients}
	if wanted == current:
		return

	doc.set("recipients", [])
	for role in roles:
		doc.append(
			"recipients",
			{
				"receiver_by_role": role,
				"condition": f"doc.workflow_state == {lane_for_role(role)!r}",
			},
		)
	doc.save(ignore_permissions=True)
	added = sorted(r for r, _ in wanted - current)
	if added:
		print(f"  approval alert now also reaches: {', '.join(added)}")


def _seed_gates():
	for gate in GATES:
		if frappe.db.exists("Visitor Gate", gate["gate_name"]):
			continue
		frappe.get_doc({"doctype": "Visitor Gate", "is_active": 1, **gate}).insert(ignore_permissions=True)
		print(f"  created Visitor Gate {gate['gate_name']}")


def _seed_visitor_types():
	for spec in VISITOR_TYPES:
		name = spec["visitor_type_name"]
		if not frappe.db.exists("Visitor Type", name):
			frappe.get_doc({"doctype": "Visitor Type", "is_active": 1, "requires_badge": 1, **spec}).insert(
				ignore_permissions=True
			)
			print(f"  created Visitor Type {name}")
			continue
		# Fill only what is still blank so admin edits survive.
		updates = {
			f: v
			for f, v in spec.items()
			if f != "visitor_type_name" and not frappe.db.get_value("Visitor Type", name, f)
		}
		if frappe.db.get_value("Visitor Type", name, "requires_badge") is None:
			updates["requires_badge"] = 1
		if updates:
			frappe.db.set_value("Visitor Type", name, updates, update_modified=False)


def _seed_id_proof_types():
	for spec in ID_PROOF_TYPES:
		if frappe.db.exists("ID Proof Type", spec["id_proof_type_name"]):
			continue
		frappe.get_doc({"doctype": "ID Proof Type", "is_active": 1, **spec}).insert(ignore_permissions=True)
		print(f"  created ID Proof Type {spec['id_proof_type_name']}")
	frappe.cache.delete_value("vms_id_proof_types")


def _seed_settings():
	settings = frappe.get_single("VMS Settings")
	changed = False

	for field, value in POLICY_DEFAULTS.items():
		if not settings.get(field):
			settings.set(field, value)
			changed = True

	if not settings.get("meal_windows"):
		for row in MEAL_WINDOWS:
			settings.append("meal_windows", row)
		changed = True

	if changed:
		settings.flags.ignore_permissions = True
		settings.flags.ignore_mandatory = True
		settings.save(ignore_permissions=True)
		print("  seeded VMS Settings defaults")


def _drop_stale_property_setters():
	for doctype, fieldname in LINK_FIELDS:
		for row in frappe.get_all(
			"Property Setter",
			filters={"doc_type": doctype, "field_name": fieldname, "property": "options"},
			fields=["name", "value"],
		):
			value = (row.value or "").strip()
			# A valid Link options value is a single DocType name.
			if value and "\n" not in value and frappe.db.exists("DocType", value):
				continue
			frappe.delete_doc("Property Setter", row.name, ignore_permissions=True, force=True)
			print(f"  dropped stale Property Setter {row.name}")


def _clear_stale_layout_fields():
	"""Strip Customer-only CRM values off passes that are not Customer visits.

	`crm_reference_type` carries a doctype default of "Lead", so every pass saved
	before VisitorPass._clear_fields_from_other_layouts existed holds a value its
	form immediately clears on load — which marks the pass dirty and hides the
	workflow Actions button behind a Save button. New saves are handled in
	validate(); this repairs the rows already on disk.
	"""
	frappe.db.sql(
		"""
		UPDATE `tabVisitor Pass`
		SET crm_reference_type = NULL, crm_lead_opportunity = NULL
		WHERE (IFNULL(visitor_type_layout, '') != 'Customer' OR IFNULL(entry_type, '') != 'New')
		  AND (IFNULL(crm_reference_type, '') != '' OR IFNULL(crm_lead_opportunity, '') != '')
		"""
	)
	if frappe.db._cursor.rowcount > 0:
		print(f"  cleared stray CRM fields on {frappe.db._cursor.rowcount} visitor pass(es)")

	# Same class of problem, different cause. `vip_category` is a Select whose
	# options began with "Board Member" rather than a blank line, so Frappe
	# assigned the first option to every pass that left the field empty — and the
	# VIP layout was absent from VisitorPass.LAYOUT_FIELDS, so unlike every other
	# layout its fields were never cleared for passes of another type. The result:
	# Contractor / Customer / Supplier / Candidate passes filed as
	# "Board Member" in reports and exports, on records whose VIP
	# section does not even render. Both causes are fixed going forward; this
	# clears what is already stored.
	frappe.db.sql(
		"""
		UPDATE `tabVisitor Pass`
		SET vip_category = NULL, protocol_notes = NULL, interpreter_language = NULL,
		    mdceo_notified = 0, interpreter_required = 0
		WHERE IFNULL(visitor_type_layout, '') != 'VIP'
		  AND (IFNULL(vip_category, '') != '' OR IFNULL(protocol_notes, '') != ''
		       OR IFNULL(interpreter_language, '') != ''
		       OR IFNULL(mdceo_notified, 0) != 0 OR IFNULL(interpreter_required, 0) != 0)
		"""
	)
	if frappe.db._cursor.rowcount > 0:
		print(f"  cleared stray VIP fields on {frappe.db._cursor.rowcount} visitor pass(es)")


def _rewrite_event_log_details():
	"""Turn the stored JSON payloads in Visitor Event Log into readable lines.

	`log_visitor_event` used to write `frappe.as_json(details)`, so the Details
	section of every event showed the raw object — braces, quoted keys, a
	`"exception_reason": null` line whenever nothing had gone wrong, and the
	security officer as `HR-EMP-00001` rather than by name. New events are
	written as prose now; this converts the ones already on disk, which staff
	would otherwise keep reading in the old form forever.

	Anything that does not parse as JSON is left exactly as it is: it is either
	already converted or was written by hand.
	"""
	import json
	import re

	from visitormanagement.visitor_management.lifecycle import _format_event_details

	rows = frappe.get_all(
		"Visitor Event Log",
		or_filters=[["details", "like", "{%"], ["details", "like", "%(HR-EMP%"]],
		fields=["name", "details"],
	)
	rewritten = 0
	for row in rows:
		details = row.details or ""
		try:
			payload = json.loads(details)
		except (ValueError, TypeError):
			payload = None

		if isinstance(payload, dict):
			rewritten_text = _format_event_details(payload)
		else:
			# Already prose, but written while the formatter still appended the
			# Employee ID after the name. Drop the trailing "(HR-EMP-…)" so old
			# entries read the same way new ones do.
			rewritten_text = re.sub(r"\s*\(HR-EMP-[^)]*\)", "", details)
			if rewritten_text == details:
				continue

		frappe.db.set_value(
			"Visitor Event Log",
			row.name,
			"details",
			rewritten_text,
			update_modified=False,
		)
		rewritten += 1

	if rewritten:
		print(f"  rewrote details on {rewritten} visitor event log(s)")


def _allow_portal_uploads():
	"""Let an invited visitor attach their ID scan and photo.

	The pre-registration portal is a guest-facing web form whose ID Proof Scan
	and Visitor Photo fields are mandatory, and `upload_file` raises
	PermissionError for Guest unless this System Setting is on — so without it
	no visitor can ever complete the form. Frappe ships it off and offers no
	per-web-form equivalent, so the app turns it on once at setup.

	The flag alone would let any anonymous request upload anything, so it is
	paired with `guard_guest_upload` (see visitor_management/portal_upload.py),
	which holds guest uploads to the same file types and size limit the portal
	advertises. Never turned back off — a site that deliberately disabled it
	after install keeps its choice on the next migrate.

	That last sentence used to be a lie. The old body was "if the setting is
	off, turn it on", which cannot tell "never configured" apart from
	"an administrator switched this off on purpose", so it silently re-enabled
	a site-wide guest upload flag on *every* migrate — the opposite of what
	this docstring promised, and invisible in the deploy log because nothing
	recorded who changed it. A one-time marker is what makes the promise true:
	we enable it once, remember that we did, and afterwards the site's own
	choice wins.
	"""
	already_enabled = frappe.db.get_single_value("System Settings", "allow_guests_to_upload_files")

	if frappe.db.get_default(_PORTAL_UPLOADS_MARKER):
		# We have had our one turn. Whatever the setting says now is the site's
		# decision, not ours. Say so loudly if the portal is therefore broken,
		# because "visitors cannot submit the form" is otherwise a mystery.
		if not already_enabled:
			print(
				"  NOTE: allow_guests_to_upload_files is off. The visitor pre-registration "
				"portal cannot accept ID scans or photos until it is switched back on."
			)
		return

	if not already_enabled:
		frappe.db.set_single_value("System Settings", "allow_guests_to_upload_files", 1)
		frappe.db.set_default(_PORTAL_UPLOADS_SELF_ENABLED_MARKER, "1")
		print("  enabled allow_guests_to_upload_files (required by the visitor portal)")
	else:
		frappe.db.set_default(_PORTAL_UPLOADS_PREEXISTING_MARKER, "1")

	# Recorded whether or not we changed anything: the point of the marker is
	# "setup has considered this setting", so a site that already had it on
	# does not get it forced back on later either.
	frappe.db.set_default(_PORTAL_UPLOADS_MARKER, "1")


def _configure_notifications():
	"""Turn off the Notification-engine copies that duplicate a code-sent email.

	Used to force `enabled = 0` unconditionally on every migrate, which
	contradicted its own docstring's promise that the setting was preserved —
	an admin who re-enabled "VMS Host Alert" (say, because they wanted the
	Notification copy back for some reason) lost that choice, silently and
	without a log line, on the next deploy. Each name now gets disabled at
	most once per site, the same marker pattern as `_activate_blacklist_entries`
	and `_allow_portal_uploads`: we turn it off once, remember that we did, and
	whatever the site does with the toggle afterwards is authoritative.
	"""
	for name in DISABLED_NOTIFICATIONS:
		marker = _NOTIFICATION_DISABLED_MARKER.format(name=name)
		if frappe.db.get_default(marker):
			continue
		if frappe.db.exists("Notification", name):
			frappe.db.set_value("Notification", name, "enabled", 0, update_modified=False)
			print(f"  disabled duplicate Notification {name} (one-time)")
		frappe.db.set_default(marker, "1")

	_repair_notification_conditions()


def _repaint_notification_history():
	"""Apply the same repaint to alerts already sitting in document timelines.

	A Communication stores the HTML as it was rendered at send time, so fixing
	the templates only helps future alerts — every message already in a timeline
	stays unreadable. This changes presentation only: the wrapper gains a
	background, headings and links gain the colour they were already meant to
	have. No wording, recipient or timestamp is touched.
	"""
	rows = frappe.get_all(
		"Communication",
		filters={
			"communication_type": "Automated Message",
			"reference_doctype": ("in", VMS_ALERT_DOCTYPES),
		},
		fields=["name", "content"],
	)

	repainted = 0
	for row in rows:
		fixed = repaint_email_html(row.content)
		if fixed and fixed != row.content:
			frappe.db.set_value("Communication", row.name, "content", fixed, update_modified=False)
			repainted += 1

	if repainted:
		print(f"  repainted {repainted} alert(s) already in timelines so they read in dark mode")


def _backfill_host_name():
	"""Fill host_name on passes that predate the field.

	Messages print the host's name rather than `person_to_visit`, which stores an
	Employee ID — meaningless to a visitor and an internal identifier to leak.
	fetch_from only populates on save, so existing rows would keep rendering the
	raw ID in every alert until someone happened to re-save them.
	"""
	stale = frappe.get_all(
		"Visitor Pass",
		filters={"person_to_visit": ("is", "set"), "host_name": ("in", [None, ""])},
		fields=["name", "person_to_visit"],
	)
	if not stale:
		return

	names = dict(
		frappe.get_all(
			"Employee",
			filters={"name": ("in", list({r.person_to_visit for r in stale}))},
			fields=["name", "employee_name"],
			as_list=True,
		)
	)
	filled = 0
	for row in stale:
		who = names.get(row.person_to_visit)
		if not who:
			continue
		frappe.db.set_value("Visitor Pass", row.name, "host_name", who, update_modified=False)
		filled += 1
	print(f"  backfilled host_name on {filled} visitor pass(es)")


def _backfill_host_email():
	"""Fill host_email on passes that predate the field.

	Notifications address the host through this field because Frappe resolves
	`receiver_by_document_field` by running the field's *value* through
	validate_email_address — it does not follow links. `person_to_visit` holds
	an Employee ID, so every alert aimed at the host was being dropped with no
	error and no log line.

	fetch_from only populates on save, so rows written before the field existed
	stay empty and would keep silently notifying nobody.
	"""
	stale = frappe.get_all(
		"Visitor Pass",
		filters={"person_to_visit": ("is", "set"), "host_email": ("in", [None, ""])},
		fields=["name", "person_to_visit"],
	)
	if not stale:
		return

	emails = dict(
		frappe.get_all(
			"Employee",
			filters={"name": ("in", list({r.person_to_visit for r in stale}))},
			fields=["name", "user_id"],
			as_list=True,
		)
	)

	filled = 0
	for row in stale:
		email = emails.get(row.person_to_visit)
		if not email:
			continue
		frappe.db.set_value("Visitor Pass", row.name, "host_email", email, update_modified=False)
		filled += 1

	missing = len(stale) - filled
	print(f"  backfilled host_email on {filled} visitor pass(es)")
	if missing:
		print(
			f"  {missing} pass(es) still have no host_email — their Employee has no "
			"linked user, so host alerts for them cannot be delivered"
		)


def _repair_notification_conditions():
	"""Re-arm conditions that a null condition_type had silently switched off.

	v16 splits the old single `condition` into `condition_type` + `condition`,
	and evaluate_alert now reads:

	    if alert.condition_type == "Python" and alert.condition: ...
	    elif alert.condition_type == "Filters" and alert.filters: ...

	Neither branch matches when condition_type is null, so the condition is not
	merely mis-evaluated — it is never consulted, and the alert sends on every
	single trigger. Rows written before the field existed carry that null, and
	the field default only applies to newly created documents, so nothing
	backfilled them on upgrade.

	The visible symptom was a rejection email arriving the moment a pass was
	sent for approval: "VMS Pass Rejected" ignored its own
	`workflow_state == 'Rejected'` test and fired on any value change.

	Only this app's notifications are repaired. Others on the site can carry the
	same null and are reported rather than changed, because silently altering
	another app's alerting during our migrate is a decision for the operator.
	"""
	ours = set(
		frappe.get_all(
			"Notification",
			filters={"module": ("in", frappe.get_module_list("visitormanagement"))},
			pluck="name",
		)
	)

	broken = frappe.get_all(
		"Notification",
		filters={"condition_type": ("in", [None, ""]), "condition": ("!=", "")},
		fields=["name", "module"],
	)

	repaired = [n.name for n in broken if n.name in ours]
	for name in repaired:
		frappe.db.set_value("Notification", name, "condition_type", "Python", update_modified=False)
	if repaired:
		print(f"  re-armed ignored conditions on: {', '.join(sorted(repaired))}")

	foreign = sorted(f"{n.name} ({n.module})" for n in broken if n.name not in ours)
	if foreign:
		print(
			"  NOTE: these non-VMS notifications also have a null condition_type, so "
			"their conditions are ignored and they fire on every trigger. Left "
			"unchanged — set condition_type to 'Python' on each to re-arm them:"
		)
		for entry in foreign:
			print(f"    - {entry}")


def _activate_blacklist_entries():
	"""One-time backfill: every blacklist lookup filters on is_active, and
	entries created under the old default of 0 blocked nobody.

	This used to run unconditionally on every migrate, which meant it did far
	more than backfill: deactivating a Visitor Blacklist entry is the normal UI
	action for "we investigated, this person is cleared", and with
	`blacklist_action = "Block Entry"` an is_active row is what turns someone
	away at the gate. Re-flipping every deactivated row back to 1 on the next
	deploy silently re-blacklisted anyone an admin had cleared — a security
	regression baked into the deploy process itself.

	Guarded the same way `_allow_portal_uploads` guards its own one-time write:
	the historical backfill runs once, is recorded, and every deactivation after
	that — however it happened — is the site's own decision and is never
	reverted.
	"""
	if not frappe.db.table_exists("Visitor Blacklist"):
		return
	if frappe.db.get_default(_BLACKLIST_BACKFILL_MARKER):
		return
	dormant = frappe.get_all("Visitor Blacklist", filters={"is_active": 0}, pluck="name")
	for name in dormant:
		frappe.db.set_value("Visitor Blacklist", name, "is_active", 1, update_modified=False)
	if dormant:
		print(f"  activated {len(dormant)} dormant blacklist entries (one-time historical backfill)")
	frappe.db.set_default(_BLACKLIST_BACKFILL_MARKER, "1")


def _migrate_item_category_vocabulary():
	"""Retire the orphaned `Electronic` value on Security Item Verify.item_type.

	The gate row and the pass row describe the same physical object, but they
	used to offer two different word lists — the pass said `Electronics`, the
	gate said `Electronic`, and the gate had no `Weapon` at all. Neither Select
	had a blank first option, and nothing in the code ever set `item_type`, so a
	browser rendered the field pre-selected on its first choice and saved it.
	That is how 77 of 87 rows came to read "Electronic", fabric samples and a
	measuring tape included.

	Both fields now share one vocabulary that starts blank. `Electronic` is no
	longer in it, and Frappe refuses to save a document holding a Select value
	outside its options — so without this, editing any historical Security Log
	would fail with "Item Type cannot be Electronic".

	Mapped to `Electronics` rather than blanked. The two spellings always meant
	the same category, so this is a rename and invents nothing; blanking would
	erase a value off a submitted audit record. Rows that were only ever
	"Electronic" because of the bug stay mislabelled, and the gate can correct
	them — that is a visible wrong answer rather than a silent one.

	Idempotent: after it runs no row holds the old value, so a re-run is a no-op.
	"""
	if not frappe.db.table_exists("Security Item Verify"):
		return

	stale = frappe.db.sql(
		"""SELECT COUNT(*) FROM `tabSecurity Item Verify` WHERE item_type = %s""",
		"Electronic",
	)[0][0]
	if not stale:
		return

	frappe.db.sql(
		"""UPDATE `tabSecurity Item Verify` SET item_type = %s WHERE item_type = %s""",
		("Electronics", "Electronic"),
	)
	print(f"  item_type: renamed {stale} legacy 'Electronic' row(s) to 'Electronics'")


def _harden_invitation_token_collation():
	"""Make `invitation_token` compare byte-for-byte, not case-insensitively.

	The column is a plain Data field, so it inherits the table's default
	collation — `utf8mb4_unicode_ci` on every site this app has ever created —
	and MariaDB's `_ci` collations fold case for comparison: a lookup for a
	token with its case flipped returned the same Visitor
	Invitation. `invitation_token` is a bearer credential (whoever holds the
	string opens the visitor's pre-registration form), so a case-scrambled copy
	of it should not work; folding case only throws away entropy the token was
	minted with (`secrets.token_urlsafe(24)` in visitor_invitation.py), it never
	adds anything a legitimate holder needs.

	`utf8mb4_bin` is MariaDB's byte-comparison collation — the standard fix for
	"this column must be case-sensitive" here, short of making the whole table
	binary. Safe to run on every migrate: it only ALTERs when the stored
	collation is not already `utf8mb4_bin`, and safe against existing data by
	construction — a column that has been comparing case-insensitively the
	whole time cannot already contain two tokens that differ only by case, so
	tightening the comparison can never collide with what's on disk. The
	column's nullability, default and length are carried through unchanged;
	only the comparison rules change. The existing unique index is unaffected —
	MODIFY COLUMN does not touch it, and NULL keeps comparing as distinct from
	NULL either way.
	"""
	if frappe.db.db_type != "mariadb":
		# The case folding this fixes is MariaDB's `_ci` collation; SQLite and
		# Postgres already compare text byte-for-byte, and neither has
		# information_schema.COLUMNS.COLLATION_NAME in this form.
		return
	if not frappe.db.table_exists("Visitor Invitation"):
		return
	if not frappe.db.has_column("Visitor Invitation", "invitation_token"):
		return

	current = frappe.db.sql(
		"""
		select COLLATION_NAME
		from information_schema.COLUMNS
		where TABLE_SCHEMA = database()
		  and TABLE_NAME = 'tabVisitor Invitation'
		  and COLUMN_NAME = 'invitation_token'
		"""
	)
	if not current or (current[0][0] or "").lower() == "utf8mb4_bin":
		return

	# ALTER is DDL, and MariaDB implicitly commits around DDL — Frappe's own
	# `check_implicit_commit` refuses to run it while writes from earlier in this
	# same migrate are still pending, exactly to stop that implicit commit from
	# landing silently in the middle of an unrelated transaction. `add_index`
	# (used above in `_add_performance_indexes`) hits the same rule and clears it
	# the same way: commit first, so the ALTER starts its own transaction instead
	# of hijacking whatever was still open.
	# nosemgrep: frappe-manual-commit - end of setup, persisted before other apps' migrate hooks
	frappe.db.commit()
	frappe.db.sql(
		"""
		alter table `tabVisitor Invitation`
		modify `invitation_token` varchar(140) collate utf8mb4_bin default null
		"""
	)
	print("  hardened invitation_token to a case-sensitive collation (utf8mb4_bin)")


def _seed_static_workflows():
	"""Create Conference Room Booking Approval / Hospitality Request Approval
	from `workflow_seed.json`, once each, and never touch them again.

	These used to ship as a `fixtures` hook entry (hooks.py) pointing at
	`fixtures/4_workflow.json`. Removing that hooks.py entry turned out NOT to
	be enough on its own: `frappe.utils.fixtures.import_fixtures` does not
	consult the hooks.py `fixtures` list at import time at all — it globs
	every `*.json` file physically present in the app's `fixtures/` directory
	and force-imports each one (`import_file_by_path(..., force=True)`,
	bypassing the `modified` guard entirely) on every single migrate. The
	hooks.py list only controls what `bench export-fixtures` writes back out.
	So as long as the workflow JSON sat in `fixtures/`, it kept being
	force-reimported regardless of what hooks.py said — proved by editing
	`allow_self_approval` on a live transition and watching a migrate discard
	it even after the hooks.py entry was removed.

	The real fix is this file living outside `fixtures/` altogether, read
	directly by this function instead of by Frappe's fixture importer. Unlike
	Visitor Pass Approval, these two workflows have no Visitor Type-style
	master to regenerate their lanes from, so full runtime generation is not
	the right shape here; a one-time seed is. `frappe.db.exists` is checked
	per workflow, so a site that already has one of these two (from an
	earlier version's fixture import) is left alone — this only ever fills in
	what is missing, and every Desk edit made afterwards — approver roles, an
	added approval level, `allow_self_approval` — survives every migrate.
	"""
	import json

	path = frappe.get_app_path("visitormanagement", "workflow_seed.json")
	try:
		# nosemgrep: frappe-security-file-traversal - path is the app's own bundled workflow_seed.json
		with open(path) as f:
			specs = json.load(f)
	except OSError:
		return

	for spec in specs:
		name = spec.get("name") or spec.get("workflow_name")
		if not name or frappe.db.exists("Workflow", name):
			continue
		_ensure_seed_workflow_dependencies(spec)
		seed = {k: v for k, v in spec.items() if k not in ("modified", "creation", "owner", "modified_by")}
		frappe.get_doc(seed).insert(ignore_permissions=True)
		print(f"  seeded Workflow {name} (first install only, never overwritten again)")


def _ensure_seed_workflow_dependencies(spec):
	"""Create the Workflow State / Action Master / Role records a seed links to.

	`frappe.installer.install_app` runs the `after_install` hook (installer.py:332)
	BEFORE `sync_fixtures` (installer.py:339). So on a FRESH install none of the
	Workflow States in `fixtures/2_workflow_state.json`, and none of the actions in
	`fixtures/3_workflow_action_master.json`, exist yet at the moment
	`_seed_static_workflows` above runs. Inserting a Workflow whose transitions
	link to them therefore raised

	    LinkValidationError: Could not find Row #2: State: Pending Approval, ...

	which aborted `after_install` outright. The damage was much wider than the two
	seeded workflows: `setup_visitor_management()` never got past that line, so
	`_build_workflow()` and the three approver-wiring steps after it never ran
	either — a buyer's fresh install ended with the app in `installed_apps`, a
	traceback on screen, no Visitor Pass approval workflow at all, and no approver
	permissions.

	This could not happen while these two shipped as `fixtures/4_workflow.json`:
	`import_fixtures` walks that directory in filename order, so `2_` and `3_` were
	always imported before `4_`. Moving the seed out of `fixtures/` (see
	`_seed_static_workflows`' own docstring for why that move was necessary) gave up
	that ordering guarantee, so the dependencies are asserted explicitly here
	instead.

	These are the same three helpers `workflow_builder.build_workflow` already calls
	for the generated Visitor Pass workflow — which is exactly why that workflow was
	never affected by this, and why the failure only showed up on a fresh install.
	"""
	from visitormanagement.visitor_management.workflow_builder import (
		PENDING_STYLE,
		STATE_STYLES,
		_ensure_role,
		_ensure_workflow_action,
		_ensure_workflow_state,
	)

	states = set()
	roles = set()

	for row in spec.get("states", []):
		if row.get("state"):
			states.add(row["state"])
		if row.get("allow_edit"):
			roles.add(row["allow_edit"])

	for row in spec.get("transitions", []):
		for key in ("state", "next_state"):
			if row.get(key):
				states.add(row[key])
		if row.get("action"):
			_ensure_workflow_action(row["action"])
		if row.get("allowed"):
			roles.add(row["allowed"])

	for state in sorted(states):
		# Same styling vocabulary as the generated workflow, so a seeded
		# "Pending Approval" is coloured like every generated "Pending <role>" lane.
		_ensure_workflow_state(state, STATE_STYLES.get(state, PENDING_STYLE))

	for role in sorted(roles):
		_ensure_role(role)


def _build_workflow():
	from visitormanagement.visitor_management.workflow_builder import build_workflow

	frappe.clear_cache()
	name = build_workflow()
	if name:
		print(f"  rebuilt workflow {name}")


def _grant_visitor_pass_cancel():
	"""Let every current approver role actually run the Cancel it is offered.

	Kept as its own function so `Visitor Type.on_update` can call it too: the
	Cancel transitions are regenerated the moment an admin points a type at a new
	approver role, and a permission granted only on migrate would leave that role
	with a button it cannot use until a developer intervenes.
	"""
	from visitormanagement.visitor_management.workflow_builder import approver_roles

	for role in sorted(set(approver_roles()) | set(VISITOR_PASS_CANCEL_ADMINS)):
		_grant("Visitor Pass", role, "cancel")
		_grant("Visitor Pass", role, "amend")
