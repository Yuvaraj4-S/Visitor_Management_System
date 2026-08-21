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

# Roles that need to read Employee so host//approver link widgets resolve.
EMPLOYEE_READERS = [
	"Security", "Hospitality Manager", "Host Employee", "Facility Manager",
	"HOD", "HR Manager", "Sales Manager", "CEO",
]

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
	{"visitor_type_name": "Contractor", "approver_role": "System Manager", "badge_prefix": "CON",
	 "badge_colour": "Orange", "default_gate": "Back Gate", "detail_layout": "Contractor"},
	{"visitor_type_name": "Candidate", "approver_role": "HR Manager", "badge_prefix": "CAN",
	 "badge_colour": "Purple", "default_gate": "Main Gate", "detail_layout": "Candidate"},
	{"visitor_type_name": "Customer", "approver_role": "Sales Manager", "badge_prefix": "CUS",
	 "badge_colour": "Green", "default_gate": "Main Gate", "detail_layout": "Customer"},
	{"visitor_type_name": "Supplier", "approver_role": "System Manager", "badge_prefix": "SUP",
	 "badge_colour": "Teal", "default_gate": "Loading Dock", "detail_layout": "Supplier"},
	{"visitor_type_name": "VIP", "approver_role": "HOD", "secondary_approver_role": "CEO",
	 "badge_prefix": "VIP", "badge_colour": "Gold", "default_gate": "VIP Entrance",
	 "detail_layout": "VIP", "requires_executive_notification": 1, "issue_badge_at_gate": 1},
]

GATES = [
	{"gate_name": "Main Gate", "location": "Front of building"},
	{"gate_name": "Back Gate", "location": "Rear entrance"},
	{"gate_name": "VIP Entrance", "location": "Executive lobby"},
	{"gate_name": "Loading Dock", "location": "Goods entrance"},
	{"gate_name": "Emergency Exit", "location": "Fire exit"},
]

ID_PROOF_TYPES = [
	{"id_proof_type_name": "Aadhaar", "aliases": "aadhar\nuid",
	 "validation_method": "Aadhaar (Verhoeff)", "normalisation": "Strip spaces and hyphens",
	 "error_message": "Aadhaar must be exactly 12 digits, must not start with 0 or 1, "
	                  "and must pass the UIDAI Verhoeff checksum."},
	{"id_proof_type_name": "PAN Card", "aliases": "pan", "validation_method": "Regex",
	 "validation_regex": "^[A-Z]{5}[0-9]{4}[A-Z]$", "normalisation": "Uppercase and strip spaces",
	 "error_message": "PAN must be in the format ABCDE1234F "
	                  "(5 uppercase letters, 4 digits, 1 uppercase letter)."},
	{"id_proof_type_name": "Passport", "validation_method": "Regex",
	 "validation_regex": "^[A-Z][0-9]{7}$", "normalisation": "Uppercase and strip spaces",
	 "error_message": "Passport must be in the format A1234567 "
	                  "(1 uppercase letter followed by 7 digits)."},
	{"id_proof_type_name": "Driving License", "aliases": "dl\ndriving licence",
	 "validation_method": "Regex", "validation_regex": "^[A-Z]{2}[0-9]{2} ?[0-9]{11}$",
	 "normalisation": "Collapse spaces",
	 "error_message": "Driving License must be in the format SS00 00000000000 "
	                  "(2 letters + 2 digits + optional space + 11 digits)."},
	# The Indian passport pattern rejects most real foreign passport numbers, so
	# ship a permissive international type and mark it foreign-eligible.
	{"id_proof_type_name": "Foreign Passport", "aliases": "international passport",
	 "validation_method": "Regex", "validation_regex": "^[A-Z0-9]{6,12}$",
	 "normalisation": "Uppercase and strip spaces", "valid_for_foreign_nationals": 1,
	 "error_message": "Foreign Passport must be 6–12 letters or digits."},
]

MEAL_WINDOWS = [
	{"meal_label": "Breakfast", "start_time": "08:00:00", "end_time": "09:00:00"},
	{"meal_label": "Lunch", "start_time": "13:00:00", "end_time": "14:00:00"},
	{"meal_label": "Dinner", "start_time": "20:00:00", "end_time": "21:30:00"},
]

# Int settings read back as 0 when unset, and 0 means "no limit" for the
# booking ceiling — so an unseeded site would silently lose the guard.
POLICY_DEFAULTS = {
	"max_advance_booking_days": 90,
	"invitation_expiry_days": 7,
	"no_show_grace_hours": 4,
	"default_country_code": "91",
	"home_country": "India",
}

# These duplicate emails the app already sends from code, so the Notification
# engine copies stay off. `enabled` is preserved across migrates once set.
# Each of these duplicates an email the app already sends from code — the code
# versions carry attachments and per-type detail the Notification cannot build,
# so the Notification copy is the one that goes. Without this the visitor got
# two "Visit Approved" emails for the same pass.
DISABLED_NOTIFICATIONS = ["VMS Host Alert", "VMS Food Dept Alert", "VMS Approval Email"]

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
	("Visitor Pass", "id_proof_type"), ("Visitor Pass", "visitor_type"),
	("Visitor Invitation", "visitor_type"), ("Security Log", "id_proof_type_verified"),
	("Security Log", "gate_name"), ("Visitor Blacklist", "id_proof_type"),
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
def after_install():
	setup_visitor_management()


def after_migrate():
	setup_visitor_management()


def setup_visitor_management():
	"""Create/refresh everything the app needs to actually work."""
	_ensure_roles()
	_ensure_permissions()
	_seed_gates()
	_seed_visitor_types()
	_seed_id_proof_types()
	_seed_settings()
	_drop_stale_property_setters()
	_clear_stale_layout_fields()
	_rewrite_event_log_details()
	_allow_portal_uploads()
	_configure_notifications()
	_repaint_notification_history()
	_backfill_host_email()
	_backfill_host_name()
	_activate_blacklist_entries()
	_build_workflow()
	# After the Visitor Types are seeded and the workflow is generated, so the
	# approver set it reads is the same one the lanes were built from.
	_align_visitor_pass_submit()
	_sync_approval_notification_recipients()
	frappe.db.commit()


def _ensure_roles():
	for role in ROLES:
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(
				ignore_permissions=True
			)
			print(f"  created Role {role}")


def _grant(doctype, role, ptype="read"):
	"""Grant one privilege, and only that privilege.

	`add_permission` creates a Custom DocPerm from the Role Permission defaults,
	and `export` defaults to 1 — so a plain `_grant(dt, role)` intended as
	"let them read" silently also let them bulk-export the whole table. Read
	access to a master and the right to download it in full are different
	decisions; callers now have to ask for `export` explicitly.
	"""
	if not frappe.db.exists("Role", role):
		return
	created = False
	if not frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0}):
		add_permission(doctype, role, 0)
		created = True
	update_permission_property(doctype, role, 0, ptype, 1)
	if created and ptype != "export":
		update_permission_property(doctype, role, 0, "export", 0)


def _revoke(doctype, role, ptypes):
	"""Take a privilege away from a role, if it was ever granted."""
	if not frappe.db.exists("Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0}):
		return
	for ptype in ptypes:
		if frappe.db.get_value(
			"Custom DocPerm", {"parent": doctype, "role": role, "permlevel": 0}, ptype
		):
			update_permission_property(doctype, role, 0, ptype, 0)
			print(f"  revoked {ptype} on {doctype} from {role}")


def _ensure_permissions():
	for role in EMPLOYEE_READERS:
		_grant("Employee", role)
	for role in HOSPITALITY_CANCELLERS:
		_grant("Hospitality Request", role, "cancel")
		_grant("Hospitality Request", role, "amend")
	for doctype, roles in LINK_TARGET_PICKERS.items():
		for role in roles:
			_grant(doctype, role, "select")
	for role in VISITOR_PASS_READERS:
		_grant("Visitor Pass", role)
	_restore_core_page_permissions()

	# Visitor Type decides which role approves which visitor — it is access
	# control expressed as data. Earlier versions granted Employee write/create,
	# which let any staff member point approver_role at a role they hold and
	# approve their own visitors (and, as a side effect, strip the real approver
	# of their lane). The doctype now ships read-only for Employee; this revokes
	# the privilege on sites that already installed the permissive version,
	# because a stored Custom DocPerm overrides what the doctype ships.
	_revoke("Visitor Type", "Employee", ("write", "create", "delete", "submit", "cancel"))


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
		doc.append("recipients", {
			"receiver_by_role": role,
			"condition": f"doc.workflow_state == {lane_for_role(role)!r}",
		})
	doc.save(ignore_permissions=True)
	added = sorted(r for r, _ in wanted - current)
	if added:
		print(f"  approval alert now also reaches: {', '.join(added)}")


def _seed_gates():
	for gate in GATES:
		if frappe.db.exists("Visitor Gate", gate["gate_name"]):
			continue
		frappe.get_doc({"doctype": "Visitor Gate", "is_active": 1, **gate}).insert(
			ignore_permissions=True
		)
		print(f"  created Visitor Gate {gate['gate_name']}")


def _seed_visitor_types():
	for spec in VISITOR_TYPES:
		name = spec["visitor_type_name"]
		if not frappe.db.exists("Visitor Type", name):
			frappe.get_doc({
				"doctype": "Visitor Type", "is_active": 1, "requires_badge": 1, **spec
			}).insert(ignore_permissions=True)
			print(f"  created Visitor Type {name}")
			continue
		# Fill only what is still blank so admin edits survive.
		updates = {
			f: v for f, v in spec.items()
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
		frappe.get_doc({"doctype": "ID Proof Type", "is_active": 1, **spec}).insert(
			ignore_permissions=True
		)
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
	updated = frappe.db.sql(
		"""
		UPDATE `tabVisitor Pass`
		SET crm_reference_type = NULL, crm_lead_opportunity = NULL
		WHERE IFNULL(visitor_type_layout, '') != 'Customer'
		  AND (IFNULL(crm_reference_type, '') != '' OR IFNULL(crm_lead_opportunity, '') != '')
		"""
	)
	if frappe.db._cursor.rowcount > 0:
		print(f"  cleared stray CRM fields on {frappe.db._cursor.rowcount} visitor pass(es)")

	# Same class of problem, different cause. `vip_category` is a Select whose
	# options began with "Board Member" rather than a blank line, so Frappe
	# assigned the first option to every pass that left the field empty — and the
	# VIP layout was absent from VisitorPass.LAYOUT_FIELDS, so unlike every other
	# layout its fields were never cleared for passes of another type. The result
	# on this site: 145 Contractor / Customer / Auditor / Supplier / Candidate
	# passes filed as "Board Member" in reports and exports, on records whose VIP
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
			"Visitor Event Log", row.name, "details",
			rewritten_text, update_modified=False,
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
	"""
	if frappe.db.get_single_value("System Settings", "allow_guests_to_upload_files"):
		return
	frappe.db.set_single_value("System Settings", "allow_guests_to_upload_files", 1)
	print("  enabled allow_guests_to_upload_files (required by the visitor portal)")


def _configure_notifications():
	for name in DISABLED_NOTIFICATIONS:
		if frappe.db.exists("Notification", name):
			frappe.db.set_value("Notification", name, "enabled", 0, update_modified=False)

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
	"""Every blacklist lookup filters on is_active; entries created under the old
	default of 0 blocked nobody."""
	if not frappe.db.table_exists("Visitor Blacklist"):
		return
	dormant = frappe.get_all("Visitor Blacklist", filters={"is_active": 0}, pluck="name")
	for name in dormant:
		frappe.db.set_value("Visitor Blacklist", name, "is_active", 1, update_modified=False)
	if dormant:
		print(f"  activated {len(dormant)} dormant blacklist entries")


def _build_workflow():
	from visitormanagement.visitor_management.workflow_builder import build_workflow

	frappe.clear_cache()
	name = build_workflow()
	if name:
		print(f"  rebuilt workflow {name}")
