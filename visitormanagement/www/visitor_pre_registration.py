from urllib.parse import quote

import frappe

# Straight to the form itself. The bare web form route answers with its own
# redirect to ".../new", and that redirect keeps nothing of the query string —
# so an old "/visitor-pre-registration?token=…" link arrived on the open form
# with its token gone.
WEB_FORM_ROUTE = "/visitor-pre-registration-form/new"


def get_context(context):
	token = (frappe.form_dict.get("token") or "").strip()
	frappe.redirect(f"{WEB_FORM_ROUTE}?token={quote(token, safe='')}" if token else WEB_FORM_ROUTE)
