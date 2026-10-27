# For license information, please see license.txt

import re
from datetime import timedelta

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, cstr, date_diff, get_datetime, get_link_to_form, get_time, getdate, nowdate

from visitormanagement.permissions import HOSPITALITY_OVERSEERS, _employee_for_user
from visitormanagement.visitor_management.lifecycle import (
	PASS_CALLED_OFF_STATUSES,
	PASS_CONFIRMED_STATUSES,
	assert_submitted_through_approval,
	populate_hospitality_request_from_pass,
	sync_hospitality_to_pass,
)
from visitormanagement.visitor_management.mail import esc, send_after_commit
from visitormanagement.visitor_management.settings import feature_on

# Every service a request can carry, in the order people read them:
# (key, its progress field, label, the flags that ask for it). Each service has
# its own progress, Pending -> Booked (arranged; the host is told) -> Done
# (delivered), or Cancelled, and is updated by the people who deliver it
# (`update_service_status`). The request's own `status` follows from them
# (`_derive_status_from_services`).
SERVICES = (
	("meal", "meal_status", "Meal", ("meal_required",)),
	("refreshments", "refreshments_status", "Refreshments", ("snacks_required", "tea_coffee_required")),
	("cab", "cab_status", "Cab", ("cab_required",)),
	("hotel", "hotel_status", "Hotel", ("hotel_required",)),
	("tour", "tour_status", "Factory Tour", ("factory_tour_required",)),
	("buggy", "buggy_status", "Buggy", ("buggy_required",)),
	("greeting", "greeting_status", "Greeting", ("greeting_required",)),
)
SERVICE_STATUS_FIELD = {key: status for key, status, _label, _flags in SERVICES}
SERVICE_STATUSES = ("Pending", "Booked", "Done", "Cancelled")
ARRANGED = ("Booked", "Done")

# What has to be written down before a service can be called Booked: what the
# people using it cannot do without. (fieldname, the cab types it applies to —
# None for all.)
DETAILS_TO_BOOK = {
	"meal": (("meal_type", None),),
	"cab": (
		("driver_name", None),
		("driver_phone", None),
		("pickup_datetime", ("Pickup", "Both")),
		("drop_datetime", ("Drop", "Both")),
	),
	"hotel": (("hotel_name", None), ("check_in", None), ("check_out", None)),
	"tour": (("tour_guide", None), ("tour_start_time", None)),
	"buggy": (("buggy_number", None), ("buggy_datetime", None)),
	"greeting": (("greeting_type", None), ("greeting_delivery_time", None)),
}

# The booking details the person updating a service may record in the same
# click (all "Allow on Submit"). Nothing else on the request is theirs to change.
SERVICE_DETAIL_FIELDS = {
	"cab": ("cab_vendor", "driver_name", "driver_phone"),
	"hotel": ("hotel_name", "booking_reference", "hotel_cost"),
	"tour": ("tour_guide",),
	"buggy": ("buggy_number", "buggy_driver"),
	"greeting": ("greeting_assigned_to", "greeting_cost"),
}

# Who delivers each service, by role — besides the Hospitality Manager, who
# updates every one. Each role updates only services it can already read
# (permissions.HOSPITALITY_SERVICE_ROLES). Hospitality User reads every request
# but delivers what has no team of its own — the food and the hotel desk; Front
# Office Executive only reads.
SERVICE_UPDATER_ROLES = {
	"meal": ("Hospitality User",),
	"refreshments": ("Hospitality User",),
	"cab": ("Transport Coordinator",),
	"hotel": ("Hospitality User",),
	"tour": ("Factory Tour Coordinator",),
	"buggy": ("Transport Coordinator",),
	"greeting": ("Greeting Staff",),
}
# The member of staff the request names for a service may update it too. Frappe's
# "Assigned To" (the sidebar) is deliberately not one of these: anyone who can
# read a document can assign it, so it cannot be what grants the right.
SERVICE_ASSIGNEE_FIELDS = {
	"meal": ("assigned_staff",),
	"refreshments": ("assigned_staff",),
	"tour": ("tour_guide",),
	"buggy": ("buggy_driver",),
	"greeting": ("greeting_assigned_to",),
}
# The services the visitor is emailed about when they are booked: the two they
# act on themselves (be at the pickup point, go to the hotel).
VISITOR_TOLD_SERVICES = ("cab", "hotel")

# What the Hospitality Manager records about delivery. Everything else on the
# request is the host's ask.
FULFILMENT_FIELDS = ("assigned_staff", "notes")

# Fields each requested service needs before the request can be approved. The
# form marks the same ones mandatory (`sync_reqd_flags` in hospitality_request.js).
REQUIRED_FOR_APPROVAL = {
	"cab_required": ("cab_type",),
	"hotel_required": ("check_in", "check_out"),
	"factory_tour_required": ("tour_guide",),
	"buggy_required": ("buggy_pickup_point", "buggy_datetime"),
	"greeting_required": ("greeting_type", "greeting_delivery_time"),
}
CAB_PICKUP_FIELDS = ("pickup_location", "pickup_datetime")
CAB_DROP_FIELDS = ("drop_location", "drop_datetime")

# How long one buggy run keeps a buggy busy. The request records only when the
# buggy is wanted, not for how long, so two requests clash when their pickups
# are closer together than one run: a buggy cannot be at the main gate at 10:00
# and at the plant at 10:10. Half an hour covers a run across a site and back.
BUGGY_RUN_MINUTES = 30


def _is_hospitality_manager(user=None):
	user = user or frappe.session.user
	return user == "Administrator" or bool(set(HOSPITALITY_OVERSEERS) & set(frappe.get_roles(user)))


def _blank(value):
	"""True for an empty value, including a Text Editor's empty markup."""
	text = cstr(value).strip()
	if not text:
		return True
	return "<img" not in text and not frappe.utils.strip_html(text).strip()


# ---------------------------------------------------------------------------
# Progress of each service
# ---------------------------------------------------------------------------


def asked_for_services(doc):
	"""(key, status field, label) of every service `doc` (a request, or a row of one) asks for."""
	return [
		(key, status, label)
		for key, status, label, flags in SERVICES
		if any(cint(doc.get(flag)) for flag in flags)
	]


def derive_request_status(statuses):
	"""The request's Status from the progress of the services it asks for.

	All called off: Cancelled. Everything not called off is Done: Completed.
	Anything Booked or Done: Confirmed — work has started. Otherwise Pending.
	"""
	live = [status for status in statuses if status != "Cancelled"]
	if statuses and not live:
		return "Cancelled"
	if live and all(status == "Done" for status in live):
		return "Completed"
	if any(status in ARRANGED for status in live):
		return "Confirmed"
	return "Pending"


def services_summary(doc):
	"""One line, "Cab: Booked · Hotel: Pending · Meal: Done", stored on the request.

	Stored as data, so in English; the form shows its own translated line.
	"""
	return " · ".join(
		f"{label}: {doc.get(status) or 'Pending'}" for _key, status, label in asked_for_services(doc)
	)


def may_update_service(doc, service, user=None):
	"""May `user` change the progress of `service` on the request `doc`?

	The Hospitality Manager, for every service; the role that delivers it
	(SERVICE_UPDATER_ROLES); the member of staff the request names for it
	(SERVICE_ASSIGNEE_FIELDS). Nobody else — Frappe's "Assigned To" included.
	`doc` is the request as stored, so nobody becomes the named member of staff
	by naming themselves in the same save.
	"""
	user = user or frappe.session.user
	if user == "Guest":
		return False
	if _is_hospitality_manager(user):
		return True
	if set(SERVICE_UPDATER_ROLES.get(service, ())) & set(frappe.get_roles(user)):
		return True
	fields = SERVICE_ASSIGNEE_FIELDS.get(service, ())
	employee = _employee_for_user(user) if fields else None
	return bool(employee and employee in [doc.get(fieldname) for fieldname in fields])


def _who_updates(service):
	"""Who may update `service`, as the refusal message names them."""
	people = [_("the Hospitality Manager")]
	people += [_(role) for role in SERVICE_UPDATER_ROLES.get(service, ())]
	meta = frappe.get_meta("Hospitality Request")
	people += [_(meta.get_label(fieldname)) for fieldname in SERVICE_ASSIGNEE_FIELDS.get(service, ())]
	return ", ".join(people)


# What people are shown about each service once it is arranged: on the Visitor
# Pass card (`get_hospitality_progress`) and in the "Booked" mails.
SERVICE_SHOWN_DETAILS = {
	"meal": ("meal_type", "assigned_meal_slots"),
	"refreshments": (),
	"cab": (
		"pickup_datetime",
		"pickup_location",
		"drop_datetime",
		"drop_location",
		"driver_name",
		"driver_phone",
		"cab_vendor",
	),
	"hotel": ("hotel_name", "check_in", "check_out", "booking_reference"),
	"tour": ("tour_date", "tour_start_time", "tour_guide"),
	"buggy": ("buggy_datetime", "buggy_pickup_point", "buggy_number", "buggy_driver"),
	"greeting": (
		"greeting_type",
		"greeting_delivery_time",
		"greeting_delivery_point",
		"greeting_assigned_to",
	),
}


def _service_details(doc, service):
	"""[(label, value)] of what is filled in for `service`, formatted for people. Plain text."""
	details = []
	for fieldname in SERVICE_SHOWN_DETAILS.get(service, ()):
		value = doc.get(fieldname)
		if _blank(value):
			continue
		df = doc.meta.get_field(fieldname)
		if df.fieldtype == "Link" and df.options == "Employee":
			value = frappe.db.get_value("Employee", value, "employee_name") or value
		elif df.fieldtype in ("Date", "Datetime", "Time"):
			value = frappe.format(value, {"fieldtype": df.fieldtype})
		details.append((_(df.label), cstr(value)))
	return details


def _booking_line(doc, service, label):
	"""One line of HTML for a mail: the service and the details people need to use it."""
	shown = " · ".join(
		f"{esc(field_label)}: <b>{esc(value)}</b>" for field_label, value in _service_details(doc, service)
	)
	return f"<b>{esc(_(label))}</b>" + (f" — {shown}" if shown else "")


def _send_quietly(**mail):
	"""Queue a mail after commit; a site with no outgoing account must not fail the save.

	See `_send_hospitality_assignment_mail` for why the message log is put back.
	"""
	messages_before = list(frappe.message_log)
	try:
		send_after_commit(**mail)
	except Exception as exc:
		frappe.local.message_log = messages_before
		frappe.log_error(
			title=f"Hospitality mail failed for {mail.get('reference_name')}",
			message=f"{exc}\n\n{frappe.get_traceback(with_context=True)}",
		)


def _get_assigned_staff_email(employee_name):
	if not employee_name:
		return None

	employee = frappe.db.get_value(
		"Employee",
		employee_name,
		["company_email", "personal_email", "user_id", "employee_name"],
		as_dict=True,
	)
	if not employee:
		return None

	return employee.company_email or employee.personal_email or employee.user_id


def _send_hospitality_assignment_mail(doc):
	email = _get_assigned_staff_email(doc.assigned_staff)
	if not email:
		return

	visitor_name, host_notes = frappe.db.get_value(
		"Visitor Pass", doc.visitor_pass, ["visitor_full_name", "hospitality_notes"]
	) or (None, None)
	visitor_name = visitor_name or doc.visitor_pass
	subject = _("Hospitality Confirmed: {0}").format(visitor_name)
	lines = [
		f"Hospitality Request: {doc.name}",
		f"Visitor Pass: {doc.visitor_pass}",
		f"Visitor: {visitor_name}",
		f"Meal Required: {'Yes' if doc.meal_required else 'No'}",
		f"Meal Type: {doc.meal_type or '-'}",
		f"Meal Slots: {getattr(doc, 'assigned_meal_slots', None) or '-'}",
		f"Hospitality Type: {getattr(doc, 'hospitality_type', None) or '-'}",
		f"Special Diet: {getattr(doc, 'special_diet', None) or '-'}",
		f"Conference Room: {doc.conference_room or '-'}",
		f"Service Time: {doc.service_time or '-'}",
	]
	# The request's Notes are the hospitality team's own; what the host asked for
	# is on the pass. The person delivering needs both.
	if not _blank(host_notes):
		lines.extend(["", f"Host's notes: {frappe.utils.strip_html(host_notes)}"])
	if not _blank(doc.notes):
		lines.extend(["", f"Notes: {frappe.utils.strip_html(doc.notes)}"])

	# frappe.sendmail on a site with no outgoing Email Account raises through
	# frappe.throw, which queues "Please setup default outgoing Email Account"
	# for the client before raising. The exception is handled below, but the
	# queued message would still reach the Hospitality Manager as if confirming
	# the request had gone wrong — so the queue is put back as it was.
	messages_before = list(frappe.message_log)
	try:
		# After commit, so a mail-server failure cannot fail the save (visitor_management/mail.py).
		send_after_commit(
			recipients=[email],
			reference_doctype=doc.doctype,
			reference_name=doc.name,
			subject=subject,
			# The lines are record values; the mail is HTML, so they are escaped.
			message="<br>".join(esc(line) for line in lines),
		)
	except Exception as exc:
		# Queueing can fail (no Email Account at all) — log and continue rather than blocking the save.
		frappe.local.message_log = messages_before
		frappe.log_error(
			title=f"Hospitality assignment email failed for {doc.name}",
			message=f"{exc}\n\n{frappe.get_traceback(with_context=True)}",
		)


class HospitalityRequest(Document):
	def autoname(self):
		visitor_name = None
		visit_date = None
		if self.visitor_pass:
			visitor_name, visit_date = frappe.db.get_value(
				"Visitor Pass", self.visitor_pass, ["visitor_full_name", "visit_date"]
			) or (None, None)

		letters = re.sub(r"[^A-Za-z]", "", visitor_name or "").upper()[:3] or "XXX"
		letters = letters.ljust(3, "X")
		date_part = getdate(visit_date or nowdate()).strftime("%d%m%y")

		base = f"HOSP-{letters}-{date_part}"
		candidate = base
		suffix = 2
		while frappe.db.exists("Hospitality Request", candidate):
			candidate = f"{base}-{suffix}"
			suffix += 1
		if self.flags.retry_count:
			# Frappe's second try after the name was taken (this DocType names
			# "by hash", so a clash on insert is retried, up to five times, with
			# a fresh `autoname`). Two passes approved at the same moment for
			# visitors with the same initials and date both saw this name free,
			# and the retry counted again inside the same snapshot, found the
			# same "free" name, and the pass approval failed with a duplicate
			# key. A random suffix cannot repeat that.
			self.name = f"{base}-{frappe.generate_hash(length=5).upper()}"
			# The rows were pointed at the name that was taken.
			self.set_parent_in_children()
			return
		self.name = candidate

	def validate(self):
		# First, while the document still holds only what the caller sent: who
		# may change the fulfilment fields. Everything after this line is the
		# system bringing the request in line with its pass and its workflow.
		self._guard_fulfilment_fields()
		self._guard_service_statuses()
		# Also before the system's own changes below: nothing is Booked or Done
		# before approval, whoever asks.
		self._validate_service_progress()
		self._validate_one_request_per_pass()
		if self.visitor_pass:
			populate_hospitality_request_from_pass(self)
		self._sync_status_with_workflow()
		self._validate_visitor_pass_approved()
		self._compute_hotel_nights()
		self._validate_cab_timing()
		self._validate_hotel_counts()
		self._validate_tour_safety()
		self._validate_buggy_conflict()
		self._validate_seating_capacity()
		self._validate_hotel_in_visit_window()
		self._validate_activities_in_visit_window()
		self.services_summary = services_summary(self)

	def onload(self):
		# Which services the person opening the form may update: the form offers
		# them one-click buttons for those and nothing else (update_service_status).
		self.set_onload(
			"services_you_update",
			[key for key, _status, _label in asked_for_services(self) if may_update_service(self, key)],
		)

	def before_submit(self):
		# The approval must have been earned (see the helper): the host held
		# `submit` here, and a bare submit of a Draft landed on Approved with no
		# Hospitality Manager and no approved pass involved.
		assert_submitted_through_approval(self)
		self._validate_visitor_pass_approved(submitting=True)
		self._validate_required_for_approval()

	def before_update_after_submit(self):
		"""An approved request is the Hospitality Manager's working record.

		`validate` does not run on an update after submit, so the rules for the
		fields that stay editable then — each service's progress, staff, and the
		booking details recorded once things are arranged — are applied here.
		"""
		self._guard_fulfilment_fields()
		self._guard_service_statuses()
		self._validate_service_progress()
		self._validate_buggy_conflict()
		self._derive_status_from_services()
		self.services_summary = services_summary(self)

	def _guard_service_statuses(self):
		"""Each service's progress is changed only by the people who deliver it.

		See `may_update_service`. Judged on every save, `ignore_permissions` or
		not: the system's own saves (the pass bringing its request in line) never
		change a service's progress, and `update_service_status` saves with
		permissions ignored only after asking the same question. A new request
		has nothing to judge: until approval its services follow the workflow
		(`_sync_status_with_workflow`).
		"""
		before = self.get_doc_before_save()
		if not before:
			return
		asked = {key for key, _status, _label in asked_for_services(self)}
		for key, status, label, _flags in SERVICES:
			new, old = self.get(status) or "Pending", before.get(status) or "Pending"
			if new == old:
				continue
			if new not in SERVICE_STATUSES:
				frappe.throw(
					_("{0} cannot be set to {1}.").format(frappe.bold(_(label)), frappe.bold(new)),
					title=_("Invalid Status"),
				)
			if key not in asked:
				frappe.throw(
					_("{0} was not asked for on this request.").format(frappe.bold(_(label))),
					title=_("Not Requested"),
				)
			if not may_update_service(before, key):
				frappe.throw(
					_("You cannot update the {0} on this request. It is updated by: {1}.").format(
						frappe.bold(_(label)), _who_updates(key)
					),
					frappe.PermissionError,
					title=_("Not Permitted"),
				)

	def _validate_service_progress(self):
		"""Booked or Done only on an approved request, and only with what the job needs.

		Approved means approved before this save: a request is approved with
		every service Pending, and the visitor is told about a booking only once
		the visit is certainly going ahead — never while it waits for the
		Hospitality Manager, is rejected, or is called off. The details are asked
		for when a service moves to Booked or Done, so a request that was already
		booked is not refused later over a detail nobody changed.
		"""
		before = self.get_doc_before_save()
		approved = bool(
			self.docstatus == 1
			and before
			and before.docstatus == 1
			and (self.get("workflow_state") or "Approved") == "Approved"
		)
		moved = [
			(key, status, label)
			for key, status, label in asked_for_services(self)
			if self.get(status) in ARRANGED
			and not (approved and (before.get(status) or "Pending") == self.get(status))
		]
		if not moved:
			return
		if not approved:
			frappe.throw(
				_(
					"{0} can be marked Booked or Done only after the request is Approved. It is {1} now."
				).format(
					", ".join(frappe.bold(_(label)) for _key, _status, label in moved),
					frappe.bold(_(self.get("workflow_state") or "Draft")),
				),
				title=_("Not Approved Yet"),
			)
		missing = []
		for key, status, label in moved:
			labels = [
				_(self.meta.get_label(fieldname))
				for fieldname, cab_types in DETAILS_TO_BOOK.get(key, ())
				if (not cab_types or self.cab_type in cab_types) and _blank(self.get(fieldname))
			]
			if labels:
				missing.append(_("{0} ({1}): {2}").format(_(label), _(self.get(status)), ", ".join(labels)))
		if missing:
			frappe.throw(
				_("Fill these in first:")
				+ "<ul>"
				+ "".join(f"<li>{esc(line)}</li>" for line in missing)
				+ "</ul>",
				title=_("Booking Details Missing"),
			)

	def _derive_status_from_services(self):
		"""An approved request's Status follows its services; nobody keeps it up to date.

		See `derive_request_status`. Before approval the workflow decides
		(`_sync_status_with_workflow`); a cancelled request stays Cancelled.
		"""
		if self.docstatus != 1:
			return
		self.status = derive_request_status(
			[self.get(status) or "Pending" for _key, status, _label in asked_for_services(self)]
		)

	def _guard_fulfilment_fields(self):
		"""Assigned Staff and Notes belong to the Hospitality Manager.

		Enforced here rather than left to the form: Employee holds write on this
		DocType (a host edits their own Draft), and the workflow's "only the
		Hospitality Manager edits this state" is a form behaviour, not a server
		rule. A save made by the system on the pass's behalf
		(`lifecycle.ensure_hospitality_request`, permissions ignored) changes
		none of these and is not a person's edit, so it is not judged.
		"""
		if self.flags.ignore_permissions:
			return

		is_manager = _is_hospitality_manager()
		before = self.get_doc_before_save()

		if self.docstatus == 1 and before and before.docstatus == 1 and not is_manager:
			frappe.throw(
				_("Only a Hospitality Manager can update an approved Hospitality Request."),
				frappe.PermissionError,
				title=_("Not Permitted"),
			)

		for fieldname in FULFILMENT_FIELDS:
			previous = before.get(fieldname) if before else None
			unchanged = (_blank(self.get(fieldname)) and _blank(previous)) or self.get(fieldname) == previous
			if not unchanged and not is_manager:
				frappe.throw(
					_("Only a Hospitality Manager can change {0}.").format(
						frappe.bold(_(self.meta.get_label(fieldname)))
					),
					frappe.PermissionError,
					title=_("Not Permitted"),
				)

		# `status` is not judged: nobody sets it. It follows the workflow before
		# approval and the services after (`_sync_status_with_workflow`,
		# `_derive_status_from_services`), whatever a save carried in it.

	def _validate_one_request_per_pass(self):
		"""One live request per pass; the pass links to exactly one.

		Checked when the request is created. `visitor_pass` cannot be changed
		afterwards (set_only_once), so it cannot become a duplicate later, and
		duplicates that already exist on a site stay editable for whoever has to
		sort them out. The locking read closes the gap with the request the pass
		raises for itself (`lifecycle._sync_hospitality_request` locks the same
		range).
		"""
		if not (self.visitor_pass and self.is_new()):
			return
		other = frappe.db.get_value(
			"Hospitality Request",
			{"visitor_pass": self.visitor_pass, "docstatus": ("<", 2), "name": ("!=", self.name or "")},
			["name", "workflow_state"],
			as_dict=True,
			for_update=True,
		)
		if not other:
			return
		message = _("Visitor Pass {0} already has Hospitality Request {1}. Open that one instead.").format(
			frappe.bold(self.visitor_pass), get_link_to_form("Hospitality Request", other.name)
		)
		if other.workflow_state == "Rejected":
			message += " " + _("It was rejected — use Actions > Reapply on it to ask again.")
		frappe.throw(message, title=_("Request Already Exists"))

	def _sync_status_with_workflow(self):
		"""Keep the fulfilment status in step with the workflow and the pass.

		`status` is what the hospitality team works from, and the workflow's
		states carry no `update_field`, so nothing moved it: a rejected request
		went on reading "Pending" (and was listed as work, and held its buggy),
		and one called off because its pass was rejected read "Cancelled" for
		good, even after the pass was approved or the request amended.

		Until a request is Approved there is no fulfilment to track, so its
		status — and each service's — is decided entirely by where it stands:
		"Cancelled" while it is Rejected or its pass is Rejected/Cancelled,
		"Pending" otherwise. So a rejected request that is sent in again, or an
		amended copy, starts from Pending. From Approved on, the services are
		updated by the people delivering them and the status follows them
		(`_derive_status_from_services`).
		"""
		if self.docstatus != 0:
			# Being approved right now: every service is still Pending.
			self._derive_status_from_services()
			return

		called_off = (self.workflow_state or "Draft") == "Rejected"
		if not called_off and self.visitor_pass:
			pass_status = frappe.db.get_value("Visitor Pass", self.visitor_pass, "status")
			called_off = pass_status in PASS_CALLED_OFF_STATUSES
		self.status = "Cancelled" if called_off else "Pending"
		asked = {status for _key, status, _label in asked_for_services(self)}
		for _key, status, _label, _flags in SERVICES:
			self.set(status, "Cancelled" if called_off and status in asked else "Pending")

	def _validate_required_for_approval(self):
		"""What each requested service needs before the request is approved.

		The form marks these mandatory, but only the form did: a workflow action
		from the list, or the request the pass promotes on its own, reached
		Approved without a tour guide or a pickup point. Draft and Pending
		Approval stay permissive on purpose — the request is raised and promoted
		by the pass, and a pass approval must not fail over a detail the
		hospitality team fills in later. Approve is where it has to be complete.
		"""
		required = []
		for flag, fieldnames in REQUIRED_FOR_APPROVAL.items():
			if cint(self.get(flag)):
				required.extend(fieldnames)
		if cint(self.cab_required):
			if self.cab_type in ("Pickup", "Both"):
				required.extend(CAB_PICKUP_FIELDS)
			if self.cab_type in ("Drop", "Both"):
				required.extend(CAB_DROP_FIELDS)

		missing = [_(self.meta.get_label(fieldname)) for fieldname in required if not self.get(fieldname)]
		if missing:
			frappe.throw(
				_("Fill in these fields before approving the request: {0}").format(
					"<ul>" + "".join(f"<li>{label}</li>" for label in missing) + "</ul>"
				),
				frappe.MandatoryError,
				title=_("Missing Fields"),
			)

	# Real-world rule: hospitality preparation should not begin until the visitor
	# is confirmed. Drafts (and re-applications after rejection) can be created
	# against any pass (so meal flags etc. can be drafted alongside the pass), but
	# moving the request out of Draft requires the linked Visitor Pass to be
	# Approved or beyond (Items Verified / Checked-In / -Out). A submit is checked
	# whatever state the document claims: on a bare submit the state field still
	# says Draft at this point.

	def _validate_visitor_pass_approved(self, submitting=False):
		if not self.visitor_pass:
			return
		current_state = getattr(self, "workflow_state", None) or "Draft"
		if current_state in ("Draft", "Rejected") and not submitting:
			return
		vp_status = frappe.db.get_value("Visitor Pass", self.visitor_pass, "status")
		if vp_status not in PASS_CONFIRMED_STATUSES:
			frappe.throw(
				_(
					"Visitor Pass {0} is currently <b>{1}</b>. Please ensure the Visitor "
					"Pass is Approved before submitting this Hospitality Request."
				).format(self.visitor_pass, vp_status or _("Draft")),
				title=_("Approval Not Allowed"),
			)

	def _validate_seating_capacity(self):
		if self.seating_capacity is not None and int(self.seating_capacity or 0) < 0:
			frappe.throw(
				_("Seating Capacity cannot be negative."),
				title=_("Invalid Seating"),
			)
		if not self.conference_room:
			return
		room_cap = frappe.db.get_value("Conference Room", self.conference_room, "capacity") or 0
		guests = int(self.seating_capacity or self.no_of_guests or 0)
		if guests and room_cap and guests > int(room_cap):
			frappe.throw(
				_("Seating ({0}) exceeds room {1} capacity ({2}). Pick a larger room.").format(
					guests, self.conference_room, room_cap
				),
				title=_("Room Over-Booked"),
			)

	def _validate_activities_in_visit_window(self):
		"""Tour / buggy / greeting times must fall within the visit window."""
		if not (self.visit_start_time and self.visit_end_time):
			return
		start_dt = get_datetime(self.visit_start_time)
		end_dt = get_datetime(self.visit_end_time)

		def _check(label, dt_value, buffer_hours=0):
			if not dt_value:
				return
			v = get_datetime(dt_value)
			lo = start_dt - timedelta(hours=buffer_hours)
			hi = end_dt + timedelta(hours=buffer_hours)
			if v < lo or v > hi:
				frappe.throw(
					_("{0} ({1}) is outside the visit window ({2} \u2192 {3}).").format(
						label, dt_value, start_dt, end_dt
					),
					title=_("Out of Visit Window"),
				)

		if self.factory_tour_required and self.tour_date:
			if self.tour_start_time:
				_check(_("Tour start"), get_datetime(f"{self.tour_date} {self.tour_start_time}"))
			if self.tour_end_time:
				_check(_("Tour end"), get_datetime(f"{self.tour_date} {self.tour_end_time}"))

		if self.buggy_required and self.buggy_datetime:
			_check(_("Buggy pickup"), self.buggy_datetime)

		if self.greeting_required and self.greeting_delivery_time:
			# greeting often happens at arrival — allow 30 min buffer either side
			_check(_("Greeting delivery"), self.greeting_delivery_time, buffer_hours=0.5)

	def _validate_hotel_in_visit_window(self):
		"""Hotel check-in/out should fall within (or very close to) the visit window."""
		if not (self.hotel_required and self.check_in and self.visitor_pass):
			return

		vp = (
			frappe.db.get_value(
				"Visitor Pass",
				self.visitor_pass,
				["visit_date", "pass_valid_until"],
				as_dict=True,
			)
			or {}
		)
		visit_date = vp.get("visit_date")
		valid_until = vp.get("pass_valid_until") or visit_date

		if visit_date and getdate(self.check_in) < getdate(visit_date):
			# Allow arriving up to 1 day earlier (for late evening/next-day meetings)
			if date_diff(visit_date, self.check_in) > 1:
				frappe.throw(
					_("Hotel check-in ({0}) is before the visit date ({1}).").format(
						self.check_in, visit_date
					),
					title=_("Invalid Hotel Dates"),
				)

		if self.check_out and valid_until:
			if getdate(self.check_out) > getdate(valid_until):
				# Allow departing up to 1 day after
				if date_diff(self.check_out, valid_until) > 1:
					frappe.throw(
						_("Hotel check-out ({0}) is after the visit ends ({1}).").format(
							self.check_out, valid_until
						),
						title=_("Invalid Hotel Dates"),
					)

	def _compute_hotel_nights(self):
		if self.hotel_required and self.check_in and self.check_out:
			nights = date_diff(self.check_out, self.check_in)
			if nights < 0:
				frappe.throw(
					_("Hotel check-out ({0}) cannot be before check-in ({1}).").format(
						self.check_out, self.check_in
					),
					title=_("Invalid Hotel Dates"),
				)
			# 0 nights is valid for day-use hotel (prayer room / locker / day stay).
			self.nights = nights
		else:
			self.nights = 0

	def _validate_hotel_counts(self):
		"""A hotel booking is for at least one room and one guest."""
		if not cint(self.hotel_required):
			return
		for fieldname in ("no_of_rooms", "no_of_guests"):
			if cint(self.get(fieldname)) < 1:
				frappe.throw(
					_("{0} must be at least 1 when a hotel is required.").format(
						frappe.bold(_(self.meta.get_label(fieldname)))
					),
					title=_("Invalid Hotel Booking"),
				)

	def _validate_cab_timing(self):
		if not self.cab_required:
			return
		if self.cab_type in ("Pickup", "Both") and not self.pickup_datetime:
			frappe.throw(_("Pickup datetime required when cab type includes Pickup"))
		if self.cab_type in ("Drop", "Both") and not self.drop_datetime:
			frappe.throw(_("Drop datetime required when cab type includes Drop"))
		if self.pickup_datetime and self.drop_datetime:
			if get_datetime(self.drop_datetime) < get_datetime(self.pickup_datetime):
				frappe.throw(_("Drop datetime cannot be before pickup datetime"))

	def _validate_tour_safety(self):
		if not self.factory_tour_required:
			return
		if self.tour_start_time and self.tour_end_time:
			# Same string-vs-time trap as conference_room.py: a Time field can arrive
			# as a string, and a single-digit hour has no leading zero, so
			# "10:00:00" <= "9:00:00" is True as strings ('1' < '9') and a 09:00-10:00
			# tour was rejected as "end time must be after start time". Worse here
			# than on Conference Room: retyping the times in the form did not clear
			# it, so a tour starting before 10:00 could never be re-saved from the
			# Desk once created. get_time() on both sides fixes it for good.
			if get_time(self.tour_end_time) <= get_time(self.tour_start_time):
				frappe.throw(_("Tour end time must be after start time"))
		self._validate_tour_areas()

	def _validate_tour_areas(self):
		"""Each area's slot runs forwards and lies inside the tour.

		The tour's own start and end are the window when they are set; otherwise
		the visit window, when the visit starts and ends on the tour day (the tour
		itself is held to the visit window in `_validate_activities_in_visit_window`).
		"""
		lo = hi = None
		if self.tour_start_time and self.tour_end_time:
			lo, hi = get_time(self.tour_start_time), get_time(self.tour_end_time)
		elif self.tour_date and self.visit_start_time and self.visit_end_time:
			visit_start, visit_end = get_datetime(self.visit_start_time), get_datetime(self.visit_end_time)
			if visit_start.date() == visit_end.date() == getdate(self.tour_date):
				lo, hi = visit_start.time(), visit_end.time()

		for row in self.get("tour_areas") or []:
			if not (row.from_time and row.to_time):
				continue
			start, end = get_time(row.from_time), get_time(row.to_time)
			area = frappe.bold(row.area_name or _("Row {0}").format(row.idx))
			if end <= start:
				frappe.throw(
					_("Tour area {0}: To Time must be after From Time.").format(area),
					title=_("Invalid Tour Area"),
				)
			if lo and hi and (start < lo or end > hi):
				frappe.throw(
					_("Tour area {0} ({1} - {2}) is outside the tour ({3} - {4}).").format(
						area,
						start.strftime("%H:%M"),
						end.strftime("%H:%M"),
						lo.strftime("%H:%M"),
						hi.strftime("%H:%M"),
					),
					title=_("Invalid Tour Area"),
				)

	def _validate_buggy_conflict(self):
		"""A buggy cannot be given to two live requests less than one run apart.

		Only an identical datetime used to count, so 10:00 and 10:05 on the same
		buggy both went through. See BUGGY_RUN_MINUTES.

		"Live" is decided by the workflow, not by `status` alone: a request that
		was rejected, or a Draft whose pass has not been approved (it may never
		be), is not work anyone will carry out and must not hold a buggy. A
		Draft whose pass IS approved is real work waiting to be sent in.

		Asked when the buggy is being assigned or changed, and when the request
		moves on in its workflow — which is where a Draft that was let through
		above meets the live work it clashes with. It is not asked again on a
		save that touches neither: the request is re-saved by its pass on every
		pass save, and a clash that appeared since must stop this request going
		forward, not stop the pass from being saved or approved.
		"""
		if not (self.buggy_required and self.buggy_number and self.buggy_datetime):
			return
		before = self.get_doc_before_save()
		if before and not (
			before.buggy_number != self.buggy_number
			or not before.buggy_datetime
			or get_datetime(before.buggy_datetime) != get_datetime(self.buggy_datetime)
			or cint(before.buggy_required) != cint(self.buggy_required)
			or before.get("workflow_state") != self.get("workflow_state")
			or before.docstatus != self.docstatus
		):
			return
		conflict = frappe.db.sql(
			"""
			SELECT hr.name, hr.buggy_datetime
			FROM `tabHospitality Request` hr
			LEFT JOIN `tabVisitor Pass` vp ON vp.name = hr.visitor_pass
			WHERE hr.name != %(name)s
			  AND hr.buggy_required = 1
			  AND hr.buggy_number = %(buggy_number)s
			  AND hr.buggy_datetime > %(run_starts)s
			  AND hr.buggy_datetime < %(run_ends)s
			  AND hr.docstatus < 2
			  AND hr.status NOT IN ('Cancelled', 'Completed')
			  AND (
				hr.workflow_state IN ('Pending Approval', 'Approved')
				OR (hr.workflow_state = 'Draft' AND vp.status IN %(pass_confirmed)s)
			  )
			LIMIT 1
			""",
			{
				"name": self.name or "",
				"buggy_number": self.buggy_number,
				"run_starts": get_datetime(self.buggy_datetime) - timedelta(minutes=BUGGY_RUN_MINUTES),
				"run_ends": get_datetime(self.buggy_datetime) + timedelta(minutes=BUGGY_RUN_MINUTES),
				"pass_confirmed": PASS_CONFIRMED_STATUSES,
			},
		)
		if conflict:
			frappe.throw(
				_("Buggy {0} is already booked at {1} ({2}). Allow {3} minutes between runs.").format(
					frappe.bold(frappe.utils.escape_html(self.buggy_number)),
					frappe.format(conflict[0][1], {"fieldtype": "Datetime"}),
					conflict[0][0],
					BUGGY_RUN_MINUTES,
				),
				title=_("Buggy Already Booked"),
			)

	def before_cancel(self):
		"""Let the Cancel action actually complete.

		The workflow offers Approved --Cancel--> Cancelled, but the parent
		Visitor Pass carries a `hospitality_request` link back to this document,
		and Frappe refuses to cancel anything another record still links to.
		So Cancel raised LinkExistsError for everyone, every time — the second
		of two reasons that button never worked (the first was that no role held
		the `cancel` permission at all).

		The back-link is descriptive, not a dependency: the pass records which
		request belongs to it. `sync_hospitality_to_pass` (from on_cancel) keeps
		the pass's own hospitality status in step and takes the link off the
		pass, so the pass is not left claiming an arrangement that was called
		off — nor linking to a cancelled document, which Frappe would then
		refuse to save the pass over.

		`before_cancel` rather than later: Frappe runs the back-link check at
		document.py:1384, immediately after `on_cancel`, so the flag has to be set
		before the cancel save gets that far.
		"""
		self.ignore_linked_doctypes = ("Visitor Pass",)

	def on_cancel(self):
		"""Move the fulfilment status too, not just the workflow state.

		`status` is what the hospitality team actually works from — it is the
		Pending/Confirmed/Served queue. Cancelling only moved `workflow_state`,
		so a called-off request still read as "Pending" and the kitchen would
		keep preparing a meal for a visit that is not happening.

		db_set because the document is cancelled by this point and a normal save
		would be refused.

		`sync_hospitality_to_pass` has to be called by hand for the same reason:
		db_set writes straight to the row without running `on_update`, so the
		sync that normally rides on it never fired on cancel. The Visitor Pass was
		left reading `hospitality_overall_status = Confirmed` for an arrangement
		that had just been called off — and the Visitor Itinerary print format
		shows that field to the gate. The sync itself is `db.set_value`, so it is
		safe against a document that is already cancelled.

		Each service that was not delivered is called off with it; one already
		Done is history and stays Done.
		"""
		values = {"status": "Cancelled"}
		for _key, status, _label in asked_for_services(self):
			if self.get(status) != "Done":
				values[status] = "Cancelled"
		self.update(values)
		values["services_summary"] = services_summary(self)
		self.db_set(values, update_modified=False)
		sync_hospitality_to_pass(self, status_only=True)

	def run_notifications(self, method):
		"""Same guard as VisitorPass.run_notifications — see the note there.

		This one matters because a Hospitality Request is created automatically
		while a Visitor Pass is being submitted, so ITS failing alert email lands
		in the pass's own response and can evict the messages the approver
		actually needs to see, including the blacklist warning. Restoring the
		snapshot keeps a second document's mail problem out of the first
		document's conversation with the user.
		"""
		messages_before = list(frappe.message_log)
		try:
			super().run_notifications(method)
		finally:
			frappe.local.message_log = messages_before

	def on_update(self):
		sync_hospitality_to_pass(self)
		self._notify_assigned_staff()

	def on_update_after_submit(self):
		# A service moving along on an approved request: on_update does not run
		# for this save, so the pass's mirror of the status and the staff
		# member's mail never happened once a request was approved.
		sync_hospitality_to_pass(self, status_only=True)
		self._notify_assigned_staff()
		self._tell_what_was_booked()

	def _tell_what_was_booked(self):
		"""When a service becomes Booked, the host and whoever raised the request hear about it.

		A bell alert and a mail each. The visitor is mailed too for a cab or a
		hotel (VISITOR_TOLD_SERVICES), unless VMS Settings switches that off
		(`email_visitor_about_bookings`). Nobody is told about a service that was
		already Booked or Done, or only moved back. Booked is possible only on an
		approved request (`_validate_service_progress`), so nobody is told about
		an arrangement for a visit that may not go ahead.
		"""
		previous = self.get_doc_before_save()
		newly_booked = [
			(key, status, label)
			for key, status, label in asked_for_services(self)
			if self.get(status) == "Booked"
			and (not previous or (previous.get(status) or "Pending") not in ARRANGED)
		]
		if not newly_booked or not self.visitor_pass:
			return
		visitor_pass = frappe.db.get_value(
			"Visitor Pass",
			self.visitor_pass,
			["name", "visitor_full_name", "visit_date", "email_id", "owner", "person_to_visit"],
			as_dict=True,
		)
		if not visitor_pass:
			return
		visitor = visitor_pass.visitor_full_name or visitor_pass.name
		visit_date = frappe.format(visitor_pass.visit_date, {"fieldtype": "Date"})
		labels = ", ".join(_(label) for _key, _status, label in newly_booked)
		lines = "".join(f"<li>{_booking_line(self, key, label)}</li>" for key, _status, label in newly_booked)
		url = frappe.utils.get_url_to_form(self.doctype, self.name)

		# The host, and whoever raised the request (the host's own pass raises it,
		# so usually the same person). Whoever booked it knows already.
		host_user = (
			frappe.db.get_value("Employee", visitor_pass.person_to_visit, "user_id")
			if visitor_pass.person_to_visit
			else None
		)
		me = frappe.session.user
		users = [
			user
			for user in dict.fromkeys((host_user, self.owner))
			if user
			and user not in ("Administrator", "Guest", me)
			and frappe.db.get_value("User", user, "enabled")
		]
		subject = _("Booked for {0}: {1}").format(visitor, labels)
		message = (
			f"<p>{esc(_('These have been arranged for your visitor {0} ({1}):').format(visitor, visit_date))}</p>"
			f"<ul>{lines}</ul>"
			f"<p><a href='{esc(url)}'>{esc(_('Open Hospitality Request {0}').format(self.name))}</a></p>"
		)
		if users:
			emails = sorted({frappe.db.get_value("User", user, "email") for user in users} - {None, ""})
			if emails:
				_send_quietly(
					recipients=emails,
					subject=subject,
					message=message,
					reference_doctype=self.doctype,
					reference_name=self.name,
				)
			for user in users:
				frappe.get_doc(
					{
						"doctype": "Notification Log",
						"for_user": user,
						"type": "Alert",
						"document_type": self.doctype,
						"document_name": self.name,
						"subject": esc(subject),
						"email_content": message,
					}
				).insert(ignore_permissions=True)

		for_visitor = [(key, label) for key, _status, label in newly_booked if key in VISITOR_TOLD_SERVICES]
		if for_visitor and visitor_pass.email_id and feature_on("email_visitor_about_bookings"):
			_send_quietly(
				recipients=[visitor_pass.email_id],
				subject=_("Your {0} for the visit on {1}").format(
					", ".join(_(label).lower() for _key, label in for_visitor), visit_date
				),
				message=(
					f"<p>{esc(_('Dear {0},').format(visitor))}</p>"
					f"<p>{esc(_('We have arranged the following for your visit:'))}</p>"
					f"<ul>{''.join(f'<li>{_booking_line(self, key, label)}</li>' for key, label in for_visitor)}</ul>"
					f"<p>{esc(_('Please contact your host if anything needs to change.'))}</p>"
				),
				reference_doctype="Visitor Pass",
				reference_name=visitor_pass.name,
			)

	def _notify_assigned_staff(self):
		previous = self.get_doc_before_save()
		status_changed_to_confirmed = self.status == "Confirmed" and (
			not previous or previous.status != "Confirmed"
		)
		assigned_staff_changed = (
			self.status == "Confirmed"
			and self.assigned_staff
			and previous
			and previous.assigned_staff != self.assigned_staff
		)
		if status_changed_to_confirmed or assigned_staff_changed:
			_send_hospitality_assignment_mail(self)


@frappe.whitelist(methods=["POST"])
def update_service_status(request: str, service: str, status: str, details: str | dict | None = None):
	"""One click from the person delivering a service: its new progress, and its booking details.

	The cab coordinator, the hotel desk and the kitchen each update their own
	service on an approved request without editing the request itself — they
	hold no write on it, and need none. `details` may carry only that service's
	booking fields (SERVICE_DETAIL_FIELDS), e.g. the driver's name and phone with
	"Booked" for the cab.
	"""
	if service not in SERVICE_STATUS_FIELD:
		frappe.throw(_("Unknown service: {0}").format(esc(service)), title=_("Invalid Service"))
	if status not in SERVICE_STATUSES:
		frappe.throw(_("Unknown status: {0}").format(esc(status)), title=_("Invalid Status"))

	doc = frappe.get_doc("Hospitality Request", request)
	label = next(label for key, _status, label, _flags in SERVICES if key == service)
	if service not in {key for key, _status, _label in asked_for_services(doc)}:
		frappe.throw(
			_("{0} was not asked for on this request.").format(frappe.bold(_(label))),
			title=_("Not Requested"),
		)
	if not may_update_service(doc, service):
		frappe.throw(
			_("You cannot update the {0} on this request. It is updated by: {1}.").format(
				frappe.bold(_(label)), _who_updates(service)
			),
			frappe.PermissionError,
			title=_("Not Permitted"),
		)
	if doc.docstatus != 1:
		frappe.throw(
			_("{0} can be updated only after the request is Approved. It is {1} now.").format(
				frappe.bold(_(label)), frappe.bold(_(doc.workflow_state or "Draft"))
			),
			title=_("Not Approved Yet"),
		)

	details = frappe.parse_json(details) if isinstance(details, str) else details
	details = details or {}
	if not isinstance(details, dict):
		frappe.throw(_("Booking details must be a set of fields and values."))
	allowed = SERVICE_DETAIL_FIELDS.get(service, ())
	others = [fieldname for fieldname in details if fieldname not in allowed]
	if others:
		frappe.throw(
			_("Only these details can be recorded with the {0}: {1}").format(
				_(label), ", ".join(_(doc.meta.get_label(f)) for f in allowed) or _("none")
			),
			title=_("Not Permitted"),
		)

	doc.update({fieldname: details[fieldname] for fieldname in allowed if fieldname in details})
	doc.set(SERVICE_STATUS_FIELD[service], status)
	# Their right to this one service was checked above; the rest of the request
	# is not theirs, and Frappe keeps every field that is not "Allow on Submit"
	# as approved.
	doc.flags.ignore_permissions = True
	doc.save()
	return {"status": doc.status, "services_summary": doc.services_summary}


@frappe.whitelist()
def get_hospitality_progress(visitor_pass: str):
	"""What the host sees on the Visitor Pass: each service asked for, how far it has got, its details.

	Only for somebody who may open the pass AND its request: reading a pass is
	not enough (Security reads every approved pass), and the request carries
	drivers' phones and hotel bookings. Plain values; the form escapes them.
	"""
	if not frappe.has_permission("Visitor Pass", "read", doc=visitor_pass):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	request_name = frappe.db.get_value("Visitor Pass", visitor_pass, "hospitality_request")
	if not request_name or not frappe.has_permission("Hospitality Request", "read", doc=request_name):
		return {"request": None}
	doc = frappe.get_doc("Hospitality Request", request_name)
	return {
		"request": {
			"name": doc.name,
			"workflow_state": doc.workflow_state or "Draft",
			"status": doc.status or "Pending",
			"services": [
				{
					"key": key,
					"label": _(label),
					"status": doc.get(status) or "Pending",
					"details": _service_details(doc, key),
				}
				for key, status, label in asked_for_services(doc)
			],
		}
	}
