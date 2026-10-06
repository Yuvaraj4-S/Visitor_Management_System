"""Send VMS emails straight away without a mail failure breaking the request.

`frappe.sendmail(..., now=True)` does not send inside the call: it queues the
mail and registers the send as an after-commit callback
(frappe/email/__init__.py). That callback runs after the caller's try/except has
already returned, so an SMTP failure — a missing password, a server that is
down, a timeout — escaped as an HTTP 500 on a request whose data was already
committed. A guard's check-in saved, and the guard was shown a crash.

Every helper below queues the mail the normal way first. Whatever happens to the
immediate attempt, the Email Queue keeps the mail and records the error, and the
scheduler retries it (SendMailContext marks a failed send "Not Sent").
"""

import frappe
from frappe.utils import cstr, escape_html


def esc(value, default=""):
	"""A record value made safe to paste into an HTML mail body.

	Frappe's field sanitiser strips scripts and event handlers, but keeps plain
	markup — an `<a href>` or `<img>` typed into a driver name or item name
	survived, and the mail builders pasted it in raw, so staff mails carried
	links and images a user had planted. Every value from a record goes through
	here; the mail's own markup stays as written.
	"""
	if value is None or value == "":
		return default
	return escape_html(cstr(value))


def send_after_commit(on_failure=None, **kwargs):
	"""Queue a mail and send it once this request's data is committed.

	For notifications that ride along with a save (a gate check-in, an alert):
	the save must never depend on the mail server. A failed send is logged and
	left in the queue for retry. `frappe.sendmail` can still raise while queueing
	(for example no Email Account at all), so callers keep their own try/except.

	`on_failure(error_text)` runs if the send does not go through, so a caller
	whose user was already told "sent" can tell them otherwise.
	"""
	kwargs.pop("now", None)
	queue = frappe.sendmail(**kwargs)
	if queue:
		frappe.db.after_commit.add(lambda name=queue.name: _send_queued(name, on_failure))
	return queue


# How long the invitation's server check may keep the host waiting. Frappe's own
# SMTP timeout is two minutes per step, long enough for a proxy to cut the
# request off — losing the invitation link the host is waiting for.
SERVER_CHECK_TIMEOUT_SECONDS = 10


def send_checked(on_failure=None, **kwargs):
	"""Check the mail server will take a mail, then queue it and send it after commit.

	For a mail whose delivery the user is waiting to hear about (an invitation
	link the host may have to pass on by hand). Raises if the outgoing account is
	missing or misconfigured, or its SMTP server cannot be reached or refuses the
	login — and in that case queues nothing, so a host who is told "not sent" and
	sends again does not later have both copies delivered by the scheduler.

	The check logs in and out without sending and writes nothing. Sending inside
	the request is not an option: SendMailContext commits the transaction to
	update the queue row, which commits the caller's half-finished work too (it
	did — a test run's invitations stayed on the site). A send that still fails
	after a good login (a refused recipient) is retried from the queue, and
	`on_failure` lets the caller tell the user, who was already told it went.
	"""
	kwargs.pop("now", None)
	if not frappe.are_emails_muted():
		_check_outgoing_server(kwargs.get("reference_doctype"))
	return send_after_commit(on_failure=on_failure, **kwargs)


def _check_outgoing_server(reference_doctype=None):
	from frappe.email.doctype.email_account.email_account import EmailAccount
	from frappe.email.smtp import SMTPServer

	account = EmailAccount.find_outgoing(match_by_doctype=reference_doctype, _raise_error=True)
	if account.service == "Frappe Mail":
		return  # an HTTP API, not SMTP: nothing to log in to
	server = SMTPServer(**account.sendmail_config(), timeout=SERVER_CHECK_TIMEOUT_SECONDS)
	try:
		server.session  # connects and logs in
	finally:
		server.quit()


def send_in_background(**kwargs):
	"""Hand a mail to a background job straight away, outside this transaction.

	For an alert raised just before a refusal (a blacklisted visitor): the
	`frappe.throw` that follows rolls the whole request back, and a mail queued
	in it — including a `now=True` one, whose send waits for a commit that never
	comes — is rolled back with it. The alert was never sent. The job is pushed
	to Redis immediately (enqueue_after_commit=False), so it survives that rollback.
	"""
	kwargs.pop("now", None)
	frappe.enqueue(
		"visitormanagement.visitor_management.mail._send_from_job",
		queue="short",
		enqueue_after_commit=False,
		mail=kwargs,
	)


def _send_from_job(mail):
	send_after_commit(**mail)


def _send_queued(queue_name, on_failure=None):
	error = None
	try:
		queue = frappe.get_doc("Email Queue", queue_name)
		if not queue.can_send_now():
			return  # emails muted or held on this site: nothing was attempted, nothing failed
		queue.send()
		# A refused recipient is recorded on the row ("Partially Sent" / "Error")
		# without raising, so the outcome is read back rather than assumed.
		status, error = frappe.db.get_value("Email Queue", queue_name, ["status", "error"])
		if status == "Sent":
			return
	except Exception as exc:
		error = str(exc)
		# The request is already answered; defer the insert so logging cannot fail it either.
		frappe.log_error(
			title="VMS: email not sent immediately (kept in Email Queue for retry)",
			reference_doctype="Email Queue",
			reference_name=queue_name,
			defer_insert=True,
		)
	if not on_failure:
		return
	lines = (error or "").strip().splitlines()
	try:
		on_failure(lines[-1] if lines else "")
		frappe.db.commit()  # after-commit callback: nothing else will commit this
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="VMS: could not report an undelivered email", defer_insert=True)
