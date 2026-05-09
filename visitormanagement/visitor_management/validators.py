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


def _canonical_type(id_type):
    key = (id_type or "").strip().lower()
    return _CANONICAL.get(key)


def validate_id(id_type, number):
    """Validate a number against the given ID type. Unknown type → False."""
    canonical = _canonical_type(id_type)
    if not canonical:
        return False
    return _VALIDATORS[canonical](number)


def detect_id_type(number):
    """Return the first canonical label whose validator accepts `number`, else None.
    Order: Aadhaar → PAN → Driving License → Passport.
    """
    for label in ("Aadhaar", "PAN Card", "Driving License", "Passport"):
        if _VALIDATORS[label](number):
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
    "PAN Card": (
        "PAN must be in the format ABCDE1234F "
        "(5 uppercase letters, 4 digits, 1 uppercase letter)."
    ),
    "Driving License": (
        "Driving License must be in the format SS00 00000000000 "
        "(2 letters + 2 digits + optional space + 11 digits)."
    ),
    "Passport": (
        "Passport must be in the format A1234567 "
        "(1 uppercase letter followed by 7 digits)."
    ),
}


def id_proof_error_message(id_type):
    canonical = _canonical_type(id_type) or id_type
    return _ERROR_MESSAGES.get(
        canonical,
        f"Unsupported ID Proof Type: {id_type!r}. "
        "Use Aadhaar, PAN Card, Driving License, or Passport.",
    )


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
            failures.append({
                "name": row.name,
                "visitor_full_name": row.visitor_full_name,
                "id_proof_type": row.id_proof_type,
                "id_proof_number": row.id_proof_number,
                "status": row.status,
                "reason": id_proof_error_message(row.id_proof_type),
            })
    return failures
