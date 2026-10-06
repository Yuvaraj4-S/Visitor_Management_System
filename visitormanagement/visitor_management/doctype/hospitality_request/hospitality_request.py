# For license information, please see license.txt

import re
from datetime import timedelta

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import cint, cstr, date_diff, get_datetime, get_link_to_form, get_time, getdate, nowdate

from visitormanagement.permissions import HOSPITALITY_OVERSEERS
from visitormanagement.visitor_management.lifecycle import (
	PASS_CALLED_OFF_STATUSES,
	PASS_CONFIRMED_STATUSES,
	assert_submitted_through_approval,
	populate_hospitality_request_from_pass,
	sync_hospitality_to_pass,
)
from visitormanagement.visitor_management.mail import esc, send_after_commit

# Fulfilment progress of an approved request, in order. "Cancelled" is not a
# step: it is where the workflow puts a request that was rejected, cancelled, or
# whose pass was called off.
STATUS_STEPS = ("Pending", "Confirmed", "Served", "Completed")

# What the Hospitality Manager records about delivery. Everything else on the
# request is the host's ask.
FULFILMENT_FIELDS = ("status", "assigned_staff", "notes")

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


def _is_hospitality_manager(user=None):
	user = user or frappe.session.user
	return user == "Administrator" or bool(set(HOSPITALITY_OVERSEERS) & set(frappe.get_roles(user)))


def _blank(value):
	"""True for an empty value, including a Text Editor's empty markup."""
	text = cstr(value).strip()
	if not text:
		return True
	return "<img" not in text and not frappe.utils.strip_html(text).strip()


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
		self.name = candidate

	def validate(self):
		# First, while the document still holds only what the caller sent: who
		# may change the fulfilment fields. Everything after this line is the
		# system bringing the request in line with its pass and its workflow.
		self._guard_fulfilment_fields()
		self._validate_one_request_per_pass()
		if self.visitor_pass:
			populate_hospitality_request_from_pass(self)
		self._sync_status_with_workflow()
		self._validate_visitor_pass_approved()
		self._compute_hotel_nights()
		self._validate_cab_timing()
		self._validate_tour_safety()
		self._validate_buggy_conflict()
		self._validate_seating_capacity()
		self._validate_hotel_in_visit_window()
		self._validate_activities_in_visit_window()

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
		fields that stay editable then — fulfilment status, staff, and the
		booking details recorded once things are arranged — are applied here.
		"""
		self._guard_fulfilment_fields()
		self._validate_buggy_conflict()

	def _guard_fulfilment_fields(self):
		"""Status, Assigned Staff and Notes belong to the Hospitality Manager.

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

		for fieldname in ("assigned_staff", "notes"):
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

		# A new document's status is whatever the form defaulted or an amended
		# copy carried over; `_sync_status_with_workflow` sets the real one.
		if not before or (self.status or "Pending") == (before.status or "Pending"):
			return

		if not is_manager:
			frappe.throw(
				_("Only a Hospitality Manager can change {0}.").format(frappe.bold(_("Status"))),
				frappe.PermissionError,
				title=_("Not Permitted"),
			)
		if not (self.docstatus == 1 and before.docstatus == 1):
			frappe.throw(
				_("Status can be changed once the request is Approved. Until then it follows the workflow."),
				title=_("Not Approved Yet"),
			)
		if self.status not in STATUS_STEPS:
			frappe.throw(
				_(
					"To call off an approved request use Actions > Cancel; Status cannot be set to {0}."
				).format(frappe.bold(_(self.status))),
				title=_("Invalid Status"),
			)
		previous_status = before.status or "Pending"
		if previous_status not in STATUS_STEPS or STATUS_STEPS.index(self.status) < STATUS_STEPS.index(
			previous_status
		):
			frappe.throw(
				_("Status cannot go back from {0} to {1}. The steps are {2}.").format(
					frappe.bold(_(previous_status)),
					frappe.bold(_(self.status)),
					" > ".join(_(step) for step in STATUS_STEPS),
				),
				title=_("Invalid Status"),
			)

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
		status is decided entirely by where it stands: "Cancelled" while it is
		Rejected or its pass is Rejected/Cancelled, "Pending" otherwise. From
		Approved on it is the Hospitality Manager's (see `_guard_fulfilment_fields`).
		"""
		if self.docstatus != 0:
			# Being approved right now: fulfilment starts from Pending.
			if self.status not in STATUS_STEPS:
				self.status = "Pending"
			return

		called_off = (self.workflow_state or "Draft") == "Rejected"
		if not called_off and self.visitor_pass:
			pass_status = frappe.db.get_value("Visitor Pass", self.visitor_pass, "status")
			called_off = pass_status in PASS_CALLED_OFF_STATUSES
		self.status = "Cancelled" if called_off else "Pending"

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

	def _validate_buggy_conflict(self):
		"""A buggy cannot be given to two live requests for the same time.

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
			SELECT hr.name
			FROM `tabHospitality Request` hr
			LEFT JOIN `tabVisitor Pass` vp ON vp.name = hr.visitor_pass
			WHERE hr.name != %(name)s
			  AND hr.buggy_required = 1
			  AND hr.buggy_number = %(buggy_number)s
			  AND hr.buggy_datetime = %(buggy_datetime)s
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
				"buggy_datetime": get_datetime(self.buggy_datetime),
				"pass_confirmed": PASS_CONFIRMED_STATUSES,
			},
		)
		if conflict:
			frappe.throw(
				_("Buggy {0} is already booked at {1} ({2}).").format(
					frappe.bold(frappe.utils.escape_html(self.buggy_number)),
					frappe.format(self.buggy_datetime, {"fieldtype": "Datetime"}),
					conflict[0][0],
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
		"""
		self.db_set("status", "Cancelled", update_modified=False)
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
		# The Hospitality Manager moving an approved request along: on_update
		# does not run for this save, so the pass's mirror of the status and the
		# staff member's mail never happened once a request was approved.
		sync_hospitality_to_pass(self, status_only=True)
		self._notify_assigned_staff()

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
