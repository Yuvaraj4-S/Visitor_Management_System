"""Tell the people involved in a visit what just happened: a bell alert and an email.

The bell is a Notification Log of type "Alert", which Frappe never emails by
itself (Notification Settings skip that type), so the email sent here is the only
one and nobody gets two. Both ride along with somebody's save, so neither may
break it: the email goes after commit (mail.send_after_commit) and every failure
is logged instead of raised.
"""

import frappe
from frappe.utils import strip_html

from visitormanagement.visitor_management.lifecycle import log_failure
from visitormanagement.visitor_management.mail import send_after_commit

# Notification Log.subject is a Text field, but the bell dropdown shows one line.
_BELL_SUBJECT_LENGTH = 140


def users_of_visit(visitor_pass, exclude=()):
	"""Enabled desk users who should hear about this visit: its host and whoever raised it."""
	candidates = [
		visitor_pass.get("host_email"),
		visitor_pass.get("owner"),
		visitor_pass.get("submitted_for_approval_by"),
	]
	skip = {"Administrator", "Guest", *(exclude or ())}
	users = []
	for user in candidates:
		if not user or user in skip or user in users:
			continue
		if frappe.db.get_value("User", user, "enabled"):
			users.append(user)
	return users


def alert_users(users, subject, message_html, reference_doctype, reference_name, email=True):
	"""Bell alert for each user, plus one email to all of them.

	`users` are User names. The built-in Administrator and Guest accounts are
	nobody's inbox and are left out; everyone else is emailed at their User email.
	"""
	users = [u for u in dict.fromkeys(users or []) if u and u not in ("Administrator", "Guest")]
	if not users:
		return
	for user in users:
		try:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"for_user": user,
					"from_user": frappe.session.user if frappe.session.user != "Guest" else None,
					"type": "Alert",
					"document_type": reference_doctype,
					"document_name": reference_name,
					"subject": strip_html(subject)[:_BELL_SUBJECT_LENGTH],
					"email_content": message_html,
				}
			).insert(ignore_permissions=True)
		except Exception:
			log_failure(f"VMS Alert (bell): {reference_name}")
	if email:
		addresses = [frappe.db.get_value("User", u, "email") or (u if "@" in u else None) for u in users]
		send_email(addresses, subject, message_html, reference_doctype, reference_name)


def send_email(recipients, subject, message_html, reference_doctype, reference_name, attachments=None):
	"""One email, after commit; a missing or failing mail server is logged, never raised."""
	recipients = [r for r in dict.fromkeys(recipients or []) if r]
	if not recipients:
		return
	try:
		send_after_commit(
			recipients=recipients,
			subject=subject,
			message=message_html,
			reference_doctype=reference_doctype,
			reference_name=reference_name,
			attachments=attachments,
		)
	except Exception:
		log_failure(f"VMS Alert (email): {reference_name}")
