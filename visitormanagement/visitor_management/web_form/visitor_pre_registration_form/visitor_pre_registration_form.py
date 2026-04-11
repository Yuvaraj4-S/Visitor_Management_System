import frappe
from frappe.utils import escape_html, format_date, format_time

from visitormanagement.visitor_management.doctype.visitor_invitation.visitor_invitation import (
	get_web_form_context,
)


LOCKED_HOST_FIELDS = {
	"visitor_type",
	"email_id",
	"visit_date",
	"expected_checkin",
	"expected_checkout",
	"person_to_visit",
	"purpose_of_visit",
}

INTERNAL_HIDE_FIELDS = {
	"entry_type",
	"status",
	"request_channel",
	"visitor_invitation",
	"pre_registration_request",
	"workflow_state",
	"host_department",
}

TYPE_SECTION_LABELS = {
	"Contractor": "Contractor Details",
	"Supplier": "Supplier Details",
	"Customer": "Customer Details",
	"Candidate": "Candidate Details",
	"VIP": "VIP Details",
}


def _safe(value):
	if value in (None, "", []):
		return "-"
	return escape_html(str(value))


def _format_visit_date(value):
	if not value:
		return "-"
	try:
		return format_date(value)
	except Exception:
		return str(value)


def _format_visit_time(value):
	if not value:
		return "-"
	try:
		return format_time(value)
	except Exception:
		return str(value)


def _host_name(employee_id):
	if not employee_id:
		return "-"
	name = frappe.db.get_value("Employee", employee_id, "employee_name")
	return escape_html(name) if name else escape_html(employee_id)


def _locked_card(label, value):
	return f"""
		<div class="vm-locked-card">
			<div class="vm-locked-label">{escape_html(label)}</div>
			<div class="vm-locked-value">{value}</div>
		</div>
	"""


def _boot_script(invitation_context, values):
	return f"""
		<script>
			window.vmInvitationValid = {frappe.as_json(bool(invitation_context.get("valid")))};
			window.vmInvitationValues = {frappe.as_json(values or {})};
			window.vmInvitationName = {frappe.as_json(invitation_context.get("invitation"))};
			window.vmInvitationMessage = {frappe.as_json(invitation_context.get("message"))};
		</script>
	"""


def _visitor_type_badge(visitor_type):
	colors = {
		"Contractor": "#ea580c",
		"Customer": "#059669",
		"Candidate": "#7c3aed",
		"Supplier": "#0891b2",
		"VIP": "#ca8a04",
	}
	color = colors.get(visitor_type, "#64748b")
	return (
		f'<span style="display:inline-block; padding:2px 10px; border-radius:6px; '
		f'font-size:0.78rem; font-weight:700; '
		f'background:{color}18; color:{color}; letter-spacing:0.02em;">'
		f'{escape_html(visitor_type or "-")}</span>'
	)


def get_context(context):
	context.no_cache = 1

	token = (frappe.form_dict.get("token") or "").strip()
	if not token:
		return

	invitation_context = get_web_form_context(token)
	context.invitation_context = invitation_context
	values = invitation_context.get("values") or {}
	boot = _boot_script(invitation_context, values)

	if not invitation_context.get("valid"):
		context.introduction_text = f"""
			{boot}
			<div class="vm-status-panel vm-status-error">
				<div class="vm-status-title">Invitation Unavailable</div>
				<div class="vm-status-message">
					{escape_html(invitation_context.get("message") or "This invitation link is invalid, expired, or has already been used.")}
					<br><span style="font-size:0.8rem; color:#94a3b8; margin-top:4px; display:inline-block;">
						If you believe this is an error, please contact your host for a new invitation link.
					</span>
				</div>
			</div>
		"""
		return

	host_display = _host_name(values.get("person_to_visit"))
	visitor_type = values.get("visitor_type", "")
	type_badge = _visitor_type_badge(visitor_type)

	context.introduction_text = f"""
		{boot}
		<div class="vm-status-panel vm-status-success">
			<div class="vm-status-title">Invitation Verified</div>
			<div class="vm-status-message">
				Your invitation has been verified. The visit details below were set by your host and cannot be changed.
				Please fill in your personal details, identity documents, and any additional information required.
			</div>
		</div>

		<div class="vm-locked-sections">
			<div class="vm-locked-section">
				<div class="vm-locked-section-title">Host-Approved Visit Details</div>
				<div class="vm-locked-grid">
					{_locked_card("Visitor Type", type_badge)}
					{_locked_card("Email", _safe(values.get("email_id")))}
					{_locked_card("Visit Date", _format_visit_date(values.get("visit_date")))}
					{_locked_card("Check-In", _format_visit_time(values.get("expected_checkin")))}
					{_locked_card("Check-Out", _format_visit_time(values.get("expected_checkout")))}
					{_locked_card("Host", host_display)}
				</div>
				<div style="margin-top:10px; padding:8px 14px; border-radius:8px; background:#fff; border:1px solid #e2e8f0;">
					<div class="vm-locked-label">Purpose of Visit</div>
					<div class="vm-locked-value">{_safe(values.get("purpose_of_visit"))}</div>
				</div>
			</div>
		</div>
	"""

	if getattr(context, "web_form_doc", None):
		visitor_type = values.get("visitor_type", "")
		active_section = TYPE_SECTION_LABELS.get(visitor_type, "")
		hide_sections = {
			label for label in TYPE_SECTION_LABELS.values() if label != active_section
		}

		hiding_section = False
		for field in context.web_form_doc.web_form_fields:
			if field.fieldname in INTERNAL_HIDE_FIELDS | LOCKED_HOST_FIELDS:
				field.hidden = 1

			if field.fieldtype == "Section Break":
				hiding_section = field.label in hide_sections
			if hiding_section:
				field.hidden = 1
