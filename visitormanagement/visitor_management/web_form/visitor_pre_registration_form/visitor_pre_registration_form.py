import inspect

import frappe
from frappe import _
from frappe.utils import escape_html, format_date, format_time

from visitormanagement.visitor_management import portal
from visitormanagement.visitor_management import settings as vms_settings
from visitormanagement.visitor_management.doctype.visitor_invitation import (
	visitor_invitation as invitation_module,
)

# The page render below calls the invitation lookup directly, not as an API
# request. The whitelisted endpoint carries @rate_limit(limit=30/hour), and in
# Frappe 15 that wrapper counts every call made while a request is active
# (frappe/rate_limiter.py, key "rl:<cmd>:<ip>" with cmd None on a page view),
# so page views were rate-limited too and refused after 30 an hour. `inspect.unwrap`
# follows the decorators' functools.wraps chain (__wrapped__) to the plain
# function; the endpoint itself keeps its limit and behaviour unchanged.
#
# The endpoint is reached through its module, not imported by name. A
# whitelisted function that is also a name in this module can be called as
# `<this module>.get_web_form_context` as well (frappe.get_attr resolves any
# module attribute, and the whitelist holds the function, not its path) — and
# Frappe's limiter, whose key carries the path used, counted those calls in a
# bucket of their own.
_load_invitation_context = inspect.unwrap(invitation_module.get_web_form_context)

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


def _js(value):
	"""A value as a JavaScript literal that is safe inside a <script> block.

	JSON alone is not: a visitor name of `</script><script>...` closes the block.
	Same escaping Frappe's own web form template applies to `frappe.web_form_doc`.
	"""
	return (
		frappe.as_json(value, indent=None)
		.replace("<", "\\u003c")
		.replace(">", "\\u003e")
		.replace("&", "\\u0026")
	)


def _boot_script(invitation_context, values):
	return f"""
		<script>
			window.vmInvitationValid = {_js(bool(invitation_context.get("valid")))};
			window.vmInvitationValues = {_js(values or {})};
			window.vmInvitationName = {_js(invitation_context.get("invitation"))};
			window.vmInvitationMessage = {_js(invitation_context.get("message"))};
		</script>
	"""


def _closed_script():
	"""Tell the form script there is nothing to fill in on this page."""
	return "<script>window.vmFormClosed = true;</script>"


def _status_panel(kind, title, message, hint=None):
	hint_html = f'<br><span class="vm-status-hint">{escape_html(hint)}</span>' if hint else ""
	return f"""
		<div class="vm-status-panel vm-status-{kind}">
			<div>
				<div class="vm-status-title">{escape_html(title)}</div>
				<div class="vm-status-message">{escape_html(message)}{hint_html}</div>
			</div>
		</div>
	"""


def _consent_block():
	"""The privacy notice and its tick box, when the site asks for one.

	Rendered here, hidden, and moved above the Submit button by the form script.
	The notice is the site's own text (VMS Settings), made safe for a public
	page by portal.privacy_notice_html. The tick only lets the form be sent:
	the server checks it again and it is the server that records the time and
	the notice version on the pass (portal.submit_pre_registration).
	"""
	if not portal.consent_required():
		return ""
	return f"""
		<div class="vm-custom-block vm-consent vm-form-hidden" id="vm-consent">
			<div class="vm-consent-title">{escape_html(_("Privacy notice"))}</div>
			<div class="vm-consent-notice">{portal.privacy_notice_html()}</div>
			<label class="vm-consent-check" for="vm-consent-check">
				<input type="checkbox" id="vm-consent-check" />
				<span>{escape_html(_("I have read this notice and agree to my details being used for this visit."))}</span>
			</label>
			<div class="vm-consent-error" role="alert"></div>
		</div>
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


def _hide_every_field(context):
	"""Nothing to fill in: a dead invitation link, or a site that takes no walk-ins."""
	if not getattr(context, "web_form_doc", None):
		return

	for field in context.web_form_doc.web_form_fields:
		field.hidden = 1
		field.reqd = 0


# The pickers a visitor needs, and what each may offer. Frappe turns every Link
# on a web form into an Autocomplete and writes ALL rows of the linked DocType
# into the page (web_form.load_form_data -> get_link_options, with no filter on
# a form that needs no login) — which is how the page source of this public
# form came to list every Employee ID in the company while "Person to Visit"
# was a Link. That field is plain text now; this is the guard for the rest and
# for any Link added to the form later: a picker not named here reaches the
# browser with no options at all.
PUBLIC_PICKERS = {
	"visitor_type": ("Visitor Type", {"is_active": 1}),
	"id_proof_type": ("ID Proof Type", {"is_active": 1}),
	"custom_nationality": ("Country", {}),
}


def _limit_public_pickers(context, visitor_type=None, closed=False):
	"""Rebuild the option lists core embedded, from what a visitor may be shown.

	Also the only place "active" can be enforced on Frappe 15: the options are in
	the page before any script runs, so a retired ID Proof Type or Visitor Type
	stayed on offer whatever the form script asked for.

	`closed`: the page shows no form at all, so it carries no lists either.
	"""
	if not getattr(context, "web_form_doc", None):
		return

	# The context holds a copy in which the Links have already become
	# Autocompletes; the Web Form record still says which ones they were.
	link_fields = {
		df.fieldname
		for df in frappe.get_cached_doc("Web Form", context.web_form_doc.name).web_form_fields
		if df.fieldtype == "Link"
	}

	for field in context.web_form_doc.web_form_fields:
		if field.fieldname not in link_fields:
			continue
		picker = PUBLIC_PICKERS.get(field.fieldname)
		if closed or not picker:
			field.fieldtype = "Data"
			field.options = ""
			continue
		doctype, filters = picker
		if field.fieldname == "visitor_type" and visitor_type:
			# With an invitation the type is the host's choice; the others are
			# not the visitor's business.
			names = [visitor_type]
		else:
			names = frappe.get_all(doctype, filters=filters, pluck="name", order_by="name asc")
		field.options = "\n".join(names)


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

	theme = _theme_block() + _brand_header()
	trailing = _brand_footer()

	token = (frappe.form_dict.get("token") or "").strip()
	if not token:
		if not portal.walk_in_allowed():
			# Invitation-only site: nothing here for someone without a link, and
			# nothing about the site's pickers in the page either.
			_hide_every_field(context)
			_limit_public_pickers(context, closed=True)
			context.introduction_text = (
				theme
				+ _closed_script()
				+ _status_panel(
					"error",
					_("Invitation Required"),
					_("Pre-registration is by invitation. Please open the link in your invitation email."),
					_(
						"If you have not received one, ask the person you are visiting to send you an invitation."
					),
				)
				+ trailing
			)
			return

		_hide_internal_fields(context)
		_limit_public_pickers(context)
		# Keep whatever introduction the web form record carries, themed.
		context.introduction_text = theme + (context.introduction_text or "") + _consent_block() + trailing
		return

	invitation_context = _load_invitation_context(token)
	context.invitation_context = invitation_context
	values = invitation_context.get("values") or {}
	boot = _boot_script(invitation_context, values)

	if not invitation_context.get("valid"):
		# The link is dead: show why and nothing else. The fields used to stay on
		# the page, editable, above a Submit button that could never work.
		_hide_every_field(context)
		_limit_public_pickers(context, closed=True)
		context.introduction_text = (
			theme
			+ boot
			+ _closed_script()
			+ _status_panel(
				"error",
				_("Invitation Unavailable"),
				invitation_context.get("message")
				or _("This invitation link is invalid, expired, or has already been used."),
				_("If you believe this is an error, please contact your host for a new invitation link."),
			)
		)
		return

	# Build locked fields set — only lock purpose_of_visit if host filled it
	locked_host_fields = set(ALWAYS_LOCKED_FIELDS)
	for fn in CONDITIONALLY_LOCKED_FIELDS:
		if (values.get(fn) or "").strip():
			locked_host_fields.add(fn)

	_limit_public_pickers(context, visitor_type=values.get("visitor_type"))
	context.introduction_text = (
		theme
		+ boot
		+ _status_panel(
			"success",
			_("Invitation Verified"),
			_(
				"Your invitation has been verified. Fields marked as locked were set by your host. "
				"Please fill in your personal details, identity documents, and any additional "
				"information required."
			),
		)
		+ _consent_block()
		+ trailing
	)

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
