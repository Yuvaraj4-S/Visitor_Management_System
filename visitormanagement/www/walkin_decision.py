import frappe
from frappe import _

from visitormanagement.visitor_management.doctype.walk_in_visitor_request.walk_in_visitor_request import (
	verify_decision_token,
)

no_cache = 1


def get_context(context):
	"""Confirmation page for the Approve / Reject links in the host's email.

	Opening the link changes nothing (mail scanners prefetch links) — the host
	has to press the button on this page."""
	context.no_cache = 1
	context.title = _("Walk-in Visitor Approval")
	context.err_msg = None
	context.decision_token = (frappe.form_dict.get("token") or "").strip()
	context.decision = "reject" if frappe.form_dict.get("action") == "reject" else "approve"

	try:
		doc = verify_decision_token(context.decision_token)
	except frappe.ValidationError:
		frappe.clear_messages()
		context.err_msg = _("This link is invalid or has expired. Please open the request from your desk.")
		return

	context.request_name = doc.name
	context.req_status = doc.status
	context.rows = [
		(_("Visitor"), doc.visitor_full_name),
		(_("Company"), doc.company__organisation or "-"),
		(_("Total visitors"), doc.number_of_visitors or 1),
		(_("Purpose"), doc.purpose_of_visit or "-"),
		(_("Mobile"), doc.mobile_number or "-"),
		(_("Date"), frappe.utils.formatdate(doc.visit_date) if doc.visit_date else "-"),
	]
