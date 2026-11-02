"""Host check-out control.

The host (Person to Visit) confirms that a visitor is leaving. The visitor then has a
grace time to reach the security gate. If they do not, Security is alerted and cannot
check the visitor out until the host reconfirms.

The grace time defaults to VMS Settings > Exit Grace Time and the host can change it
each time they confirm (within MAX_GRACE_MINUTES).

State lives on the Visitor Pass (`exit_control_status`):
    ""              no confirmation pending
    "Awaiting Exit" host confirmed, visitor is on the way to the gate
    "Exit Overdue"  grace time passed, the exit is held until the host reconfirms
"""

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, now_datetime

from visitormanagement.visitor_management.lifecycle import log_visitor_event, notify_users, security_users
from visitormanagement.visitor_management.time_utils import format_time_hhmm

DEFAULT_GRACE_MINUTES = 15
MAX_GRACE_MINUTES = 480
AWAITING = "Awaiting Exit"
OVERDUE = "Exit Overdue"


def _has_control_fields():
	"""The columns only exist after `bench migrate`; the gate must keep working before it."""
	return frappe.get_meta("Visitor Pass").has_field("exit_control_status")


def is_enabled():
	# get_single_value reports 0 for a Check that was never saved; on a site that predates
	# this setting "never saved" must mean the default (on), so read the stored row itself.
	rows = frappe.db.sql(
		"select value from `tabSingles` where doctype = 'VMS Settings' and field = 'enable_host_checkout_control'"
	)
	return True if not rows else bool(cint(rows[0][0]))


def default_grace_minutes():
	value = cint(frappe.db.get_single_value("VMS Settings", "exit_grace_minutes"))
	return value if value > 0 else DEFAULT_GRACE_MINUTES


def _resolve_grace(minutes):
	"""The host's chosen grace time, or the settings default when they left it blank."""
	if minutes in (None, ""):
		return default_grace_minutes()
	minutes = cint(minutes)
	if not 1 <= minutes <= MAX_GRACE_MINUTES:
		frappe.throw(
			_("Grace time must be between 1 and {0} minutes.").format(MAX_GRACE_MINUTES), title=_("Invalid Grace Time")
		)
	return minutes


def exit_hold_message(pass_name):
	"""Why Security must not check this visitor out, or None when the exit is not held."""
	if not pass_name or not _has_control_fields():
		return None
	if frappe.db.get_value("Visitor Pass", pass_name, "exit_control_status") != OVERDUE:
		return None
	host = frappe.db.get_value("Visitor Pass", pass_name, "person_to_visit")
	host_name = frappe.db.get_value("Employee", host, "employee_name") if host else None
	return _(
		"Exit on hold: the visitor did not reach the gate in time after the host confirmed check-out. "
		"The host ({0}) must reconfirm before the visitor can leave."
	).format(host_name or host or "-")


def latest_host_messages(pass_names):
	"""{pass: the host's most recent reason (confirm / reconfirm / cancel)} in one query."""
	pass_names = [n for n in pass_names if n]
	if not pass_names or not _has_control_fields():
		return {}
	rows = frappe.get_all(
		"Host Checkout Confirmation",
		filters={
			"parent": ["in", pass_names],
			"parenttype": "Visitor Pass",
			"action": ["in", ["Confirmed", "Reconfirmed", "Cancelled"]],
		},
		fields=["parent", "remarks"],
		order_by="actioned_on desc, creation desc",
	)
	latest = {}
	for r in rows:
		latest.setdefault(r.parent, (r.remarks or "").strip() or None)  # first seen = newest
	return latest


def latest_host_message(pass_name):
	"""The host's most recent reason (confirm / reconfirm / cancel), or None."""
	return latest_host_messages([pass_name]).get(pass_name)


def clear_exit_control(pass_name):
	"""Drop any pending confirmation (the visitor has left, or the host changed their mind)."""
	if not _has_control_fields():
		return
	frappe.db.set_value(
		"Visitor Pass",
		pass_name,
		{"exit_control_status": "", "exit_deadline": None},
		update_modified=False,
	)


def _host_only(doc):
	from visitormanagement.visitor_management.doctype.visitor_pass.visitor_pass import _is_pass_host

	if not _is_pass_host(doc):
		frappe.throw(
			_("Only the host ({0}) can confirm this visitor's check-out.").format(doc.person_to_visit or "-"),
			frappe.PermissionError,
		)


def _add_history(doc, action, deadline=None, remarks=None):
	row = doc.append(
		"host_checkout_confirmations",
		{
			"action": action,
			"actioned_by": frappe.session.user,
			"actioned_on": now_datetime(),
			"exit_deadline": deadline,
			"remarks": remarks,
		},
	)
	row.db_insert()
	return row


def checkout_control_state(doc, is_host):
	"""What the Visitor Pass form should offer: ``doc`` needs docstatus, status and the exit fields."""
	eligible = bool(
		is_host and _has_control_fields() and is_enabled() and doc.get("docstatus") == 1 and doc.get("status") == "Checked-In"
	)
	return {
		"can_confirm": eligible,
		"status": doc.get("exit_control_status") or "",
		"exit_deadline": doc.get("exit_deadline"),
		"grace_minutes": default_grace_minutes(),
		"max_grace_minutes": MAX_GRACE_MINUTES,
	}


@frappe.whitelist()
def get_checkout_control_state(visitor_pass):
	"""Same state as the form's onload, for callers that need a fresh read."""
	from visitormanagement.visitor_management.doctype.visitor_pass.visitor_pass import _is_pass_host

	if not frappe.db.exists("Visitor Pass", visitor_pass):
		frappe.throw(_("Visitor Pass {0} not found.").format(visitor_pass), frappe.DoesNotExistError)
	frappe.has_permission("Visitor Pass", "read", doc=visitor_pass, throw=True)
	doc = frappe.db.get_value(
		"Visitor Pass",
		visitor_pass,
		["docstatus", "status", "person_to_visit", "exit_control_status", "exit_deadline"],
		as_dict=True,
	)
	return checkout_control_state(doc, _is_pass_host(doc))


@frappe.whitelist()
def confirm_host_checkout(visitor_pass, grace_minutes=None, remarks=None):
	"""Host confirms the visitor is leaving (or reconfirms after an overdue hold)."""
	doc = frappe.get_doc("Visitor Pass", visitor_pass)
	_host_only(doc)
	if not is_enabled():
		frappe.throw(_("Host check-out control is switched off."), title=_("Not Available"))
	if doc.docstatus != 1 or doc.status != "Checked-In":
		frappe.throw(
			_("Only a visitor who is currently checked in can be confirmed for check-out. Current status: {0}").format(
				doc.status
			),
			title=_("Cannot Confirm"),
		)

	minutes = _resolve_grace(grace_minutes)
	remarks = (remarks or "").strip() or None
	again = doc.get("exit_control_status") in (AWAITING, OVERDUE)
	was_overdue = doc.get("exit_control_status") == OVERDUE
	if was_overdue and not remarks:
		frappe.throw(
			_("Enter the reason for reconfirming: Security needs it to release the held exit."), title=_("Reason Required")
		)
	now = now_datetime()
	deadline = add_to_date(now, minutes=minutes)

	row = _add_history(doc, "Reconfirmed" if again else "Confirmed", deadline, remarks)
	doc.db_set(
		{
			"exit_control_status": AWAITING,
			"host_checkout_confirmed_on": now,
			"host_checkout_confirmed_by": frappe.session.user,
			"exit_deadline": deadline,
			"host_checkout_reconfirm_count": cint(doc.get("host_checkout_reconfirm_count")) + (1 if again else 0),
		}
	)
	doc.add_comment(
		"Info",
		_("Host {0} check-out; visitor must reach the gate within {1} min (by {2}).").format(
			_("reconfirmed") if again else _("confirmed"), minutes, frappe.utils.format_datetime(deadline, "HH:mm")
		),
	)
	log_visitor_event(
		doc.name,
		"Host Check-out Reconfirmed" if again else "Host Check-out Confirmed",
		event_status="Recorded",
		source_doctype="Host Checkout Confirmation",
		source_name=row.name,
		details={"exit_deadline": str(deadline), "grace_minutes": minutes, "remarks": remarks or "", "by": frappe.session.user},
	)
	_notify_security_confirmed(doc, deadline, was_overdue, remarks)
	return {"status": AWAITING, "exit_deadline": deadline}


@frappe.whitelist()
def cancel_host_checkout(visitor_pass, remarks=None):
	"""Host says the visitor is still with them: drop the pending confirmation."""
	doc = frappe.get_doc("Visitor Pass", visitor_pass)
	_host_only(doc)
	if doc.get("exit_control_status") not in (AWAITING, OVERDUE):
		frappe.throw(_("There is no pending check-out confirmation on this pass."), title=_("Nothing to Cancel"))

	remarks = (remarks or "").strip()
	if not remarks:
		frappe.throw(_("Enter the reason for cancelling the check-out confirmation."), title=_("Reason Required"))
	was_overdue = doc.get("exit_control_status") == OVERDUE
	row = _add_history(doc, "Cancelled", None, remarks)
	doc.db_set({"exit_control_status": "", "exit_deadline": None})
	doc.add_comment("Info", _("Host cancelled the check-out confirmation; the visitor is still on site."))
	log_visitor_event(
		doc.name,
		"Host Check-out Cancelled",
		event_status="Recorded",
		source_doctype="Host Checkout Confirmation",
		source_name=row.name,
		details={"remarks": remarks or "", "by": frappe.session.user},
	)
	if was_overdue:
		_notify_security(
			doc,
			_("Exit hold lifted: {0} ({1}) is still with the host").format(
				frappe.utils.escape_html(doc.visitor_full_name or ""), doc.name
			),
			_("The host cancelled the check-out confirmation. The visitor remains checked in."),
			remarks,
		)
	return {"status": ""}


def mark_exit_overdue(pass_name):
	"""Scheduler: the visitor did not reach the gate in time. Hold the exit and alert.

	Re-checks under a row lock: the host may have reconfirmed, cancelled or the visitor may have
	left since the scheduler listed this pass. Returns True when the pass was put on hold."""
	current = frappe.db.get_value(
		"Visitor Pass", pass_name, ["status", "exit_control_status", "exit_deadline"], as_dict=True, for_update=True
	)
	if (
		not current
		or current.status != "Checked-In"
		or current.exit_control_status != AWAITING
		or not current.exit_deadline
		or current.exit_deadline >= now_datetime()
	):
		return False
	doc = frappe.get_doc("Visitor Pass", pass_name)
	deadline = doc.get("exit_deadline")
	row = _add_history(doc, OVERDUE, deadline, None)
	doc.db_set("exit_control_status", OVERDUE)
	log_visitor_event(
		doc.name,
		"Exit Overdue",
		event_status="Auto-flagged",
		source_doctype="Host Checkout Confirmation",
		source_name=row.name,
		details={"exit_deadline": str(deadline or "")},
	)

	esc = frappe.utils.escape_html
	_notify_security(
		doc,
		_("EXIT OVERDUE: {0} ({1}) has not reached the gate").format(esc(doc.visitor_full_name or ""), doc.name),
		_(
			"The host confirmed this visitor's check-out but the visitor did not reach the gate by {0}. "
			"<b>Do not let the visitor exit</b> until the host reconfirms."
		).format(frappe.utils.format_datetime(deadline, "HH:mm")),
		latest_host_message(doc.name),
	)
	# The host is the only one who can lift the hold, so tell them too.
	host_user = frappe.db.get_value("Employee", doc.person_to_visit, "user_id") if doc.person_to_visit else None
	if host_user:
		notify_users(
			[host_user],
			doc,
			_("Reconfirm check-out: {0} ({1}) has not reached the gate").format(esc(doc.visitor_full_name or ""), doc.name),
			_(
				"You confirmed this visitor's check-out but they have not reached the security gate. "
				"Security will hold the exit until you open the pass and choose <b>Reconfirm Check-out</b>, "
				"or <b>Cancel Check-out</b> if the visitor is still with you."
			),
		)
	return True


def flag_exit_overdue_passes():
	"""Scheduler (every few minutes): hold the exit of every confirmed visitor past their deadline."""
	if not _has_control_fields() or not is_enabled():
		return
	names = frappe.get_all(
		"Visitor Pass",
		filters={
			"docstatus": 1,
			"status": "Checked-In",
			"exit_control_status": AWAITING,
			"exit_deadline": ["<", now_datetime()],
		},
		pluck="name",
	)
	for name in names:
		try:
			mark_exit_overdue(name)
		except Exception:
			frappe.log_error(frappe.get_traceback(), f"flag_exit_overdue_passes {name}")


def _notify_security_confirmed(doc, deadline, was_overdue, remarks=None):
	esc = frappe.utils.escape_html
	when = frappe.utils.format_datetime(deadline, "HH:mm")
	if was_overdue:
		subject = _("Exit hold lifted: {0} ({1})").format(esc(doc.visitor_full_name or ""), doc.name)
		message = _("The host reconfirmed check-out. The visitor may now be checked out; expected at the gate by {0}.").format(when)
	else:
		subject = _("Visitor leaving: {0} ({1}) expected at the gate by {2}").format(
			esc(doc.visitor_full_name or ""), doc.name, when
		)
		message = _("The host confirmed this visitor's check-out. Expect them at the gate by {0}.").format(when)
	_notify_security(doc, subject, message, remarks)


def _notify_security(doc, subject, message, remarks=None):
	users = security_users()
	if not users:
		return
	esc = frappe.utils.escape_html
	host_name = frappe.db.get_value("Employee", doc.person_to_visit, "employee_name") or doc.person_to_visit
	body = (
		f"<p>{message}</p><ul>"
		f"<li><b>Visitor:</b> {esc(doc.visitor_full_name or '-')} ({doc.name})</li>"
		f"<li><b>Host:</b> {esc(host_name or '-')}</li>"
		f"<li><b>Expected Check-Out:</b> {format_time_hhmm(doc.expected_checkout, '-')}</li>"
		+ (f"<li><b>Host's message:</b> {esc(remarks)}</li>" if remarks else "")
		+ "</ul>"
	)
	notify_users(users, doc, subject, body)
