import contextlib

import frappe
from frappe import _
from frappe.utils import add_days, getdate, today

from visitormanagement.visitor_management.lifecycle import log_failure

# Visitor Type is a customer-nameable master (see security_log.get_approved_vip_queue,
# which resolves "VIP" the same way): a site is free to rename or deactivate the
# "Candidate" record, so this module must never key off that literal name. It keys
# off the fixed Detail Layout Select option instead.
CANDIDATE_DETAIL_LAYOUT = "Candidate"


def maybe_create_invitation(doc, method=None):
	"""Job Applicant after_insert/on_update hook (see hooks.py doc_events).

	Job Applicant is an HRMS core doctype. Creating a Visitor Invitation here is a
	convenience on top of the candidate's real record — it must never be able to
	block that record's save. Every failure, expected or not, is logged and
	swallowed rather than raised, including a misconfigured or missing "Candidate"
	Visitor Type.

	A swallowed failure must also leave nothing behind for the HRMS user: no
	half-written invitation (rolled back to a savepoint) and no red dialog —
	`frappe.throw` queues its message before raising, so the message log is put
	back as it was, as the app does elsewhere (e.g. lifecycle.py).
	"""
	if (doc.get("custom_interview_mode") or "Online") != "Offline":
		return  # the common case for HRMS users: nothing of this app's to do

	messages_before = list(frappe.message_log)
	frappe.db.savepoint(_SAVEPOINT)
	try:
		_maybe_create_invitation(doc, method)
	except Exception:
		# Suppressed when there is nothing to roll back to (a commit in between);
		# the log below still records the failure.
		with contextlib.suppress(Exception):
			frappe.db.rollback(save_point=_SAVEPOINT)
		frappe.local.message_log = messages_before
		log_failure("Candidate Flow: maybe_create_invitation failed")


_SAVEPOINT = "vms_candidate_flow"


def _maybe_create_invitation(doc, method=None):
	if (doc.get("custom_interview_mode") or "Online") != "Offline":
		return

	existing = frappe.db.get_value("Visitor Invitation", {"reference_job_applicant": doc.name}, "name")
	if existing:
		_follow_reschedule(doc, frappe.get_doc("Visitor Invitation", existing))
		return

	if not doc.email_id:
		frappe.throw(_("Email ID is required to create a Visitor Invitation for an Offline candidate."))

	host = doc.get("custom_interview_host")
	if not host:
		frappe.throw(_("Interview Host (Employee) is required when Interview Mode is Offline."))

	visit_date = getdate(doc.get("custom_interview_visit_date") or add_days(today(), 1))
	if visit_date < getdate(today()):
		frappe.throw(_("Interview Visit Date cannot be in the past."))

	visitor_type = _resolve_candidate_visitor_type()
	if not visitor_type:
		frappe.throw(
			_("No active Visitor Type is configured with Detail Layout '{0}'.").format(
				CANDIDATE_DETAIL_LAYOUT
			)
		)

	checkin = doc.get("custom_interview_checkin_time") or "10:00:00"
	checkout = doc.get("custom_interview_checkout_time") or "11:00:00"
	purpose = f"Interview - {doc.get('designation') or doc.get('job_title') or 'Open Position'}"

	inv = frappe.new_doc("Visitor Invitation")
	inv.update(
		{
			"visitor_type": visitor_type,
			"visitor_email": doc.email_id,
			"visitor_mobile": doc.get("phone_number") or "",
			"visitor_full_name": doc.applicant_name,
			"host_employee": host,
			"visit_date": visit_date,
			"expected_checkin": checkin,
			"expected_checkout": checkout,
			"purpose_of_visit": purpose,
			"reference_job_applicant": doc.name,
		}
	)
	inv.insert(ignore_permissions=True)

	try:
		inv.send_invitation()
	except Exception:
		log_failure("Candidate Flow: send_invitation failed")

	frappe.msgprint(
		_("Visitor Invitation {0} created and sent to {1}.").format(
			frappe.bold(inv.name), frappe.bold(doc.email_id)
		),
		alert=True,
		indicator="green",
	)


def _resolve_candidate_visitor_type():
	"""Active Visitor Type whose Detail Layout is 'Candidate' — never a literal name.

	- None active: returns None. The caller throws, which this module's outer
	  wrapper catches and logs — so a Job Applicant save still succeeds, only the
	  invitation is skipped.
	- More than one active: picks the first by name (creation order) and logs a
	  warning naming all of them, so the ambiguity is traceable instead of picking
	  silently. This is a misconfiguration the customer should resolve — most
	  sites will have exactly one.
	"""
	types = frappe.get_all(
		"Visitor Type",
		filters={"detail_layout": CANDIDATE_DETAIL_LAYOUT, "is_active": 1},
		pluck="name",
		order_by="name asc",
	)
	if not types:
		return None
	if len(types) > 1:
		log_failure(
			"Candidate Flow: ambiguous Candidate Visitor Type",
			f"Multiple active Visitor Types have Detail Layout '{CANDIDATE_DETAIL_LAYOUT}': "
			f"{types}. Using {types[0]!r} for candidate invitations.",
		)
	return types[0]


# What an interview change carries over to the candidate's invitation.
_INTERVIEW_TO_INVITATION = (
	("custom_interview_visit_date", "visit_date"),
	("custom_interview_checkin_time", "expected_checkin"),
	("custom_interview_checkout_time", "expected_checkout"),
	("custom_interview_host", "host_employee"),
)
_CLOSED_INVITATION_STATUSES = ("Submitted", "Expired", "Cancelled")


def _follow_reschedule(doc, inv):
	"""Carry a rescheduled interview over to the invitation already raised for it.

	This used to stop at "an invitation exists": HR moved the interview on the
	Job Applicant, and the candidate's invitation — and the pass made from it,
	and what the gate expects — kept the old date, with nobody told. An open
	invitation now moves and is sent again; one the candidate has already used
	(or that is cancelled or expired) is left as it is and HR is told.
	"""
	changes = {}
	for source, target in _INTERVIEW_TO_INVITATION:
		value = doc.get(source)
		if value and str(value) != str(inv.get(target) or ""):
			if target == "visit_date" and getdate(value) == getdate(inv.visit_date):
				continue
			changes[target] = value
	if not changes:
		return
	link = frappe.utils.get_link_to_form("Visitor Invitation", inv.name)
	if inv.invitation_status in _CLOSED_INVITATION_STATUSES:
		frappe.msgprint(
			_(
				"The interview schedule changed, but Visitor Invitation {0} is already {1}, so it was not "
				"updated. Update the candidate's visit yourself."
			).format(link, _(inv.invitation_status)),
			title=_("Invitation Not Updated"),
			indicator="orange",
		)
		return
	inv.update(changes)
	try:
		inv.save(ignore_permissions=True)
	except frappe.ValidationError as exc:
		frappe.clear_messages()
		frappe.msgprint(
			_(
				"Visitor Invitation {0} could not be moved to the new interview schedule: {1} "
				"Please correct the invitation yourself."
			).format(link, frappe.utils.strip_html(str(exc))),
			title=_("Invitation Not Updated"),
			indicator="orange",
		)
		return
	try:
		inv.send_invitation()
		frappe.msgprint(
			_(
				"Visitor Invitation {0} now follows the new interview schedule and was sent again to {1}."
			).format(link, frappe.bold(inv.visitor_email)),
			alert=True,
			indicator="green",
		)
	except Exception:
		log_failure("Candidate Flow: resend after reschedule failed")
		frappe.msgprint(
			_(
				"Visitor Invitation {0} now follows the new interview schedule, but the email to {1} could "
				"not be sent. Please tell the candidate about the change."
			).format(link, frappe.bold(inv.visitor_email)),
			title=_("Candidate Not Notified"),
			indicator="orange",
		)
