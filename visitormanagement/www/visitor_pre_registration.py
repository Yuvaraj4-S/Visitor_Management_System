from urllib.parse import urlencode

import frappe

WEB_FORM_ROUTE = "/visitor-pre-registration-form"


def get_context(context):
	token = (frappe.form_dict.get("token") or "").strip()
	# Encoded so a crafted token cannot smuggle extra query parameters (or a
	# fragment) into the redirect target.
	frappe.redirect(f"{WEB_FORM_ROUTE}?{urlencode({'token': token})}" if token else WEB_FORM_ROUTE)
