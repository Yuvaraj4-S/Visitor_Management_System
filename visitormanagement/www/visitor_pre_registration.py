import frappe

WEB_FORM_ROUTE = "/visitor-pre-registration-form"


def get_context(context):
    token = (frappe.form_dict.get("token") or "").strip()
    frappe.redirect(f"{WEB_FORM_ROUTE}?token={token}" if token else WEB_FORM_ROUTE)
