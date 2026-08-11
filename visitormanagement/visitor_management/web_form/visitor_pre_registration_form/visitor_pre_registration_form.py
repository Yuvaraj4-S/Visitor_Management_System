import frappe
from frappe.utils import escape_html, format_date, format_time

from visitormanagement.visitor_management import settings as vms_settings
from visitormanagement.visitor_management.doctype.visitor_invitation.visitor_invitation import (
	get_web_form_context,
)


ALWAYS_LOCKED_FIELDS = {
	"visitor_type",
	"email_id",
	"visit_date",
	"expected_checkin",
	"expected_checkout",
	"person_to_visit",
}

CONDITIONALLY_LOCKED_FIELDS = {
	"purpose_of_visit",
}

INTERNAL_HIDE_FIELDS = {
	"entry_type",
	"status",
	"request_channel",
	"visitor_invitation",
	"workflow_state",
	"host_department",
}

# Section labels are keyed by the Visitor Type's *layout*, so a custom type that
# reuses one of the shipped layouts hides the same section. Every one of these is
# hidden on the public form regardless — the constant exists so the list of
# type-specific sections stays in one place.
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


# Visitor Type.badge_colour is a named swatch; map it to a hex the portal can use.
BADGE_SWATCHES = {
	"Orange": "#ea580c",
	"Purple": "#7c3aed",
	"Green": "#059669",
	"Teal": "#0891b2",
	"Gold": "#ca8a04",
	"Blue": "#2563eb",
	"Red": "#dc2626",
	"Grey": "#64748b",
}


def _visitor_type_badge(visitor_type):
	"""Colour the badge from the Visitor Type master so a custom type is styled
	by its own configured colour instead of falling back to grey."""
	swatch = None
	if visitor_type:
		swatch = frappe.db.get_value("Visitor Type", visitor_type, "badge_colour")
	color = BADGE_SWATCHES.get(swatch, "#64748b")
	return (
		f'<span style="display:inline-block; padding:2px 10px; border-radius:6px; '
		f'font-size:0.78rem; font-weight:700; '
		f'background:{color}18; color:{color}; letter-spacing:0.02em;">'
		f'{escape_html(visitor_type or "-")}</span>'
	)


def _hide_internal_fields(context):
	"""Hide internal/system fields that visitors should never see."""
	if not getattr(context, "web_form_doc", None):
		return

	for field in context.web_form_doc.web_form_fields:
		if field.fieldname in INTERNAL_HIDE_FIELDS:
			field.hidden = 1


def _theme_block():
	"""Inline the site's palette as CSS variables.

	The stylesheet is written against these variables, so a site themes the whole
	page by setting one colour in VMS Settings — no stylesheet edit, and nothing
	sector-specific baked into the app.
	"""
	p = vms_settings.brand_palette()
	return (
		"<style>:root{"
		f"--vr-brand:{p['brand']};"
		f"--vr-brand-dark:{p['brand_dark']};"
		f"--vr-brand-soft:{p['brand_soft']};"
		f"--vr-brand-border:{p['brand_border']};"
		f"--vr-brand-rgb:{p['brand_rgb']};"
		f"--vr-on-brand:{p['on_brand']};"
		f"--vr-brand-tint:rgba({p['brand_rgb']},0.08);"
		"}</style>"
	)


def _brand_header():
	"""Optional logo / organisation lockup above the form title."""
	b = vms_settings.portal_branding()
	if not b["logo"] and not b["organisation"]:
		return ""
	logo = (
		f'<img class="vm-brand-logo" src="{escape_html(b["logo"])}" alt="" />'
		if b["logo"] else ""
	)
	org = (
		f'<span class="vm-brand-name">{escape_html(b["organisation"])}</span>'
		if b["organisation"] else ""
	)
	return f'<div class="vm-brand">{logo}{org}</div>'


def _brand_footer():
	b = vms_settings.portal_branding()
	if not b["footer_note"]:
		return ""
	return f'<div class="vm-portal-footer">{escape_html(b["footer_note"])}</div>'


def get_context(context):
	context.no_cache = 1

	theme = _theme_block() + _brand_header()
	trailing = _brand_footer()

	token = (frappe.form_dict.get("token") or "").strip()
	if not token:
		_hide_internal_fields(context)
		# Keep whatever introduction the web form record carries, themed.
		context.introduction_text = theme + (context.introduction_text or "") + trailing
		return

	invitation_context = get_web_form_context(token)
	context.invitation_context = invitation_context
	values = invitation_context.get("values") or {}
	boot = _boot_script(invitation_context, values)

	if not invitation_context.get("valid"):
		context.introduction_text = f"""
			{theme}{boot}
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
	purpose = (values.get("purpose_of_visit") or "").strip()

	# Build locked fields set — only lock purpose_of_visit if host filled it
	locked_host_fields = set(ALWAYS_LOCKED_FIELDS)
	for fn in CONDITIONALLY_LOCKED_FIELDS:
		if (values.get(fn) or "").strip():
			locked_host_fields.add(fn)

	context.introduction_text = f"""
		{theme}{boot}
		<div class="vm-status-panel vm-status-success">
			<div class="vm-status-title">Invitation Verified</div>
			<div class="vm-status-message">
				Your invitation has been verified. Fields marked as locked were set by your host.
				Please fill in your personal details, identity documents, and any additional information required.
			</div>
		</div>
		{trailing}
	"""

	if getattr(context, "web_form_doc", None):
		# Hide ALL visitor-type specific sections — keep only the core 4:
		# Visitor Profile, Visit Details, Identity Documents (+ Visitor Items is injected via JS)
		hide_sections = set(TYPE_SECTION_LABELS.values())

		hiding_section = False
		for field in context.web_form_doc.web_form_fields:
			if field.fieldname in INTERNAL_HIDE_FIELDS:
				field.hidden = 1
			elif field.fieldname in locked_host_fields:
				# Show host-set fields inline in the form, but lock editing
				field.read_only = 1

			if field.fieldtype == "Section Break":
				hiding_section = field.label in hide_sections
			if hiding_section:
				field.hidden = 1
