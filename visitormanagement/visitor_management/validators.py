"""Reusable ID proof validators for Indian ID documents.

Pure-Python logic — no Frappe imports — so the validator functions can be
called from portal handlers, doctype controllers, whitelisted APIs, or any
future Frappe/ERPNext/plain-Python caller. Frappe-specific helpers
(`audit_legacy_id_proofs`) live at the bottom and are the only thing that
imports `frappe`.

Canonical ID type labels mirror the Select options on Visitor Pass and the
Visitor Pre-Registration web form:
    "Aadhaar", "PAN Card", "Passport", "Driving License"

Short aliases ("PAN", "DL") are accepted by `validate_id` / `id_proof_error_message`.
"""

import re

# ─────────────────────────────────────────────────────────────
# VERHOEFF CHECKSUM TABLES (for Aadhaar)
# ─────────────────────────────────────────────────────────────

VERHOEFF_D = (
	(0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
	(1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
	(2, 3, 4, 0, 1, 7, 8, 9, 5, 6),
	(3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
	(4, 0, 1, 2, 3, 9, 5, 6, 7, 8),
	(5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
	(6, 5, 9, 8, 7, 1, 0, 4, 3, 2),
	(7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
	(8, 7, 6, 5, 9, 3, 2, 1, 0, 4),
	(9, 8, 7, 6, 5, 4, 3, 2, 1, 0),
)

VERHOEFF_P = (
	(0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
	(1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
	(5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
	(8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
	(9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
	(4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
	(2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
	(7, 0, 4, 6, 9, 1, 3, 2, 5, 8),
)

VERHOEFF_INV = (0, 4, 3, 2, 1, 5, 6, 7, 8, 9)


def _verhoeff_checksum(digits):
	"""Return 0 iff the supplied digit string passes the Verhoeff checksum."""
	c = 0
	for i, d in enumerate(reversed(digits)):
		c = VERHOEFF_D[c][VERHOEFF_P[i % 8][int(d)]]
	return c


# ─────────────────────────────────────────────────────────────
# PRIMITIVE VALIDATORS
# ─────────────────────────────────────────────────────────────

_AADHAAR_RE = re.compile(r"^\d{12}$")
_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]{1}$")
_DL_RE = re.compile(r"^[A-Z]{2}[0-9]{2}\s?[0-9]{11}$")
_PASSPORT_RE = re.compile(r"^[A-Z]{1}[0-9]{7}$")


def _strip(number):
	return "".join((number or "").split())


def _strip_hyphens(number):
	return re.sub(r"[\s\-]", "", number or "")


def validate_aadhaar(number):
	"""12 digits, first digit ∈ 2..9, passes Verhoeff."""
	clean = _strip_hyphens(str(number or ""))
	if not _AADHAAR_RE.match(clean):
		return False
	if clean[0] in ("0", "1"):
		return False
	return _verhoeff_checksum(clean) == 0


def validate_pan(number):
	clean = _strip(str(number or "")).upper()
	return bool(_PAN_RE.match(clean))


def validate_driving_license(number):
	# Preserve an internal space if present; only strip leading/trailing whitespace.
	clean = str(number or "").strip().upper()
	# Collapse multi-space runs to a single space (users often type "TN05  ...").
	clean = re.sub(r"\s+", " ", clean)
	return bool(_DL_RE.match(clean))


def validate_passport(number):
	clean = _strip(str(number or "")).upper()
	return bool(_PASSPORT_RE.match(clean))


# ─────────────────────────────────────────────────────────────
# TYPE DISPATCH
# ─────────────────────────────────────────────────────────────

_CANONICAL = {
	"aadhaar": "Aadhaar",
	"aadhar": "Aadhaar",
	"uid": "Aadhaar",
	"pan": "PAN Card",
	"pan card": "PAN Card",
	"dl": "Driving License",
	"driving license": "Driving License",
	"driving licence": "Driving License",
	"passport": "Passport",
}

_VALIDATORS = {
	"Aadhaar": validate_aadhaar,
	"PAN Card": validate_pan,
	"Driving License": validate_driving_license,
	"Passport": validate_passport,
}


# ─────────────────────────────────────────────────────────────
# MASTER-DRIVEN TYPES (ID Proof Type doctype)
# ─────────────────────────────────────────────────────────────
# The four built-ins above stay as the fallback so this module keeps working
# standalone (no Frappe import at module scope) and on a site that has not
# migrated yet. When the `ID Proof Type` master is present it wins, so a site
# can add its own document types — foreign passports, national IDs, residence
# permits — with their own pattern, aliases and error text, without a release.

_NORMALISERS = {
	"Uppercase and strip spaces": lambda v: _strip(v).upper(),
	"Strip spaces and hyphens": lambda v: _strip_hyphens(v).upper(),
	"Collapse spaces": lambda v: re.sub(r"\s+", " ", (v or "").strip()).upper(),
	"None": lambda v: (v or "").strip(),
}


_MASTER_FIELDS = [
	"name",
	"aliases",
	"validation_method",
	"validation_regex",
	"normalisation",
	"error_message",
	"valid_for_foreign_nationals",
]


def _row_config(row):
	"""One ID Proof Type master row as the config the functions below read."""
	return {
		"aliases": [a.strip().lower() for a in (row.get("aliases") or "").splitlines() if a.strip()],
		"method": row.get("validation_method") or "Regex",
		"regex": row.get("validation_regex") or "",
		"normalisation": row.get("normalisation") or "Uppercase and strip spaces",
		"error_message": row.get("error_message") or "",
		"foreign_ok": bool(row.get("valid_for_foreign_nationals")),
	}


def _load_master():
	"""Return {canonical_name: config} for the ACTIVE ID Proof Type rows, or {}.

	Frappe is imported lazily and every failure is swallowed: this module is
	deliberately usable outside a Frappe request (portal helpers, plain-Python
	callers, scripts) and must never hard-depend on a site being connected.
	"""
	try:
		import frappe
	except ImportError:
		return {}

	try:
		cached = frappe.cache.get_value("vms_id_proof_types")
		if cached is not None:
			return cached
	except Exception:
		cached = None

	try:
		rows = frappe.get_all(
			"ID Proof Type",
			filters={"is_active": 1},
			order_by="creation asc",
			fields=_MASTER_FIELDS,
		)
	except Exception:
		return {}

	table = {row["name"]: _row_config(row) for row in rows}

	try:
		frappe.cache.set_value("vms_id_proof_types", table)
	except Exception:
		pass
	return table


def _deactivated_master_row(id_type):
	"""(name, config) of the type's master row when that row is switched off, else None.

	_load_master() holds active rows only, so a deactivated type used to look the
	same as a type the master never had — and the four built-in types then fell
	back to their built-in validator: switching "Passport" off in the master left
	it accepted on every new pass. Read straight from the table (not cached): it
	is only reached for a type that is not in the active master.
	"""
	name = (id_type or "").strip()
	if not name:
		return None
	name = _CANONICAL.get(name.lower(), name)
	try:
		import frappe

		row = frappe.db.get_value("ID Proof Type", name, ["is_active", *_MASTER_FIELDS], as_dict=True)
	except Exception:
		return None  # no site, or no master table: the built-in types apply
	if not row or row.get("is_active"):
		return None
	return row["name"], _row_config(row)


def is_deactivated(id_type):
	"""Whether the ID Proof Type master has this type but has it switched off."""
	return _deactivated_master_row(id_type) is not None


def _canonical_type(id_type):
	key = (id_type or "").strip().lower()

	master = _load_master()
	for name, cfg in master.items():
		if key == name.strip().lower() or key in cfg["aliases"]:
			return name

	return _CANONICAL.get(key)


def _validate_with_master(canonical, number, cfg):
	normalise = _NORMALISERS.get(cfg["normalisation"], _NORMALISERS["Uppercase and strip spaces"])
	clean = normalise(str(number or ""))

	method = cfg["method"]
	if method == "None":
		return bool(clean)
	if method == "Aadhaar (Verhoeff)":
		return validate_aadhaar(clean)
	if not cfg["regex"]:
		return False
	try:
		return bool(re.match(cfg["regex"], clean))
	except re.error:
		return False


def validate_id(id_type, number, allow_deactivated=False):
	"""Validate a number against the given ID type. Unknown type → False.

	A type switched off in the ID Proof Type master is not accepted for a new
	number. `allow_deactivated` is for a number already on record that is being
	saved again unchanged: it is still checked against the type's own rule, so a
	pass raised before the type was switched off can go on being approved.
	"""
	canonical = _canonical_type(id_type)
	cfg = _load_master().get(canonical) if canonical else None
	if cfg:
		return _validate_with_master(canonical, number, cfg)

	deactivated = _deactivated_master_row(id_type)
	if deactivated:
		name, cfg = deactivated
		return _validate_with_master(name, number, cfg) if allow_deactivated else False

	if not canonical:
		return False

	# No master row for this type at all: the built-in rule.
	validator = _VALIDATORS.get(canonical)
	return validator(number) if validator else False


def normalise_id_number(id_type, number):
	"""Return `number` normalised per the ID Proof Type master's rule, or
	`number` unchanged if the type has no master entry (the four built-in
	types, or an unknown type).

	`_validate_with_master` already normalises a value before testing it
	against the regex — but only a throwaway local copy, so a master
	configured with e.g. "Uppercase and strip spaces" would validate
	`lfc-1234` against `^LFC-[0-9]{4}$` correctly (the copy is uppercased
	first) while the caller went on to store the original, lowercase
	`lfc-1234`. Any later exact-match lookup for `LFC-1234` (a gate guard,
	a report) then silently misses that row. Callers that persist a value
	after `validate_id` succeeds must run it through this too, so the
	stored value is the same one that passed validation.
	"""
	canonical = _canonical_type(id_type)
	if not canonical:
		return number

	cfg = _load_master().get(canonical)
	if not cfg:
		return number

	normalise = _NORMALISERS.get(cfg["normalisation"], _NORMALISERS["Uppercase and strip spaces"])
	return normalise(str(number or ""))


def is_valid_for_foreign_nationals(id_type):
	"""Whether a foreign national may present this document type.

	Falls back to the original hardcoded rule (Passport only) when the master
	is unavailable.
	"""
	canonical = _canonical_type(id_type)
	cfg = _load_master().get(canonical) if canonical else None
	if cfg:
		return cfg["foreign_ok"]
	deactivated = _deactivated_master_row(id_type)
	if deactivated:
		return deactivated[1]["foreign_ok"]
	return canonical == "Passport"


def foreign_national_id_types():
	"""Types a foreign national may present, for error messages / link filters."""
	master = _load_master()
	if master:
		return [n for n, cfg in master.items() if cfg["foreign_ok"]]
	return ["Passport"]


def active_types():
	"""Usable ID proof types, in master order.

	Order matters for `detect_id_type`: a narrow pattern (Passport) must be
	tried before a permissive one (Foreign Passport), so the list follows the
	master's own creation order rather than alphabetical.
	"""
	master = _load_master()
	return list(master) if master else list(_VALIDATORS)


def detect_id_type(number):
	"""Return the first configured type whose validator accepts `number`, else None."""
	for label in active_types():
		if validate_id(label, number):
			return label
	return None


# ─────────────────────────────────────────────────────────────
# ERROR MESSAGE HELPER
# ─────────────────────────────────────────────────────────────

_ERROR_MESSAGES = {
	"Aadhaar": (
		"Aadhaar must be exactly 12 digits, must not start with 0 or 1, "
		"and must pass the UIDAI Verhoeff checksum."
	),
	"PAN Card": ("PAN must be in the format ABCDE1234F (5 uppercase letters, 4 digits, 1 uppercase letter)."),
	"Driving License": (
		"Driving License must be in the format SS00 00000000000 "
		"(2 letters + 2 digits + optional space + 11 digits)."
	),
	"Passport": ("Passport must be in the format A1234567 (1 uppercase letter followed by 7 digits)."),
}


def id_proof_error_message(id_type):
	canonical = _canonical_type(id_type) or id_type

	cfg = _load_master().get(canonical)
	if cfg and cfg["error_message"]:
		return cfg["error_message"]

	if is_deactivated(id_type):
		return f"{canonical} is not accepted as an ID proof at present. Choose another ID Proof Type."

	return _ERROR_MESSAGES.get(
		canonical,
		f"Unsupported ID Proof Type: {id_type!r}. Use Aadhaar, PAN Card, Driving License, or Passport.",
	)


# ─────────────────────────────────────────────────────────────
# MASKING — what people see in place of the real number
# ─────────────────────────────────────────────────────────────


def mask_id_number(raw):
	"""Mask ID proof number, preserving separators and showing only last 4 characters.

	Aadhaar  5001-5002-5003  →  XXXX-XXXX-5003
	PAN      AABPR2345T     →  XXXXXX345T
	Passport P1234567       →  XXXX4567
	DL       DL-TN-05210099 →  XX-XX-XXXX0099
	"""
	chars = [(i, ch) for i, ch in enumerate(raw) if ch.isalnum()]
	if len(chars) <= 4:
		return raw

	visible_positions = {pos for pos, _ in chars[-4:]}
	return "".join(ch if not ch.isalnum() or i in visible_positions else "X" for i, ch in enumerate(raw))


def is_masked_id(value):
	"""Whether `value` is already the masked form rather than a real number.

	A masked value is its own mask. A real number never is: Aadhaar is digits only,
	and a PAN or passport that happens to contain an X still differs from its mask.
	"""
	value = (value or "").strip()
	return bool(value) and "X" in value and mask_id_number(value) == value


# ─────────────────────────────────────────────────────────────
# LEGACY AUDIT (Frappe-only; ship once with this change)
# ─────────────────────────────────────────────────────────────


def audit_legacy_id_proofs():
	"""List Visitor Pass rows whose stored ID proof would fail the new strict
	validator. Run once via `bench execute` after deploying the strict rule
	so operators can clean records before they're re-saved/approved.
	"""
	import frappe

	rows = frappe.get_all(
		"Visitor Pass",
		filters={
			"id_proof_number": ["is", "set"],
			"id_proof_type": ["is", "set"],
		},
		fields=["name", "visitor_full_name", "id_proof_type", "id_proof_number", "status"],
		limit_page_length=0,
	)
	failures = []
	for row in rows:
		if not validate_id(row.id_proof_type, row.id_proof_number):
			failures.append(
				{
					"name": row.name,
					"visitor_full_name": row.visitor_full_name,
					"id_proof_type": row.id_proof_type,
					"id_proof_number": row.id_proof_number,
					"status": row.status,
					"reason": id_proof_error_message(row.id_proof_type),
				}
			)
	return failures
