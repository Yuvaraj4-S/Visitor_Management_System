import frappe

from visitormanagement.visitor_management.doctype.visitor_invitation.visitor_invitation import (
	summarise_guest_rows,
)


def execute():
	"""Visitor Invitation now holds a Visitors table (one row, link and pass per visitor).
	Move each existing single-visitor invitation into a one-row table, keeping its
	token so links already emailed keep working."""
	invitations = frappe.get_all(
		"Visitor Invitation",
		fields=[
			"name", "visitor_email", "meal_required", "invitation_status", "invitation_token",
			"portal_submission_url", "invitation_sent_on", "link_opened_on", "form_submitted_on", "visitor_pass",
		],
	)
	for inv in invitations:
		if not inv.visitor_email:
			continue
		if frappe.db.exists("Visitor Invitation Guest", {"parent": inv.name, "parenttype": "Visitor Invitation"}):
			continue

		full_name = None
		if inv.visitor_pass:
			full_name = frappe.db.get_value("Visitor Pass", inv.visitor_pass, "visitor_full_name")
		status = inv.invitation_status if inv.invitation_status != "Partially Submitted" else "Saved"

		doc = frappe.get_doc("Visitor Invitation", inv.name)
		row = doc.append(
			"visitors",
			{
				"visitor_full_name": full_name or inv.visitor_email.split("@")[0],
				"visitor_email": inv.visitor_email,
				"meal_required": inv.meal_required,
				"invitation_status": status or "Draft",
				"visitor_pass": inv.visitor_pass,
				"invitation_token": inv.invitation_token,
				"portal_submission_url": inv.portal_submission_url,
				"invitation_sent_on": inv.invitation_sent_on,
				"link_opened_on": inv.link_opened_on,
				"form_submitted_on": inv.form_submitted_on,
			},
		)
		row.db_insert()
		_status, summary = summarise_guest_rows(doc.visitors)
		frappe.db.set_value("Visitor Invitation", inv.name, "visitors_summary", summary, update_modified=False)
