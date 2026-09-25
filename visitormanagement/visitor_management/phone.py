"""Country-aware mobile number validation, shared by every entry path.

Why this is not a length check
------------------------------
The app previously accepted anything between 10 and 15 digits and rebuilt it by
assuming the *last ten digits* were the subscriber number and everything before
was the country code. That produced nonsense from bad input — `999999999999999`
normalised to `+99999-9999999999`, a five-digit country code, when no country
code is longer than three digits. It also rejected perfectly valid numbers from
countries whose subscriber numbers are not ten digits long.

Numbering plans are per-country and irregular, so this defers to Google's
libphonenumber (the `phonenumbers` package, already installed with Frappe),
which knows the length and prefix rules for every region. A number is valid only
if it is a real, dialable number *for its country*.

Which country
-------------
A number written with an international prefix (`+44 …`) carries its own country
and is validated against that. A number typed bare (`9845012345`) is read as
belonging to the site's configured home country — so an Indian site keeps
accepting ten digits with nothing in front, and a German site accepts German
local numbers, without either having to say so.
"""

import frappe
from frappe import _

import phonenumbers

from visitormanagement.visitor_management import settings as vms_settings

# Fallback when the site's home country cannot be resolved to a region.
DEFAULT_REGION = "IN"

# The field is a *mobile* number: it is how the host and security reach a visitor
# who is standing at the gate, so a number that cannot receive a call or text
# there is not fit for purpose. Types that may be a mobile are allowed — in much
# of North America the plans are not separable and libphonenumber reports
# FIXED_LINE_OR_MOBILE for perfectly good mobiles.
MOBILE_CAPABLE_TYPES = frozenset({
	phonenumbers.PhoneNumberType.MOBILE,
	phonenumbers.PhoneNumberType.FIXED_LINE_OR_MOBILE,
	phonenumbers.PhoneNumberType.VOIP,
	phonenumbers.PhoneNumberType.PERSONAL_NUMBER,
})


def default_region():
	"""ISO 3166-1 alpha-2 region for bare, un-prefixed numbers.

	Taken from the home country in VMS Settings, whose Country record already
	carries the ISO code libphonenumber wants.
	"""
	country = vms_settings.home_country()
	if not country:
		return DEFAULT_REGION

	code = frappe.db.get_value("Country", country, "code")
	return (code or DEFAULT_REGION).upper()


def parse(number, region=None):
	"""Return a parsed number, or None if it cannot be read at all."""
	value = (number or "").strip()
	if not value:
		return None

	try:
		return phonenumbers.parse(value, region or default_region())
	except phonenumbers.NumberParseException:
		return None


def is_valid(number, region=None):
	parsed = parse(number, region)
	return bool(parsed) and phonenumbers.is_valid_number(parsed)


def format_for_storage(number, region=None):
	"""Normalise to the `+<isd>-<subscriber>` shape Frappe's Phone control expects.

	Frappe renders a Phone field by splitting on the first hyphen, so the stored
	value has to keep that separator rather than plain E.164.
	"""
	parsed = parse(number, region)
	if not parsed:
		return (number or "").strip()

	# `national_number` is the subscriber number with the trunk prefix already
	# removed. Formatting to NATIONAL and stripping punctuation would keep the
	# leading 0 many countries add for domestic dialling, producing +91-09845012345.
	return f"+{parsed.country_code}-{parsed.national_number}"


def validate_mobile(number, label=None, region=None, required=False):
	"""Validate and normalise, or raise with a message naming the field.

	Returns the normalised value. An empty value passes unless `required`.
	"""
	label = label or _("Mobile Number")
	value = (number or "").strip()
	# Frappe's Phone control stores "<picker code>-<what was typed>". A visitor who
	# follows the portal's own hint and types "+81 9012345678" while the picker
	# still shows +91 produced "+91-+81 9012345678", which could not be parsed, so
	# the pre-registration was refused. A code typed with the number wins.
	head, sep, typed = value.partition("-")
	if sep and head.startswith("+") and head[1:].isdigit() and typed.lstrip().startswith("+"):
		value = typed.strip()

	if not value:
		if required:
			frappe.throw(_("{0} is required.").format(label), title=_("Missing Mobile Number"))
		return value

	region = region or default_region()
	parsed = parse(value, region)

	if not parsed:
		frappe.throw(
			_("{0} could not be read as a phone number. Enter digits only, or start with "
			  "the country code — for example +44 20 7946 0958.").format(label),
			title=_("Invalid Mobile Number"),
		)

	if phonenumbers.is_valid_number(parsed) and phonenumbers.number_type(parsed) not in MOBILE_CAPABLE_TYPES:
		frappe.throw(
			_("{0} '{1}' is a valid number but not a mobile one. Security needs a number "
			  "that reaches the visitor at the gate.").format(label, value),
			title=_("Mobile Number Required"),
		)

	if not phonenumbers.is_valid_number(parsed):
		example = _example_for(region)
		frappe.throw(
			_("{0} '{1}' is not a valid number{2}. {3}").format(
				label,
				value,
				_(" for {0}").format(region) if not value.lstrip().startswith("+") else "",
				_("Local numbers should look like {0}; for anywhere else, start with the "
				  "country code, e.g. +44 20 7946 0958.").format(example) if example
				else _("Start with the country code for an international number."),
			),
			title=_("Invalid Mobile Number"),
		)

	return format_for_storage(value, region)


def _example_for(region):
	"""A real example number for the region, used in the error message."""
	try:
		sample = phonenumbers.example_number_for_type(
			region, phonenumbers.PhoneNumberType.MOBILE
		)
		if sample:
			return phonenumbers.format_number(
				sample, phonenumbers.PhoneNumberFormat.NATIONAL
			)
	except Exception:
		pass
	return None
