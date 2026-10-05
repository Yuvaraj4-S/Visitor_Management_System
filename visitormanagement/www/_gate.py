import os

import frappe
from frappe import _

no_cache = 1


def get_context(context):
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/gate"
		raise frappe.Redirect
	if not frappe.has_permission("Security Log", "create"):
		frappe.throw(_("The Security Gate app is for Security staff only."), frappe.PermissionError)
	context.no_cache = 1
	bundle = os.path.join(frappe.get_app_path("visitormanagement"), "public", "gate", "gate.js")
	context.asset_version = int(os.path.getmtime(bundle)) if os.path.exists(bundle) else 0
	context.boot = frappe._dict(
		csrf_token=frappe.sessions.get_csrf_token(),
		user=frappe.session.user,
		user_fullname=frappe.utils.get_fullname(frappe.session.user),
		site_name=frappe.local.site,
	)
	return context
