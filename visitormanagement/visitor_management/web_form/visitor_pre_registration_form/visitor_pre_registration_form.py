import frappe
from frappe import _
from frappe.utils import escape_html, format_date, format_time

from visitormanagement.visitor_management import settings as vms_settings
from visitormanagement.visitor_management.doctype.visitor_invitation.visitor_invitation import (
	get_web_form_context,
)
from visitormanagement.visitor_management.portal import invitation_required_message

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

# The pick-lists this anonymous page carries, and which records go in them.
#
# Frappe turns every Link field of a web form into a pick-list built on the
# server and written into the page — for a form that needs no login, that is
# every record of the linked DocType, to anyone on the internet
# (frappe/website/doctype/web_form/web_form.py get_link_options). A filter set
# from the form's script cannot change it: the list is already in the HTML.
# So the lists are decided here: fieldname -> (DocType, filters). A linked field
# that is named neither here nor in PORTAL_PUBLIC_LISTS gets no list at all
# (see _restrict_link_options).
PORTAL_PICK_LISTS = {
	"visitor_type": ("Visitor Type", {"is_active": 1}),
	"id_proof_type": ("ID Proof Type", {"is_active": 1}),
}

# Link fields whose whole list is public reference data, left as Frappe built it.
PORTAL_PUBLIC_LISTS = {"custom_nationality"}

# Visitor Pass makes Company / Organisation mandatory for these layouts
# (visitor_pass.json, company__organisation.mandatory_depends_on). The portal
# asks for the same, or the host receives a pass they cannot save.
COMPANY_REQUIRED_LAYOUTS = ("Contractor", "Supplier", "Customer")


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


def _js(value):
	"""A value as a JavaScript literal that is safe inside an inline <script>.

	JSON alone is not: a "</script>" inside any string (a purpose of visit, a
	Visitor Type name) would end the script block and be read as markup.
	"""
	return frappe.as_json(value).replace("<", "\\u003c")


def _boot_script(invitation_context, values):
	return f"""
		<script>
			window.vmInvitationValid = {_js(bool(invitation_context.get("valid")))};
			window.vmInvitationValues = {_js(values or {})};
			window.vmInvitationName = {_js(invitation_context.get("invitation"))};
			window.vmInvitationMessage = {_js(invitation_context.get("message"))};
		</script>
	"""


def _config_script(walk_in_allowed):
	"""What the form's script needs to know on every visit, invited or not."""
	company_required_types = frappe.get_all(
		"Visitor Type",
		filters={"is_active": 1, "detail_layout": ("in", COMPANY_REQUIRED_LAYOUTS)},
		pluck="name",
	)
	return (
		"<script>"
		f"window.vmCompanyRequiredTypes = {_js(company_required_types)};"
		f"window.vmWalkInAllowed = {_js(bool(walk_in_allowed))};"
		"</script>"
	)


def _invitation_required_panel():
	"""Shown instead of the form to a visitor without an invitation link while walk-ins are off.

	The form itself is hidden by the page's own stylesheet rule, written here so it
	holds before any script runs. The server refuses such a submission and its
	uploads anyway (portal._refuse_without_invitation, portal_upload._refuse_without_invitation).
	"""
	return f"""
		<style>.web-form {{ display: none !important; }}</style>
		<div class="vm-status-panel vm-status-info" role="status">
			<div>
				<div class="vm-status-title">{escape_html(_("Invitation Required"))}</div>
				<div class="vm-status-message">{escape_html(invitation_required_message())}</div>
			</div>
		</div>
	"""


def _restrict_link_options(context):
	"""Replace the pick-lists Frappe built with the ones this page may show.

	By the time this runs Frappe has already converted each Link field to an
	Autocomplete holding every record of its DocType. Visitor Type and ID Proof
	Type are rebuilt from the active records only, so a type an administrator
	switched off is no longer offered to visitors. Any other list that was not
	deliberately allowed is dropped and the field becomes a plain text box —
	"Person to Visit" used to put the ID of every Employee into this page.
	"""
	if not getattr(context, "web_form_doc", None):
		return

	for field in context.web_form_doc.web_form_fields:
		if field.fieldtype != "Autocomplete" or field.fieldname in PORTAL_PUBLIC_LISTS:
			continue

		source = PORTAL_PICK_LISTS.get(field.fieldname)
		if not source:
			field.fieldtype = "Data"
			field.options = None
			continue

		doctype, filters = source
		field.options = "\n".join(frappe.get_all(doctype, filters=filters, pluck="name", order_by="name asc"))


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
		f"font-size:0.78rem; font-weight:700; "
		f'background:{color}18; color:{color}; letter-spacing:0.02em;">'
		f"{escape_html(visitor_type or '-')}</span>"
	)


def _hide_internal_fields(context):
	"""Hide internal/system fields that visitors should never see."""
	if not getattr(context, "web_form_doc", None):
		return

	for field in context.web_form_doc.web_form_fields:
		if field.fieldname in INTERNAL_HIDE_FIELDS:
			field.hidden = 1


def _apply_home_country(context):
	"""The form ships with India as the home country; use the configured one, so
	Nationality defaults to it and Visa Copy is asked of everyone else."""
	if not getattr(context, "web_form_doc", None):
		return

	home = vms_settings.home_country()
	foreign = f"eval:doc.custom_nationality && doc.custom_nationality != {frappe.as_json(home)}"
	for field in context.web_form_doc.web_form_fields:
		if field.fieldname == "custom_nationality":
			field.default = home
		elif field.fieldname == "custom_visa_copy":
			field.depends_on = foreign
			field.mandatory_depends_on = foreign


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
	logo = f'<img class="vm-brand-logo" src="{escape_html(b["logo"])}" alt="" />' if b["logo"] else ""
	org = f'<span class="vm-brand-name">{escape_html(b["organisation"])}</span>' if b["organisation"] else ""
	return f'<div class="vm-brand">{logo}{org}</div>'


def _brand_footer():
	b = vms_settings.portal_branding()
	if not b["footer_note"]:
		return ""
	return f'<div class="vm-portal-footer">{escape_html(b["footer_note"])}</div>'


def get_context(context):
	context.no_cache = 1
	_apply_home_country(context)
	_restrict_link_options(context)

	walk_in_allowed = vms_settings.pre_registration_without_invitation_allowed()
	theme = _theme_block() + _config_script(walk_in_allowed) + _brand_header()
	trailing = _brand_footer()

	token = (frappe.form_dict.get("token") or "").strip()
	if not token and not walk_in_allowed:
		context.introduction_text = theme + _invitation_required_panel() + trailing
		return

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
