"""Leave the site as it was before this app, when the app is removed.

`bench uninstall-app` deletes what is tagged with this app's modules — its
DocTypes and their rows, workspaces, reports, charts, number cards, the web form
and module-tagged notifications. Everything else this app put on the site
stayed behind:

  - `allow_guests_to_upload_files`, switched on site-wide for the visitor portal;
  - the Job Applicant interview fields (customisations carry no module);
  - the three workflows and two untagged notifications, pointing at DocTypes
    that no longer exist;
  - permission rows granted on HRMS/ERPNext DocTypes (Employee, Supplier, Job
    Applicant, Maintenance Visit) — which also froze those DocTypes' permissions
    as custom, so later HRMS/ERPNext permission updates stopped applying;
  - the roles it created;
  - visitors' ID scans, photos and visa copies: the File rows and the bytes on
    disk, orphaned once their passes were dropped — personal documents with no
    record left to govern who may read them;
  - assignments, pending workflow actions, comments, versions, emails and
    share/permission rows pointing at records that no longer exist;
  - its scheduled jobs (until the next migrate) and its one-time setup markers.

`before_uninstall` removes all of that. Anything the site may own too (a role or
permission row that existed before this app) is left alone.
"""

import inspect

import frappe

from visitormanagement.setup import (
	_CREATED_MARKER,
	_PORTAL_UPLOADS_SELF_ENABLED_MARKER,
	_core_link_grants,
	_shared_records,
)

APP_MODULES = ("Visitor Management", "Conference Room")
WORKFLOWS = ("Visitor Pass Approval", "Hospitality Request Approval", "Conference Room Booking Approval")

# Rows elsewhere that point at a record of one of this app's DocTypes.
REFERENCING_ROWS = (
	("File", "attached_to_doctype"),
	("ToDo", "reference_type"),
	("Workflow Action", "reference_doctype"),
	("Comment", "reference_doctype"),
	("Version", "ref_doctype"),
	("Communication", "reference_doctype"),
	("Notification Log", "document_type"),
	("DocShare", "share_doctype"),
	("User Permission", "allow"),
	("Custom DocPerm", "parent"),
	("Property Setter", "doc_type"),
	("Email Queue", "reference_doctype"),
)


def before_uninstall():
	if _is_dry_run():
		# `uninstall-app --dry-run` still calls this hook (frappe/installer.py
		# remove_app runs before_uninstall before it looks at dry_run), so a
		# rehearsal used to switch guest uploads off for the whole site.
		print("  Visitor Management: dry run — nothing on the site is changed by this app's uninstall hook.")
		return

	app_doctypes = frappe.get_all("DocType", filters={"module": ("in", APP_MODULES)}, pluck="name")

	_revert_portal_uploads_setting()
	_remove_customisations()
	_remove_workflows_and_alerts(app_doctypes)
	_restore_core_permissions()
	_remove_referencing_rows(app_doctypes)
	_remove_created_shared_records(app_doctypes)
	_remove_scheduled_jobs()
	_forget_setup_markers()


def _is_dry_run():
	for frame_info in inspect.stack():
		if frame_info.function == "remove_app" and frame_info.frame.f_globals.get("__name__") == "frappe.installer":
			return bool(frame_info.frame.f_locals.get("dry_run"))
	return False


def _revert_portal_uploads_setting():
	"""Turn `allow_guests_to_upload_files` back off — but only when this app is
	the one that turned it on, and only when it is still on now.

	`setup._PORTAL_UPLOADS_SELF_ENABLED_MARKER` is set in exactly the branch of
	`_allow_portal_uploads()` that performs the flip. A site whose setting was
	already on (or that ran a version from before the marker existed) is left as
	it is, and the administrator is told why — reverting unconditionally could
	break guest uploads another app relies on.
	"""
	if not frappe.db.get_default(_PORTAL_UPLOADS_SELF_ENABLED_MARKER):
		print(
			"  Visitor Management: allow_guests_to_upload_files was left as-is. This "
			"install has no record of having switched it on itself (it may already "
			"have been on before this app was installed, for another app or by an "
			"administrator's own choice), so it is not safe to assume this app owns "
			"turning it back off. Review System Settings -> Allow Guests to Upload "
			"Files by hand if it should be disabled now."
		)
		return

	currently_enabled = frappe.db.get_single_value("System Settings", "allow_guests_to_upload_files")
	if not currently_enabled:
		return

	frappe.db.set_single_value("System Settings", "allow_guests_to_upload_files", 0)
	print(
		"  Visitor Management: turned allow_guests_to_upload_files back off "
		"(this install had switched it on for the visitor portal)."
	)


def _shipped_custom_fields():
	"""(dt, fieldname) for every Custom Field in visitor_management/custom/*.json."""
	import glob
	import json
	import os

	fields = []
	for path in glob.glob(os.path.join(frappe.get_app_path("visitormanagement"), "*", "custom", "*.json")):
		with open(path) as f:
			spec = json.load(f)
		for field in spec.get("custom_fields", []):
			fields.append((field.get("dt") or spec.get("doctype"), field["fieldname"]))
	return fields


def _remove_customisations():
	for dt, fieldname in _shipped_custom_fields():
		name = frappe.db.get_value("Custom Field", {"dt": dt, "fieldname": fieldname}, "name")
		if name:
			# The column and any values in it stay, as with every Custom Field
			# Frappe deletes; the field is no longer part of the form.
			frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
			print(f"  removed Custom Field {name}")


def _remove_workflows_and_alerts(app_doctypes):
	for name in WORKFLOWS:
		if frappe.db.exists("Workflow", name):
			frappe.delete_doc("Workflow", name, ignore_permissions=True, force=True)
			print(f"  removed Workflow {name}")
	if app_doctypes:
		for name in frappe.get_all(
			"Notification", filters={"document_type": ("in", app_doctypes)}, pluck="name"
		):
			# ignore_on_trash, as Frappe's own module cleanup does: Notification.on_trash
			# refuses to delete a standard one, and would touch the app's files.
			frappe.delete_doc(
				"Notification", name, ignore_permissions=True, force=True, ignore_on_trash=True
			)


def _restore_core_permissions():
	"""Take back this app's rows on HRMS/ERPNext DocTypes, and un-freeze them.

	Granting a role on a DocType copies its standard permissions into Custom
	DocPerm, and from then on only the custom rows count. Once this app's own
	rows are gone, if what is left is exactly the standard set, the custom rows
	are dropped too so the DocType follows its owning app's permissions again. If
	the site customised them further, they are left as the site made them.
	"""
	from frappe.permissions import rights

	for doctype, roles in _core_link_grants().items():
		if not frappe.db.exists("DocType", doctype):
			continue
		standard = frappe.get_all("DocPerm", filters={"parent": doctype}, fields=["*"])
		standard_roles = {row.role for row in standard}
		for role in roles - standard_roles:
			for name in frappe.get_all(
				"Custom DocPerm", filters={"parent": doctype, "role": role}, pluck="name"
			):
				frappe.delete_doc("Custom DocPerm", name, ignore_permissions=True, force=True)
				print(f"  removed {role} permission on {doctype}")

		def shape(rows):
			return sorted(
				(row.role, row.permlevel or 0, row.if_owner or 0, tuple(row.get(p) or 0 for p in rights))
				for row in rows
			)

		custom = frappe.get_all("Custom DocPerm", filters={"parent": doctype}, fields=["*"])
		if custom and shape(custom) == shape(standard):
			frappe.db.delete("Custom DocPerm", {"parent": doctype})
			print(f"  {doctype} permissions follow its own app again")
		frappe.clear_cache(doctype=doctype)


def _remove_referencing_rows(app_doctypes):
	if not app_doctypes:
		return
	for doctype, fieldname in REFERENCING_ROWS:
		if not frappe.db.table_exists(doctype):
			continue
		names = frappe.get_all(doctype, filters={fieldname: ("in", app_doctypes)}, pluck="name")
		if not names:
			continue
		if doctype == "File":
			# Through the document, so the bytes on disk go too (File.on_trash
			# keeps them only while another File row still points at them).
			for name in names:
				try:
					frappe.delete_doc(
						"File", name, ignore_permissions=True, force=True, delete_permanently=True
					)
				except Exception:
					frappe.log_error(title=f"VMS uninstall: could not delete File {name}")
		else:
			for table in frappe.get_meta(doctype).get_table_fields():
				frappe.db.delete(table.options, {"parent": ("in", names), "parenttype": doctype})
			frappe.db.delete(doctype, {fieldname: ("in", app_doctypes)})
		print(f"  removed {len(names)} {doctype} row(s) of this app's records")


def _created_by_app(doctype, name):
	return bool(frappe.db.get_default(_CREATED_MARKER.format(doctype=doctype, name=name)))


def _remove_created_shared_records(app_doctypes):
	"""Roles, workflow states and actions this app created — and nothing it found.

	"HOD", "CEO", "Security", "Draft", "Approved" are names other apps and sites
	use too, so only a record this app recorded creating is a candidate, and even
	then only while nothing outside this app still uses it. A site installed by a
	version that did not record this keeps them all.
	"""
	elsewhere = {"parent": ("not in", app_doctypes or [""])}
	for role in _shared_records()["Role"]:
		if not (frappe.db.exists("Role", role) and _created_by_app("Role", role)):
			continue
		if frappe.db.exists("DocPerm", {"role": role, **elsewhere}) or frappe.db.exists(
			"Custom DocPerm", {"role": role, **elsewhere}
		):
			print(f"  kept Role {role}: another app's DocType grants it permissions")
			continue
		frappe.db.delete("Has Role", {"role": role})
		frappe.delete_doc("Role", role, ignore_permissions=True, force=True)
		print(f"  removed Role {role}")

	# This app's workflows are already gone; anything still naming a state or
	# action belongs to another workflow.
	for state in _shared_records()["Workflow State"] + _created_lane_states():
		if not (frappe.db.exists("Workflow State", state) and _created_by_app("Workflow State", state)):
			continue
		if frappe.db.exists("Workflow Document State", {"state": state}) or frappe.db.exists(
			"Workflow Transition", {"state": state}
		) or frappe.db.exists("Workflow Transition", {"next_state": state}):
			continue
		frappe.delete_doc("Workflow State", state, ignore_permissions=True, force=True)
		print(f"  removed Workflow State {state}")
	for action in _shared_records()["Workflow Action Master"]:
		if not (frappe.db.exists("Workflow Action Master", action) and _created_by_app("Workflow Action Master", action)):
			continue
		if frappe.db.exists("Workflow Transition", {"action": action}):
			continue
		frappe.delete_doc("Workflow Action Master", action, ignore_permissions=True, force=True)
		print(f"  removed Workflow Action {action}")


def _created_lane_states():
	"""The "Pending <role>" lanes the workflow builder created for approver roles."""
	prefix = _CREATED_MARKER.format(doctype="Workflow State", name="")
	return [
		row[0][len(prefix):]
		for row in frappe.db.sql("select defkey from tabDefaultValue where defkey like %s", (prefix + "%",))
	]


def _remove_scheduled_jobs():
	for name in frappe.get_all(
		"Scheduled Job Type", filters={"method": ("like", "visitormanagement.%")}, pluck="name"
	):
		frappe.delete_doc("Scheduled Job Type", name, ignore_permissions=True, force=True)


def _forget_setup_markers():
	"""So a later reinstall starts from a clean slate instead of skipping setup."""
	# Matched in Python: `\_` is an escape in MariaDB's LIKE but not in SQLite's,
	# where the same pattern matched nothing and every marker survived — so a
	# reinstall skipped its one-time setup (no guest uploads, no permissions).
	for row in frappe.get_all(
		"DefaultValue", filters={"parent": "__default", "defkey": ("like", "vms%")}, fields=["name", "defkey"]
	):
		if row.defkey.startswith("vms_"):
			frappe.db.delete("DefaultValue", {"name": row.name})
	frappe.clear_cache()
