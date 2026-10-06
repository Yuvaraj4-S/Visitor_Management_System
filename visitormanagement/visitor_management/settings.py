"""Typed accessors for VMS Settings.

Everything the app used to hardcode — meal windows, the advance-booking ceiling,
invitation expiry, the no-show grace window, the phone country code — is read
through here so a site can retune the flow without a code change. Each accessor
falls back to the value that was previously hardcoded, so behaviour is unchanged
until an admin edits the setting.

`get_cached_doc` keeps this cheap: VMS Settings is a small Single read on nearly
every Visitor Pass save.
"""

import frappe
from frappe.utils import cint, flt

# Values the app hardcoded before these became settings.
DEFAULT_MEAL_WINDOWS = (
	("Breakfast", "08:00:00", "09:00:00"),
	("Lunch", "13:00:00", "14:00:00"),
	("Dinner", "20:00:00", "21:30:00"),
)
DEFAULT_MAX_ADVANCE_DAYS = 90
DEFAULT_INVITATION_EXPIRY_DAYS = 7
DEFAULT_NO_SHOW_GRACE_HOURS = 4
DEFAULT_COUNTRY_CODE = "91"
DEFAULT_HOME_COUNTRY = "India"
DEFAULT_FEVER_THRESHOLD_C = 37.5
DEFAULT_MAX_PORTAL_SUBMISSIONS_PER_HOUR = 20


def _settings():
	try:
		return frappe.get_cached_doc("VMS Settings")
	except Exception:
		# Single may not exist yet during install / very early migrate.
		return None


def _int(fieldname, fallback):
	doc = _settings()
	if not doc:
		return fallback
	value = cint(getattr(doc, fieldname, 0))
	return value if value > 0 else fallback


def _float(fieldname, fallback):
	doc = _settings()
	if not doc:
		return fallback
	value = flt(getattr(doc, fieldname, 0))
	return value if value else fallback


def meal_windows():
	"""[(label, start, end)] — configured windows, else the built-in three."""
	doc = _settings()
	rows = list(getattr(doc, "meal_windows", None) or []) if doc else []
	windows = [
		(r.meal_label, str(r.start_time), str(r.end_time))
		for r in rows
		if r.meal_label and r.start_time and r.end_time
	]
	return windows or list(DEFAULT_MEAL_WINDOWS)


def max_advance_booking_days():
	"""How many days ahead a visit may be scheduled: the setting, or the default.

	Always at least 1. The VMS Settings form refuses anything lower
	(VMSSettings._validate_policy_numbers: "0 would disable the rule silently"),
	and a value that is not a positive number — never saved, or written as 0 by
	an import or an older version — is read as "not set" and gives the default,
	like every other policy number here (`_int`). It used to be passed through,
	and the callers treated 0 as "no ceiling": a rule the form says cannot be
	switched off could be off without anybody having chosen that.
	"""
	return _int("max_advance_booking_days", DEFAULT_MAX_ADVANCE_DAYS)


def invitation_expiry_days():
	return _int("invitation_expiry_days", DEFAULT_INVITATION_EXPIRY_DAYS)


def no_show_grace_hours():
	return _int("no_show_grace_hours", DEFAULT_NO_SHOW_GRACE_HOURS)


def fever_threshold_c():
	"""°C at/above which a Contact Trace Record's exposure risk is classified High.

	Used to be hardcoded (37.5) in lifecycle.py. Health policy differs by site
	and authority, so it is configurable; 0 is read as unset, same as every
	other numeric setting in this module.
	"""
	return _float("fever_threshold_c", DEFAULT_FEVER_THRESHOLD_C)


def max_portal_submissions_per_hour():
	"""Guest pre-registration submissions allowed per identity, per hour.

	Used to be the literal 20 written independently in two places in portal.py
	(a module constant and an `@rate_limit` decorator argument), which could
	drift from each other. Both now read this one setting.
	"""
	return _int("max_portal_submissions_per_hour", DEFAULT_MAX_PORTAL_SUBMISSIONS_PER_HOUR)


def country_code():
	doc = _settings()
	raw = (getattr(doc, "default_country_code", None) or "") if doc else ""
	digits = "".join(c for c in str(raw) if c.isdigit())
	return digits or DEFAULT_COUNTRY_CODE


def home_country():
	doc = _settings()
	return (getattr(doc, "home_country", None) if doc else None) or DEFAULT_HOME_COUNTRY


def flag(fieldname, fallback=0):
	"""Read a Check setting."""
	doc = _settings()
	if not doc:
		return cint(fallback)
	return cint(getattr(doc, fieldname, fallback))


DEFAULT_SECURITY_ALERT_ROLES = ("Security", "System Manager")
DEFAULT_DIGEST_ROLES = (
	"Hospitality Manager",
	"Hospitality User",
	"Transport Coordinator",
	"Front Office Executive",
	"Factory Tour Coordinator",
	"Greeting Staff",
)


def _roles(fieldname, fallback):
	doc = _settings()
	rows = list(getattr(doc, fieldname, None) or []) if doc else []
	roles = [r.role for r in rows if r.role]
	return roles or list(fallback)


def security_alert_roles():
	"""Roles notified when a blacklisted visitor is matched."""
	return _roles("security_alert_roles", DEFAULT_SECURITY_ALERT_ROLES)


def digest_recipient_roles():
	"""Roles receiving the daily hospitality digest."""
	return _roles("digest_recipient_roles", DEFAULT_DIGEST_ROLES)


def admin_email():
	"""Always-on recipient for VMS security alerts."""
	doc = _settings()
	value = (getattr(doc, "admin_email", None) or "").strip() if doc else ""
	return value or None


DEFAULT_BRAND = "#1A56DB"


def _clamp(v):
	return max(0, min(255, round(v)))


def _hex_to_rgb(value):
	value = (value or "").strip().lstrip("#")
	if len(value) == 3:
		value = "".join(c * 2 for c in value)
	if len(value) != 6:
		return None
	try:
		return tuple(int(value[i : i + 2], 16) for i in (0, 2, 4))
	except ValueError:
		return None


def _mix(rgb, target, amount):
	"""Blend `rgb` toward `target` by `amount` (0..1)."""
	return tuple(_clamp(c + (t - c) * amount) for c, t in zip(rgb, target, strict=False))


def _rgb_to_hex(rgb):
	return "#{:02x}{:02x}{:02x}".format(*rgb)


def _relative_luminance(rgb):
	def channel(c):
		c = c / 255
		return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

	r, g, b = (channel(c) for c in rgb)
	return 0.2126 * r + 0.7152 * g + 0.0722 * b


def brand_palette():
	"""Derive a full portal palette from the single configured brand colour.

	Computed server-side rather than with CSS color-mix() so the result does not
	depend on browser support, and so the on-brand text colour can be chosen from
	real contrast rather than guessed.
	"""
	doc = _settings()
	raw = (getattr(doc, "portal_brand_colour", None) or "") if doc else ""
	rgb = _hex_to_rgb(raw) or _hex_to_rgb(DEFAULT_BRAND)

	white, black = (255, 255, 255), (0, 0, 0)
	# Text sitting on the brand colour: pick whichever of black/white contrasts more.
	on_brand = "#ffffff" if _relative_luminance(rgb) < 0.45 else "#0f172a"

	return {
		"brand": _rgb_to_hex(rgb),
		"brand_dark": _rgb_to_hex(_mix(rgb, black, 0.18)),
		"brand_soft": _rgb_to_hex(_mix(rgb, white, 0.88)),
		"brand_border": _rgb_to_hex(_mix(rgb, white, 0.62)),
		"brand_rgb": "{}, {}, {}".format(*rgb),
		"on_brand": on_brand,
	}


def portal_branding():
	"""Logo / organisation / footer shown on the public form."""
	doc = _settings()
	get = (lambda f: (getattr(doc, f, None) or "").strip()) if doc else (lambda f: "")
	return {
		"logo": get("portal_logo"),
		"organisation": get("portal_organisation_name"),
		"footer_note": get("portal_footer_note"),
	}


def blacklist_action():
	doc = _settings()
	return (getattr(doc, "blacklist_action", None) if doc else None) or "Block Entry"


# ─────────────────────────────────────────────────────────
# Public form: privacy notice, consent, walk-ins
# ─────────────────────────────────────────────────────────
DEFAULT_PRIVACY_NOTICE_VERSION = "1"

# The standard notice, shown while VMS Settings -> Privacy Notice Text is blank.
# English source text; `privacy_notice_text()` returns it translated.
DEFAULT_PRIVACY_NOTICE = (
	"We collect your name, contact details, photograph and government ID to confirm who you are, "
	"issue your visitor pass, keep our premises secure and meet our legal duties. Your details are "
	"seen only by the staff who host, approve and check in visitors, and are kept only for as long "
	"as these purposes and the law require. You can ask the organisation you are visiting to "
	"correct or delete them."
)


def _default_privacy_notice():
	from frappe import _

	# Written out again as a literal so the translation tools pick the text up;
	# it must stay identical to DEFAULT_PRIVACY_NOTICE.
	return _(
		"We collect your name, contact details, photograph and government ID to confirm who you are, "
		"issue your visitor pass, keep our premises secure and meet our legal duties. Your details are "
		"seen only by the staff who host, approve and check in visitors, and are kept only for as long "
		"as these purposes and the law require. You can ask the organisation you are visiting to "
		"correct or delete them."
	)


def portal_consent_required():
	"""Must a visitor agree to the privacy notice before submitting the public form?

	On by default. A site upgraded from a version without the setting has no
	stored value yet (a Single's default is only applied when it is first
	saved), and that reads as "required", not as "off".

	The loaded document cannot tell the two apart: Frappe turns a Check that was
	never stored into 0 (BaseDocument._fix_numeric_types). So a 0 is only taken
	as "unticked" when a value is actually stored for it.
	"""
	doc = _settings()
	if not doc or cint(getattr(doc, "require_portal_consent", 1)):
		return True
	# `tabSingles` has no `modified` column, which get_value sorts by unless told not to.
	stored = frappe.db.get_value(
		"Singles",
		{"doctype": "VMS Settings", "field": "require_portal_consent"},
		"value",
		order_by=None,
	)
	return stored is None


def privacy_notice_text():
	"""The notice shown above the consent box: the site's own text, else the standard one.

	Plain text. Escape it when rendering.
	"""
	doc = _settings()
	text = (getattr(doc, "privacy_notice_text", None) or "").strip() if doc else ""
	return text or _default_privacy_notice()


def privacy_notice_version():
	"""The version of the notice a consent is recorded against."""
	doc = _settings()
	value = (str(getattr(doc, "privacy_notice_version", None) or "")).strip() if doc else ""
	return value or DEFAULT_PRIVACY_NOTICE_VERSION


def walk_in_allowed():
	"""May the public form be used without an invitation link?

	Off unless VMS Settings -> Allow Pre-Registration Without an Invitation is
	ticked. A site upgraded from 1.0.0, where the form was always open, has it
	switched on once by the upgrade (upgrades/platform_steps.py).
	"""
	return bool(flag("allow_walk_in_pre_registration", 0))


def default_checkout_time():
	doc = _settings()
	value = getattr(doc, "default_checkout_time", None) if doc else None
	return str(value) if value else None
