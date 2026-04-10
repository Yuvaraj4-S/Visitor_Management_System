import frappe
from frappe.utils import escape_html

from visitormanagement.visitor_management.doctype.visitor_invitation.visitor_invitation import (
	get_web_form_context,
)


INTERNAL_HIDE_FIELDS = {
	"status",
	"request_channel",
	"visitor_pass",
	"visitor_invitation",
}


def _display_value(value, boolean_as_yes_no=False):
	if value in (None, "", []):
		return "-"
	if boolean_as_yes_no:
		return "Yes" if value else "No"
	return str(value)


def _locked_field(label, value):
	return f"""
		<div class="vm-locked-card">
			<div class="vm-locked-label">{escape_html(label)}</div>
			<div class="vm-locked-value">{escape_html(value)}</div>
		</div>
	"""

def get_context(context):
	context.no_cache = 1

	token = (frappe.form_dict.get("token") or "").strip()
	if not token:
		return

	invitation_context = get_web_form_context(token)
	context.invitation_context = invitation_context
	values = invitation_context.get("values") or {}
	context.script = f"""
		window.vmInvitationValid = {frappe.as_json(bool(invitation_context.get("valid")))};
		window.vmInvitationValues = {frappe.as_json(values)};
		window.vmInvitationName = {frappe.as_json(invitation_context.get("invitation"))};
		window.vmInvitationMessage = {frappe.as_json(invitation_context.get("message"))};
	"""

	if not invitation_context.get("valid"):
		context.introduction_text = f"""
			<div class="vm-status-panel vm-status-error">
				<div class="vm-status-title">Invitation Not Available</div>
				<div class="vm-status-message">{escape_html(invitation_context.get("message") or "This invitation link is invalid, expired, or already used.")}</div>
			</div>
		"""
		return

	context.introduction_text = f"""
		<div class="vm-status-panel vm-status-success">
			<div class="vm-status-title">Invitation Verified</div>
			<div class="vm-status-message">Host-approved visit details are prefilled inside the form below. Please review them and complete only your personal information.</div>
		</div>
		<div class="vm-locked-sections">
			<div class="vm-locked-section">
				<div class="vm-locked-section-title">Host Approved Visitor Details</div>
				<div class="vm-locked-grid">
					{_locked_field("Visitor Type", _display_value(values.get("visitor_type")))}
					{_locked_field("Visitor Email", _display_value(values.get("email_id")))}
					{_locked_field("Visit Date", _display_value(values.get("visit_date")))}
					{_locked_field("Expected Check-In", _display_value(values.get("expected_checkin")))}
					{_locked_field("Expected Check-Out", _display_value(values.get("expected_checkout")))}
					{_locked_field("Person to Visit", _display_value(values.get("person_to_visit")))}
					{_locked_field("Purpose of Visit", _display_value(values.get("purpose_of_visit")))}
				</div>
			</div>
			<div class="vm-locked-section">
				<div class="vm-locked-section-title">Hospitality Details</div>
				<div class="vm-locked-grid">
					{_locked_field("Meal Required", _display_value(values.get("meal_required"), boolean_as_yes_no=True))}
					{_locked_field("Meal Type", _display_value(values.get("meal_type")))}
					{_locked_field("Meal Slots", _display_value(values.get("assigned_meal_slots")))}
					{_locked_field("Hospitality Type", _display_value(values.get("hospitality_type")))}
					{_locked_field("Refreshments Required", _display_value(values.get("refreshments_required"), boolean_as_yes_no=True))}
					{_locked_field("Conference Room", _display_value(values.get("conference_room")))}
				</div>
			</div>
		</div>
	"""

	if getattr(context, "web_form_doc", None):
		for field in context.web_form_doc.web_form_fields:
			if field.fieldname in INTERNAL_HIDE_FIELDS:
				field.hidden = 1
