# For license information, please see license.txt
"""Gate Entry Request — ad-hoc visitor captured at the gate.

Security records the visitor and sends the request to the person (or Employee
Group) being visited. Any mapped person approves or rejects; on approval a
Visitor Pass is created and submitted, so the standard badge / QR / approval
email / hospitality flow runs exactly as for any other approved pass.

Status moves only through the whitelisted actions below:
    Draft -> Pending Approval -> Approved | Rejected | Expired (scheduler)
"""

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_to_date, cint, flt, now_datetime, today

from visitormanagement.visitor_management.utils import (
	GATE_BY_VISITOR_TYPE,
	GROUP,
	SINGLE_PERSON,
	doc_link,
	get_employee_for_user,
	get_employee_users,
	get_group_employees,
	get_mapped_employees,
	mask_for_user,
	notify_users,
	restore_masked_id,
)
from visitormanagement.visitor_management.validators import id_proof_error_message, validate_id

DRAFT, PENDING, APPROVED, REJECTED, EXPIRED = "Draft", "Pending Approval", "Approved", "Rejected", "Expired"

# Visitor Item fields declared on the request; the verification fields stay for Security at check-in.
ITEM_FIELDS = (
	"item_code", "item_name", "item_category", "quantity", "unit_of_measure",
	"description", "is_new_item", "serial_number", "estimated_value",
)


class GateEntryRequest(Document):
	def onload(self):
		masked = mask_for_user(self)
		if masked != self.id_proof_number:
			self.id_proof_number = masked
			self.set_onload("id_masked", 1)
		self.set_onload("can_act", cint(self.status == PENDING and self.can_user_act()))

	def validate(self):
		restore_masked_id(self)
		self._normalize_mobile_number()
		self._guard_status_change()
		self._validate_mapping()
		self._validate_company()
		self._validate_id_proof()
		self._validate_duration()
		self._set_defaults()
		if self.status in (DRAFT, PENDING):
			self._check_blacklist()
			self._check_duplicate_pending()

	# ─────────────────────────────────────────────────────────
	# Validations
	# ─────────────────────────────────────────────────────────
	def _guard_status_change(self):
		if self.is_new():
			self.status = DRAFT
			return
		if self.flags.vms_status_change:
			return
		if self.has_value_changed("status"):
			frappe.throw(_("Status can only be changed with the Submit / Approve / Reject actions."))
		if self.status != DRAFT and (self.has_value_changed("person_to_visit") or self.has_value_changed("visitor_group")):
			frappe.throw(_("The visit mapping cannot be changed after the request is sent."))

	def _normalize_mobile_number(self):
		# same "+91-XXXXXXXXXX" form the Visitor Pass stores (the Phone field needs a country code)
		raw = str(self.mobile_number or "").strip()
		if not raw or raw.startswith("+"):
			return
		digits = "".join(c for c in raw if c.isdigit())
		if digits.startswith("91") and len(digits) > 10:
			digits = digits[2:]
		if len(digits) >= 10:
			self.mobile_number = f"+91-{digits[-10:]}"

	def _validate_mapping(self):
		if self.mapping_type == GROUP:
			if not self.visitor_group:
				frappe.throw(_("Visitor Group is required when Visit Mapping is Group."))
			if not get_group_employees(self.visitor_group):
				frappe.throw(_("Employee Group {0} has no active employees.").format(self.visitor_group))
			self.person_to_visit = None
		else:
			self.mapping_type = SINGLE_PERSON
			if not self.person_to_visit:
				frappe.throw(_("Person to Visit is required when Visit Mapping is Single Person."))
			if frappe.db.get_value("Employee", self.person_to_visit, "status") != "Active":
				frappe.throw(_("Person to Visit {0} is not an Active employee.").format(self.person_to_visit))
			self.visitor_group = None

	def _validate_company(self):
		if self.visitor_type in ("Contractor", "Supplier", "Customer") and not self.visitor_company:
			frappe.throw(_("Company / Organization is required for {0} visitors.").format(self.visitor_type), frappe.MandatoryError)

	def _validate_id_proof(self):
		if self.id_proof_type and self.id_proof_number and not validate_id(self.id_proof_type, self.id_proof_number):
			frappe.throw(_(id_proof_error_message(self.id_proof_type)), title=_("Invalid ID Proof"))

	def _validate_duration(self):
		if flt(self.expected_duration) <= 0:
			self.expected_duration = 2
		max_hours = cint(frappe.db.get_single_value("VMS Settings", "max_visit_duration_hrs"))
		if max_hours and flt(self.expected_duration) > max_hours:
			frappe.throw(_("Expected Duration cannot exceed {0} hours (VMS Settings).").format(max_hours))
		if cint(self.number_of_visitors) < 1:
			self.number_of_visitors = 1

	def _set_defaults(self):
		if not self.security_officer:
			self.security_officer = get_employee_for_user()
		if not self.gate_name and self.visitor_type:
			self.gate_name = GATE_BY_VISITOR_TYPE.get(self.visitor_type, "Main Gate")

	def _check_blacklist(self):
		from visitormanagement.visitor_management.doctype.visitor_blacklist.visitor_blacklist import (
			VisitorBlacklist,
		)

		match = VisitorBlacklist.find_active_match(
			id_proof_number=self.id_proof_number,
			visitor_name=self.visitor_full_name,
			id_proof_type=self.id_proof_type,
		)
		if match:
			reason = frappe.db.get_value("Visitor Blacklist", match, "reason")
			frappe.throw(
				_("Visitor {0} is on the active blacklist. Reason: {1}").format(
					self.visitor_full_name, reason or _("Not specified")
				),
				title=_("Access Denied — Blacklisted Visitor"),
			)

	def _check_duplicate_pending(self):
		existing = frappe.db.get_value(
			"Gate Entry Request",
			{"id_proof_number": self.id_proof_number, "status": PENDING, "name": ["!=", self.name or ""]},
			"name",
		)
		if existing:
			frappe.throw(
				_("Gate Entry Request {0} for this ID proof is already pending approval.").format(existing),
				title=_("Duplicate Request"),
			)

	# ─────────────────────────────────────────────────────────
	# Recipients / permissions
	# ─────────────────────────────────────────────────────────
	def get_approver_users(self):
		return get_employee_users(get_mapped_employees(self))

	def can_user_act(self, user=None):
		user = user or frappe.session.user
		if user == "Administrator" or "System Manager" in frappe.get_roles(user):
			return True
		return user in self.get_approver_users()

	def _security_users(self):
		users = get_employee_users([self.security_officer]) if self.security_officer else []
		return users or [self.owner]

	def _set_status(self, status, **values):
		self.update(values)
		self.status = status
		self.flags.vms_status_change = True
		self.save(ignore_permissions=True)

	# ─────────────────────────────────────────────────────────
	# Transitions
	# ─────────────────────────────────────────────────────────
	def submit_for_approval(self):
		if self.status != DRAFT:
			frappe.throw(_("Only Draft requests can be sent for approval."))
		if frappe.get_cached_doc("VMS Settings").get("require_item_declaration") and not self.visitor_items:
			frappe.throw(
				_("Item declaration is required — add at least one item the visitor is carrying."),
				title=_("Items Not Declared"),
			)
		approvers = self.get_approver_users()
		if not approvers:
			frappe.throw(_("None of the mapped employees has a user login, so nobody can approve this request."))
		self._set_status(PENDING, request_datetime=now_datetime())
		notify_users(
			approvers,
			_("Gate Entry Request: {0} wants to visit").format(self.visitor_full_name),
			self._approval_message(),
			self.doctype,
			self.name,
		)

	def approve(self):
		self._ensure_pending_and_allowed()
		visitor_pass = self._create_visitor_pass()
		self._set_status(
			APPROVED,
			approved_by=frappe.session.user,
			approval_datetime=now_datetime(),
			visitor_pass=visitor_pass.name,
		)
		notify_users(
			self._security_users(),
			_("Approved: {0} — Pass {1}").format(self.visitor_full_name, visitor_pass.name),
			_("<p>Gate Entry Request <b>{0}</b> was approved by {1}.</p><p>Visitor Pass: <a href='{2}'>{3}</a> ({4}). Proceed with the standard check-in.</p>").format(
				self.name,
				frappe.utils.get_fullname(frappe.session.user),
				doc_link("Visitor Pass", visitor_pass.name),
				visitor_pass.name,
				visitor_pass.workflow_state or visitor_pass.status,
			),
			self.doctype,
			self.name,
		)
		return visitor_pass.name

	def reject(self, reason=None):
		self._ensure_pending_and_allowed()
		self._set_status(REJECTED, rejected_by=frappe.session.user, rejection_reason=reason)
		notify_users(
			self._security_users(),
			_("Rejected: {0}").format(self.visitor_full_name),
			_("<p>Gate Entry Request <b>{0}</b> was rejected by {1}.</p><p>Reason: {2}</p><p>Do not allow entry.</p>").format(
				self.name, frappe.utils.get_fullname(frappe.session.user), frappe.utils.escape_html(reason or "-")
			),
			self.doctype,
			self.name,
		)

	def expire(self):
		if self.status != PENDING:
			return
		self._set_status(EXPIRED)
		notify_users(
			self._security_users(),
			_("Expired: {0}").format(self.visitor_full_name),
			_("<p>Gate Entry Request <b>{0}</b> got no response in time and has expired. Do not allow entry.</p>").format(self.name),
			self.doctype,
			self.name,
		)

	def _ensure_pending_and_allowed(self):
		if self.status != PENDING:
			frappe.throw(_("This request is not pending approval (status: {0}).").format(self.status))
		if not self.can_user_act():
			frappe.throw(_("Only the person being visited (or a member of the mapped group) can act on this request."), frappe.PermissionError)

	# ─────────────────────────────────────────────────────────
	# Visitor Pass
	# ─────────────────────────────────────────────────────────
	def _host_employee(self):
		if self.mapping_type != GROUP:
			return self.person_to_visit
		members = get_group_employees(self.visitor_group)
		approver = get_employee_for_user()
		return approver if approver in members else (members[0] if members else None)

	def _create_visitor_pass(self):
		checkin = now_datetime()
		checkout = add_to_date(checkin, hours=flt(self.expected_duration) or 2)
		if checkout.date() != checkin.date():
			checkout = checkin.replace(hour=23, minute=59, second=0, microsecond=0)

		vp = frappe.new_doc("Visitor Pass")
		vp.update(
			{
				"visitor_type": self.visitor_type,
				"entry_type": "New",
				"request_channel": "Gate",
				"visitor_full_name": self.visitor_full_name,
				"mobile_number": self.mobile_number,
				"email_id": self.email_id,
				"company__organisation": self.visitor_company,
				"id_proof_type": self.id_proof_type,
				"id_proof_number": self.id_proof_number,
				"id_proof_scan": self.id_proof_scan,
				"visitor_photo": self.visitor_photo,
				"mapping_type": self.mapping_type,
				"visitor_group": self.visitor_group if self.mapping_type == GROUP else None,
				"person_to_visit": self._host_employee(),
				"purpose_of_visit": self.purpose_of_visit,
				"visit_date": today(),
				"expected_checkin": checkin.strftime("%H:%M:%S"),
				"expected_checkout": checkout.strftime("%H:%M:%S"),
				"meal_required": self.meal_required,
				"number_of_people": cint(self.number_of_visitors) or 1,
				"cab_required": self.cab_required,
				"hotel_required": self.hotel_required,
				"factory_tour_required": self.factory_tour_required,
				"greeting_required": self.greeting_required,
				"gate_entry_request": self.name,
			}
		)
		for item in self.visitor_items or []:
			vp.append("visitor_items", {f: item.get(f) for f in ITEM_FIELDS})
		if self.visitor_type == "Supplier":
			# the pass form makes Meeting Subject mandatory for a Supplier "Meeting" visit
			vp.supplier_visit_mode = vp.supplier_visit_mode or "Meeting"
			vp.meeting_subject = (self.purpose_of_visit or "")[:140]
		vp.flags.ignore_permissions = True
		vp.insert()

		if self.visitor_type == "VIP":
			# VIP protocol (HOD -> CEO) still applies; the host's approval only lets the
			# request through the gate desk into the normal VIP lane.
			from frappe.model.workflow import apply_workflow

			vp = apply_workflow(vp, "Submit")
		else:
			# The host has approved: submitting sets the pass Approved and runs the
			# standard on_submit (badge, QR code, approval email, food alert).
			vp.flags.ignore_permissions = True
			vp.submit()
		return vp

	def _items_summary(self):
		return ", ".join(f"{i.item_name} × {flt(i.quantity):g}" for i in self.visitor_items or []) or "-"

	def _approval_message(self):
		flags = [
			label
			for field, label in (
				("meal_required", _("Meal")),
				("cab_required", _("Cab")),
				("hotel_required", _("Hotel")),
				("factory_tour_required", _("Factory Tour")),
				("greeting_required", _("Greeting")),
			)
			if self.get(field)
		]
		rows = [
			(_("Visitor"), self.visitor_full_name),
			(_("Company"), self.visitor_company or "-"),
			(_("Visitor Type"), self.visitor_type),
			(_("Mobile"), self.mobile_number),
			(_("Number of Visitors"), self.number_of_visitors or 1),
			(_("Purpose"), self.purpose_of_visit),
			(_("Expected Duration"), _("{0} hours").format(flt(self.expected_duration) or 2)),
			(_("Gate"), self.gate_name or "-"),
			(_("Hospitality"), ", ".join(flags) or "-"),
			(_("Items Carried"), self._items_summary()),
		]
		table = "".join(
			f"<tr><td style='padding:4px 8px;'><b>{k}</b></td><td style='padding:4px 8px;'>{frappe.utils.escape_html(str(v))}</td></tr>"
			for k, v in rows
		)
		return (
			f"<p>A visitor is waiting at the gate and wants to meet you.</p>"
			f"<table style='border-collapse:collapse;'>{table}</table>"
			f"<p><a href='{doc_link(self.doctype, self.name)}'>Open the request to Approve or Reject</a></p>"
		)


# ─────────────────────────────────────────────────────────
# Whitelisted actions
# ─────────────────────────────────────────────────────────
@frappe.whitelist()
def submit_gate_entry_request(gate_entry_request):
	doc = frappe.get_doc("Gate Entry Request", gate_entry_request)
	doc.check_permission("write")
	doc.submit_for_approval()
	return doc.status


@frappe.whitelist()
def approve_gate_entry(gate_entry_request):
	doc = frappe.get_doc("Gate Entry Request", gate_entry_request)
	return {"visitor_pass": doc.approve()}


@frappe.whitelist()
def reject_gate_entry(gate_entry_request, reason=None):
	doc = frappe.get_doc("Gate Entry Request", gate_entry_request)
	doc.reject(reason)
	return doc.status


def expire_pending_gate_entries():
	"""Hourly: expire requests nobody acted on within VMS Settings timeout."""
	timeout_hours = flt(frappe.db.get_single_value("VMS Settings", "gate_entry_timeout_hours")) or 2
	cutoff = add_to_date(now_datetime(), hours=-timeout_hours)
	for name in frappe.get_all(
		"Gate Entry Request", filters={"status": PENDING, "request_datetime": ["<", cutoff]}, pluck="name"
	):
		try:
			frappe.get_doc("Gate Entry Request", name).expire()
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(frappe.get_traceback(), f"Gate Entry Request expiry failed: {name}")


# ─────────────────────────────────────────────────────────
# Row-level permissions (wired in hooks.py)
# ─────────────────────────────────────────────────────────
def get_permission_query_conditions(user=None):
	user = user or frappe.session.user
	if user == "Administrator":
		return None
	roles = set(frappe.get_roles(user))
	if roles & {"System Manager", "Security"}:
		return None
	table = "`tabGate Entry Request`"
	conditions = [f"{table}.owner = {frappe.db.escape(user)}"]
	employee = get_employee_for_user(user, active_only=False)
	if employee:
		emp = frappe.db.escape(employee)
		conditions.append(f"{table}.person_to_visit = {emp}")
		conditions.append(
			f"({table}.mapping_type = 'Group' and exists (select 1 from `tabEmployee Group Table` egt"
			f" where egt.parent = {table}.visitor_group and egt.parenttype = 'Employee Group' and egt.employee = {emp}))"
		)
	return "(" + " or ".join(conditions) + ")"


def has_permission(doc, user=None, permission_type=None):
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	roles = set(frappe.get_roles(user))
	if roles & {"System Manager", "Security"}:
		return True
	if doc.owner == user:
		return True
	employee = get_employee_for_user(user, active_only=False)
	if not employee:
		return False
	mapped = doc.person_to_visit == employee or (
		doc.mapping_type == GROUP and employee in get_group_employees(doc.visitor_group)
	)
	# mapped employees read the request and act through the whitelisted Approve /
	# Reject methods; they never edit the record itself
	return bool(mapped) and permission_type in (None, "read", "print", "email", "share", "select")
