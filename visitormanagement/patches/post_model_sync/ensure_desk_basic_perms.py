"""Ensure standard Employee role can read the `Page` DocType.

Frappe's desk loads `Page` metadata when rendering form views (sidebar, workflow
widget, breadcrumbs etc.). Without read access on Page, navigating to many
form/list routes raises a misleading "No permission for Page" error — even
when the underlying doctype perms are correct.

On a stock Frappe install, the `All` role typically has Page read, but on this
site only Administrator + System Manager were granted it. This patch adds a
Custom DocPerm so every desk-using Employee can load the Page metadata that
the desk needs in the background.

Idempotent: safe to re-run on every migrate.
"""
import frappe


_DESK_REQUIRED_PAGE_READS_BY_ROLE = ("Employee",)


def execute():
	for role in _DESK_REQUIRED_PAGE_READS_BY_ROLE:
		_ensure_page_read_perm(role)
	frappe.db.commit()


def _ensure_page_read_perm(role):
	if not frappe.db.exists("Role", role):
		return
	if frappe.db.exists(
		"Custom DocPerm",
		{"parent": "Page", "role": role, "permlevel": 0},
	):
		return
	doc = frappe.get_doc({
		"doctype": "Custom DocPerm",
		"parent": "Page",
		"parenttype": "DocType",
		"parentfield": "permissions",
		"role": role,
		"permlevel": 0,
		"read": 1,
	})
	doc.flags.ignore_permissions = True
	doc.insert()
