import frappe
from frappe import _
from frappe.utils import today, add_days, getdate


def maybe_create_invitation(doc, method=None):
	if (doc.get("interview_mode") or "Online") != "Offline":
		return

	if frappe.db.exists("Visitor Invitation", {"reference_job_applicant": doc.name}):
		return

	if not doc.email_id:
		frappe.throw(_("Email ID is required to create a Visitor Invitation for an Offline candidate."))

	host = doc.get("interview_host")
	if not host:
		frappe.throw(_("Interview Host (Employee) is required when Interview Mode is Offline."))

	visit_date = getdate(doc.get("interview_visit_date") or add_days(today(), 1))
	if visit_date < getdate(today()):
		frappe.throw(_("Interview Visit Date cannot be in the past."))

	checkin = doc.get("interview_checkin_time") or "10:00:00"
	checkout = doc.get("interview_checkout_time") or "11:00:00"
	purpose = f"Interview - {doc.get('designation') or doc.get('job_title') or 'Open Position'}"

	inv = frappe.new_doc("Visitor Invitation")
	inv.update({
		"visitor_type": "Candidate",
		"visitor_email": doc.email_id,
		"visitor_mobile": doc.get("phone_number") or "",
		"visitor_full_name": doc.applicant_name,
		"host_employee": host,
		"visit_date": visit_date,
		"expected_checkin": checkin,
		"expected_checkout": checkout,
		"purpose_of_visit": purpose,
		"reference_job_applicant": doc.name,
	})
	inv.insert(ignore_permissions=True)

	try:
		inv.send_invitation()
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Candidate Flow: send_invitation failed")

	frappe.msgprint(
		_("Visitor Invitation {0} created and sent to {1}.").format(
			frappe.bold(inv.name), frappe.bold(doc.email_id)
		),
		alert=True,
		indicator="green",
	)
