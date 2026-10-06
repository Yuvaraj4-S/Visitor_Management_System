import frappe
from frappe import _
from frappe.utils import add_days, get_link_to_form, getdate, today

from visitormanagement.visitor_management.doctype.visitor_invitation.visitor_invitation import (
	values_differ,
)

# Visitor Type is a customer-nameable master (see security_log.get_approved_vip_queue,
# which resolves "VIP" the same way): a site is free to rename or deactivate the
# "Candidate" record, so this module must never key off that literal name. It keys
# off the fixed Detail Layout Select option instead.
CANDIDATE_DETAIL_LAYOUT = "Candidate"

# What HR schedules on the Job Applicant, and where each lands. The same pairs
# hold for the invitation and for the Visitor Pass the candidate registers.
INVITATION_SCHEDULE_FIELDS = {
	"custom_interview_host": "host_employee",
	"custom_interview_visit_date": "visit_date",
	"custom_interview_checkin_time": "expected_checkin",
	"custom_interview_checkout_time": "expected_checkout",
}
PASS_SCHEDULE_FIELDS = {**INVITATION_SCHEDULE_FIELDS, "custom_interview_host": "person_to_visit"}

# Invitations HR's reschedule can no longer be applied to.
CLOSED_STATUSES = ("Submitted", "Cancelled")


def maybe_create_invitation(doc, method=None):
	"""Job Applicant after_insert/on_update hook (see hooks.py doc_events).

	Job Applicant is an HRMS core doctype. Creating a Visitor Invitation here is a
	convenience on top of the candidate's real record — it must never be able to
	block that record's save. Every failure, expected or not, is logged and
	swallowed rather than raised, including a misconfigured or missing "Candidate"
	Visitor Type.
	"""
	try:
		_maybe_create_invitation(doc, method)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Candidate Flow: maybe_create_invitation failed")


def _maybe_create_invitation(doc, method=None):
	if (doc.get("custom_interview_mode") or "Online") != "Offline":
		return

	existing = frappe.db.get_value(
		"Visitor Invitation", {"reference_job_applicant": doc.name}, "name", order_by="creation desc"
	)
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

	if _send(inv):
		frappe.msgprint(
			_("Visitor Invitation {0} created and sent to {1}.").format(
				frappe.bold(inv.name), frappe.bold(doc.email_id)
			),
			alert=True,
			indicator="green",
		)
	else:
		frappe.msgprint(
			_(
				"Visitor Invitation {0} was created, but the email to {1} could not be sent. "
				"Open the invitation and use Copy Invitation Link to send the link yourself."
			).format(get_link_to_form("Visitor Invitation", inv.name), frappe.bold(doc.email_id)),
			title=_("Invitation Not Sent"),
			indicator="orange",
		)


def _send(inv):
	"""Mail the invitation; True when the mail went out.

	Called as the HR user saving the applicant, who needs no rights of their own
	on the invitation: this is the flow's send, not a desk action.
	"""
	try:
		return bool(inv.issue_link_and_send().get("delivered"))
	except Exception:
		frappe.log_error(frappe.get_traceback(), "Candidate Flow: send_invitation failed")
		return False


def _schedule_changes(doc, target, fields):
	"""The applicant's interview schedule where it differs from `target`.

	Only what HR actually filled in counts: a blank date or time on the applicant
	was defaulted when the invitation was raised and is not a reschedule.
	"""
	return {
		fieldname: doc.get(source)
		for source, fieldname in fields.items()
		if doc.get(source) and values_differ(target.get(fieldname), doc.get(source))
	}


def _follow_reschedule(doc, inv):
	"""Carry a rescheduled interview over to the invitation already raised.

	This used to stop at "an invitation exists", so HR moved the interview on the
	Job Applicant and the candidate's invitation — and the pass made from it, and
	the gate's expectation — kept the old date, with nobody told.
	"""
	if inv.invitation_status in CLOSED_STATUSES:
		_warn_schedule_not_applied(doc, inv)
		return

	changes = _schedule_changes(doc, inv, INVITATION_SCHEDULE_FIELDS)
	if not changes:
		return

	inv.update(changes)
	try:
		inv.save(ignore_permissions=True)
	except frappe.ValidationError as exc:
		# Replace the bare validation message with one that says what it is about.
		frappe.clear_messages()
		frappe.msgprint(
			_(
				"Visitor Invitation {0} could not be moved to the new interview schedule: {1} "
				"Please correct the invitation yourself."
			).format(get_link_to_form("Visitor Invitation", inv.name), str(exc)),
			title=_("Invitation Not Updated"),
			indicator="orange",
		)
		return

	invitation = get_link_to_form("Visitor Invitation", inv.name)
	if _send(inv):
		frappe.msgprint(
			_(
				"Visitor Invitation {0} now follows the new interview schedule, and the "
				"candidate has been sent the updated invitation at {1}."
			).format(invitation, frappe.bold(inv.visitor_email)),
			alert=True,
			indicator="green",
		)
	else:
		frappe.msgprint(
			_(
				"Visitor Invitation {0} now follows the new interview schedule, but the email to "
				"{1} could not be sent. Please tell the candidate about the change."
			).format(invitation, frappe.bold(inv.visitor_email)),
			title=_("Candidate Not Notified"),
			indicator="orange",
		)


def _warn_schedule_not_applied(doc, inv):
	"""Tell HR when a reschedule cannot reach an invitation that is finished with."""
	invitation = get_link_to_form("Visitor Invitation", inv.name)

	if inv.invitation_status == "Cancelled":
		if _schedule_changes(doc, inv, INVITATION_SCHEDULE_FIELDS):
			frappe.msgprint(
				_(
					"Visitor Invitation {0} was cancelled, so the interview schedule was not "
					"applied to it. Raise a new Visitor Invitation for the candidate if the "
					"interview is going ahead."
				).format(invitation),
				title=_("Invitation Not Updated"),
				indicator="orange",
			)
		return

	# Submitted: the candidate has registered, and the visit now lives on the
	# Visitor Pass. That pass belongs to the host and its approval lane, so it is
	# not rewritten from here — say what has to be changed, until it has been.
	pass_name = inv.visitor_pass if frappe.db.exists("Visitor Pass", inv.visitor_pass or "") else None
	target = frappe.get_doc("Visitor Pass", pass_name) if pass_name else inv
	fields = PASS_SCHEDULE_FIELDS if pass_name else INVITATION_SCHEDULE_FIELDS
	if not _schedule_changes(doc, target, fields):
		return

	if pass_name:
		message = _(
			"The candidate has already registered with Visitor Invitation {0}, so the new "
			"interview schedule was not applied. Ask the host to change the visit date and "
			"times on Visitor Pass {1}."
		).format(invitation, get_link_to_form("Visitor Pass", pass_name))
	else:
		message = _(
			"The candidate has already registered with Visitor Invitation {0}, so the new "
			"interview schedule was not applied. Ask the host to change the visitor's pass."
		).format(invitation)
	frappe.msgprint(message, title=_("Visitor Pass Not Updated"), indicator="orange")


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
		frappe.log_error(
			f"Multiple active Visitor Types have Detail Layout '{CANDIDATE_DETAIL_LAYOUT}': "
			f"{types}. Using {types[0]!r} for candidate invitations.",
			"Candidate Flow: ambiguous Candidate Visitor Type",
		)
	return types[0]
