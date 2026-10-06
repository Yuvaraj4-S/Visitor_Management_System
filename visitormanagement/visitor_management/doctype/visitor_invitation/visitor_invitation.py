# For license information, please see license.txt

import secrets

import re
import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import add_days, cint, date_diff, get_datetime, get_time, get_url, getdate, now_datetime, today

from visitormanagement.visitor_management.lifecycle import (
	apply_hospitality_meal_plan,
	derive_hospitality_meal_plan,
	ensure_hospitality_request,
)
from visitormanagement.visitor_management.time_utils import format_time_hhmm, strip_expected_time_seconds
from visitormanagement.visitor_management.multi_day import (
	date_range_label,
	get_visit_end_date,
	validate_multi_day_range,
)


INVITATION_EXPIRY_DAYS = 7


def _coerce_datetime(value):
	if not value:
		return None

	return get_datetime(value)


def build_invitation_link(token):
	"""Build invitation URL using the current request's host so the link works
	dynamically across localhost / ngrok / production domains — no need to
	hard-code `host_name` in site_config.
	"""
	path = f"/visitor-pre-registration-form/new?token={token}"
	base = None
	# Prefer current request's host (request-context send)
	try:
		req = getattr(frappe.local, "request", None)
		if req is not None and getattr(req, "host_url", None):
			base = req.host_url.rstrip("/")
	except Exception:
		base = None
	# Fallback to Frappe's get_url (respects host_name, else infers)
	if not base:
		return get_url(path)
	return f"{base}{path}"


GUEST_DOCTYPE = "Visitor Invitation Guest"
# Per-visitor hospitality choices the host makes on the Visitors table → copied to the pass.
GUEST_PASS_FIELDS = ("meal_required", "cab_required", "factory_tour_required")
PASS_STATUSES_CLOSED = ("Checked-Out", "Cancelled", "Rejected")
LINK_STATUS_ORDER = ("Draft", "Sent", "Opened", "Saved", "Submitted", "Expired")


def get_valid_invitation_by_token(token):
	"""Resolve a visitor's invitation link. Every row of the Visitors table has its own
	token; the matching row is available as ``get_guest_row(invitation)``."""
	token = (token or "").strip()
	if not token:
		return None

	guest = frappe.db.get_value(
		GUEST_DOCTYPE,
		{"invitation_token": token, "parenttype": "Visitor Invitation"},
		["name", "parent"],
		as_dict=True,
	)
	# Links sent before the Visitors table existed carry the invitation-level token.
	name = guest.parent if guest else frappe.db.get_value("Visitor Invitation", {"invitation_token": token}, "name")
	if not name:
		return None

	doc = frappe.get_doc("Visitor Invitation", name)
	row = next((r for r in doc.visitors if guest and r.name == guest.name), None)
	if not row and not guest and doc.visitors:
		row = doc.visitors[0]
	if not row:
		return None

	if row.invitation_status in {"Submitted", "Expired"}:
		return None

	expires_on = _coerce_datetime(doc.invitation_expires_on)
	if expires_on and expires_on < now_datetime():
		expire_invitation(doc)
		return None

	doc.flags.guest_row = row
	return doc


def get_guest_row(invitation):
	return invitation.flags.get("guest_row") if invitation else None


def expire_invitation(doc):
	for row in doc.visitors:
		if row.invitation_status != "Submitted":
			update_guest_row(doc, row, {"invitation_status": "Expired"}, refresh=False)
	refresh_invitation_status(doc)


def update_guest_row(invitation, row, values, refresh=True):
	"""Persist link-tracking values on one visitor row (works on a submitted/saved parent)."""
	frappe.db.set_value(GUEST_DOCTYPE, row.name, values, update_modified=False)
	row.update(values)
	if refresh:
		refresh_invitation_status(invitation)


def summarise_guest_rows(rows):
	"""Invitation-level status + "x of y submitted" from the visitor rows."""
	statuses = [r.invitation_status or "Draft" for r in rows]
	total, submitted = len(statuses), statuses.count("Submitted")
	if not total:
		status = "Draft"
	elif submitted == total:
		status = "Submitted"
	elif submitted:
		status = "Partially Submitted"
	elif all(s == "Expired" for s in statuses):
		status = "Expired"
	else:
		status = max((s for s in statuses if s != "Expired"), key=LINK_STATUS_ORDER.index, default="Draft")
	return status, _("{0} of {1} submitted").format(submitted, total) if total else ""


def refresh_invitation_status(invitation):
	rows = invitation.visitors
	status, summary = summarise_guest_rows(rows)
	opened = [get_datetime(r.link_opened_on) for r in rows if r.link_opened_on]
	submitted = [get_datetime(r.form_submitted_on) for r in rows if r.form_submitted_on]
	values = {
		"invitation_status": status,
		"visitors_summary": summary,
		"visitor_pass": next((r.visitor_pass for r in rows if r.visitor_pass), None),
		"link_opened_on": min(opened) if opened else None,
		"form_submitted_on": max(submitted) if status == "Submitted" and submitted else None,
	}
	invitation.db_set(values, update_modified=False)


def _format_time_for_web_form(value):
	if not value:
		return ""

	return get_time(value).strftime("%H:%M")


def _format_datetime_for_web_form(value):
	if not value:
		return ""

	return get_datetime(value).strftime("%Y-%m-%d %H:%M:%S")


@frappe.whitelist(allow_guest=True)
def get_web_form_context(token):
	invitation = get_valid_invitation_by_token(token)
	if not invitation:
		return {
			"valid": False,
			"message": "This invitation link is invalid, expired, or already used.",
		}

	guest = get_guest_row(invitation)
	if guest.invitation_status in ("Draft", "Sent"):
		update_guest_row(invitation, guest, {"invitation_status": "Opened", "link_opened_on": now_datetime()})

	meal_plan = derive_hospitality_meal_plan(invitation)
	existing_pass = None
	if guest.visitor_pass and frappe.db.exists("Visitor Pass", guest.visitor_pass):
		existing_pass = frappe.get_doc("Visitor Pass", guest.visitor_pass)

	values = {
		"entry_type": "New",
		"request_channel": "Portal",
		"visitor_invitation": invitation.name,
		"visitor_type": invitation.visitor_type,
		"email_id": guest.visitor_email,
		"mobile_number": guest.visitor_mobile or "",
		"visitor_full_name": guest.visitor_full_name or "",
		"visit_date": str(invitation.visit_date) if invitation.visit_date else "",
		"multi_day_pass": invitation.multi_day_pass,
		"pass_valid_until": str(invitation.pass_valid_until) if invitation.pass_valid_until else "",
		"expected_checkin": _format_time_for_web_form(invitation.expected_checkin),
		"expected_checkout": _format_time_for_web_form(invitation.expected_checkout),
		"person_to_visit": invitation.host_employee,
		"purpose_of_visit": invitation.purpose_of_visit,
		"meal_required": guest.meal_required,
		"cab_required": guest.cab_required,
		"factory_tour_required": guest.factory_tour_required,
		"meal_type": meal_plan["meal_type"] if guest.meal_required else "",
		"assigned_meal_slots": meal_plan["assigned_meal_slots"] if guest.meal_required else "",
		"hospitality_type": meal_plan["hospitality_type"] if guest.meal_required else "",
		"service_time": _format_datetime_for_web_form(meal_plan["service_time"]) if guest.meal_required else "",
		"refreshments_required": invitation.refreshments_required,
		"conference_room": invitation.get("conference_room") or "",
	}

	if existing_pass:
		values.update(
			{
				"visitor_full_name": existing_pass.visitor_full_name,
				"mobile_number": existing_pass.mobile_number,
				"company__organisation": existing_pass.company__organisation,
				"supplier_link": existing_pass.supplier_link,
				"supplier_visit_mode": existing_pass.supplier_visit_mode,
				"visit_category": existing_pass.visit_category,
				"products_discussed": existing_pass.products_discussed,
				"meeting_outcome": existing_pass.meeting_outcome,
				"followup_date": str(existing_pass.followup_date) if existing_pass.followup_date else "",
				"contractor_link": existing_pass.contractor_link,
				"work_order_ref": existing_pass.work_order_ref,
				"tools_list": existing_pass.tools_list,
				"job_applicant_link": existing_pass.job_applicant_link,
				"position_applied": existing_pass.position_applied,
				"candidate_interview_type": existing_pass.candidate_interview_type,
				"interview_panel": existing_pass.interview_panel,
				"vip_category": existing_pass.vip_category,
				"mdceo_notified": existing_pass.mdceo_notified,
				"interpreter_required": existing_pass.interpreter_required,
				"interpreter_language": existing_pass.interpreter_language,
				"protocol_notes": existing_pass.protocol_notes,
				"meal_required": existing_pass.meal_required,
				"meal_type": existing_pass.meal_type or values.get("meal_type"),
				"assigned_meal_slots": existing_pass.assigned_meal_slots or values.get("assigned_meal_slots"),
				"hospitality_type": existing_pass.hospitality_type or values.get("hospitality_type"),
				"special_diet": existing_pass.special_diet,
				"service_time": _format_datetime_for_web_form(existing_pass.service_time)
				if existing_pass.service_time
				else values.get("service_time"),
				"refreshments_required": existing_pass.refreshments_required,
				"items_carried": existing_pass.items_carried,
				"visitor_items": [
					{
						"item_code": row.item_code,
						"item_name": row.item_name,
						"item_category": row.item_category,
						"quantity": row.quantity,
						"unit_of_measure": row.unit_of_measure,
						"description": row.description,
						"is_new_item": row.is_new_item,
						"serial_number": row.serial_number,
						"estimated_value": row.estimated_value,
					}
					for row in (existing_pass.get("visitor_items") or [])
				],
			}
		)

	return {
		"valid": True,
		"invitation": invitation.name,
		"values": values,
	}


class VisitorInvitation(Document):
	def onload(self):
		# Default Host Employee to the current user's Employee record (if any)
		if self.is_new() and not self.host_employee and frappe.session.user not in ("Administrator", "Guest"):
			emp = frappe.db.get_value(
				"Employee",
				{"user_id": frappe.session.user, "status": "Active"},
				"name",
			)
			if emp:
				self.host_employee = emp

	def validate(self):
		strip_expected_time_seconds(self)

		if not self.created_by_user:
			self.created_by_user = frappe.session.user

		if not self.invitation_status:
			self.invitation_status = "Draft"

		self._validate_visit_date()
		validate_multi_day_range(self)
		self._validate_visitors()
		self._validate_email()
		self._validate_time_range()
		self._validate_host_active()

		if not self.invitation_expires_on and self.visit_date:
			# A multi-day visitor may register any time until the visit's last day.
			self.invitation_expires_on = get_datetime(f"{get_visit_end_date(self)} 23:59:59")

		self.invitation_expires_on = _coerce_datetime(self.invitation_expires_on)

		if self.invitation_expires_on and self.invitation_expires_on < now_datetime():
			frappe.throw(_("Invitation Expiry must be a future date and time."))

		self._apply_hospitality_defaults()

	def _validate_visit_date(self):
		if not self.visit_date:
			return
		today_date = getdate(today())
		visit_date = getdate(self.visit_date)
		if visit_date < today_date:
			frappe.throw(
				_("Visit date {0} is in the past. Cannot send an invitation for a past date.").format(self.visit_date),
				title=_("Invalid Visit Date"),
			)
		if date_diff(visit_date, today_date) > 90:
			frappe.throw(
				_("Visit date cannot be more than 90 days in the future."),
				title=_("Invalid Visit Date"),
			)

	def on_update(self):
		self._sync_visitor_choices_to_passes()

	def _sync_visitor_choices_to_passes(self):
		"""Host changed Meal / Cab / Factory Tour on a visitor row after that visitor's
		pass was created — carry the change to the pass and its Hospitality Request."""
		for row in self.visitors:
			if not row.visitor_pass or not frappe.db.exists("Visitor Pass", row.visitor_pass):
				continue
			vp = frappe.get_doc("Visitor Pass", row.visitor_pass)
			if vp.docstatus == 2 or vp.status in PASS_STATUSES_CLOSED:
				continue

			wanted = {f: cint(row.get(f)) for f in GUEST_PASS_FIELDS}
			if all(cint(vp.get(f)) == v for f, v in wanted.items()):
				continue

			if vp.docstatus == 0:
				vp.update(wanted)
				vp.save(ignore_permissions=True)
			else:
				# Approved pass: hospitality fields aren't editable after submit, so write
				# them directly, recompute the meal plan and refresh the Hospitality Request.
				vp.update(wanted)
				apply_hospitality_meal_plan(vp, preserve_existing=True)
				vp.db_set(
					{
						**wanted,
						"meal_type": vp.meal_type,
						"assigned_meal_slots": vp.assigned_meal_slots,
						"hospitality_type": vp.hospitality_type,
						"service_time": vp.service_time,
					},
					update_modified=False,
				)
				ensure_hospitality_request(vp)

			changes = ", ".join(
				f"{frappe.unscrub(f).replace(' Required', '')}: {_('Yes') if v else _('No')}" for f, v in wanted.items()
			)
			vp.add_comment("Info", _("Hospitality updated from Visitor Invitation {0} — {1}").format(self.name, changes))

	def _validate_visitors(self):
		"""One row per visitor, each with a unique, valid email."""
		if not self.visitors:
			frappe.throw(_("Add at least one visitor in the Visitors table."), title=_("No Visitors"))

		seen = set()
		for row in self.visitors:
			row.visitor_email = (row.visitor_email or "").strip()
			if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", row.visitor_email):
				frappe.throw(
					_("Row {0}: Visitor Email '{1}' is not a valid email address.").format(row.idx, row.visitor_email),
					title=_("Invalid Email"),
				)
			key = row.visitor_email.lower()
			if key in seen:
				frappe.throw(
					_("Row {0}: {1} is already in the Visitors table.").format(row.idx, row.visitor_email),
					title=_("Duplicate Visitor"),
				)
			seen.add(key)
			row.invitation_status = row.invitation_status or "Draft"

		# A visitor who already has a pass cannot be removed from the invitation.
		if not self.is_new():
			kept = {r.name for r in self.visitors}
			removed = frappe.get_all(
				GUEST_DOCTYPE,
				filters={"parent": self.name, "parenttype": self.doctype, "visitor_pass": ("is", "set")},
				fields=["name", "visitor_full_name", "visitor_pass"],
			)
			for r in removed:
				if r.name not in kept:
					frappe.throw(
						_("{0} already has Visitor Pass {1} and cannot be removed.").format(
							r.visitor_full_name, r.visitor_pass
						),
						title=_("Visitor Has Pass"),
					)

		# Header fields mirror the group: first visitor's email, meal if anyone needs one.
		self.visitor_email = self.visitors[0].visitor_email
		self.meal_required = 1 if any(cint(r.meal_required) for r in self.visitors) else 0
		self.invitation_status, self.visitors_summary = summarise_guest_rows(self.visitors)

	def _validate_email(self):
		if self.visitor_email and not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", self.visitor_email):
			frappe.throw(
				_("Visitor Email '{0}' is not a valid email address.").format(self.visitor_email),
				title=_("Invalid Email"),
			)

	def _validate_time_range(self):
		if self.expected_checkin and self.expected_checkout:
			if get_time(self.expected_checkin) >= get_time(self.expected_checkout):
				frappe.throw(
					_("Expected Check-In must be before Expected Check-Out."),
					title=_("Invalid Time Range"),
				)

	def _validate_host_active(self):
		if not self.host_employee:
			return
		status = frappe.db.get_value("Employee", self.host_employee, "status")
		if status != "Active":
			frappe.throw(
				_("Host employee {0} is not Active (status: {1}).").format(self.host_employee, status or "Unknown"),
				title=_("Invalid Host"),
			)

	def _apply_hospitality_defaults(self):
		self.meal_required = frappe.utils.cint(self.meal_required)
		self.refreshments_required = frappe.utils.cint(self.refreshments_required)

		meal_plan = derive_hospitality_meal_plan(self)
		self.meal_type = meal_plan["meal_type"] if self.meal_required else None
		self.assigned_meal_slots = meal_plan["assigned_meal_slots"] if self.meal_required else None
		self.hospitality_type = meal_plan["hospitality_type"] if self.meal_required else None

	@frappe.whitelist()
	def send_invitation(self):
		if self.is_new():
			frappe.throw("Save the Visitor Invitation before sending the invitation mail.")

		rows = [r for r in self.visitors if r.invitation_status not in ("Submitted", "Expired")]
		if not rows:
			frappe.throw(_("Every visitor has already submitted the form — nothing to send."))

		sent_on = now_datetime()
		expires_on = _coerce_datetime(self.invitation_expires_on) or add_days(
			sent_on, INVITATION_EXPIRY_DAYS
		)

		if expires_on < sent_on:
			frappe.throw("Invitation Expiry must be later than the send time.")

		self.db_set({"invitation_sent_on": sent_on, "invitation_expires_on": expires_on})

		sent_to = []
		for row in rows:
			# Resending keeps the visitor's existing link so a saved draft stays reachable.
			token = row.invitation_token or secrets.token_urlsafe(24)
			link = build_invitation_link(token)
			update_guest_row(
				self,
				row,
				{
					"invitation_token": token,
					"portal_submission_url": link,
					"invitation_sent_on": sent_on,
					"invitation_status": row.invitation_status if row.invitation_status in ("Opened", "Saved") else "Sent",
				},
				refresh=False,
			)
			self._send_guest_email(row, link, expires_on)
			sent_to.append(row.visitor_email)

		# Header token/link mirror the first visitor (older code paths read them).
		first = self.visitors[0]
		self.db_set({"invitation_token": first.invitation_token, "portal_submission_url": first.portal_submission_url})
		refresh_invitation_status(self)
		return sent_to

	def _send_guest_email(self, row, link, expires_on):
		message = [
			f"Dear {frappe.utils.escape_html(row.visitor_full_name or 'Visitor')},",
			"",
			"You have received a visitor pre-registration invitation from our company.",
			f"Visitor Type: {self.visitor_type or '-'}",
			f"Host: {self.host_employee or '-'}",
			f"Visit Date: {date_range_label(self) or '-'}",
			f"Expected Check-In: {format_time_hhmm(self.expected_checkin, '-')}",
			f"Expected Check-Out: {format_time_hhmm(self.expected_checkout, '-')}",
			f"Purpose: {self.purpose_of_visit or '-'}",
			"",
			"Please use the secure link below to fill your information before arrival:",
			f"<a href=\"{link}\">{link}</a>",
			"",
			f"This invitation expires on {expires_on}.",
		]
		frappe.sendmail(
			recipients=[row.visitor_email],
			subject="Visitor Pre-Registration Invitation",
			message="<br>".join(message),
			now=True,
		)
