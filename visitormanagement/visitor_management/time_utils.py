"""Expected check-in / check-out times are captured to the minute — no seconds.

These helpers keep stored values at ``HH:MM:00`` and render them as ``HH:MM``
in mails and messages, without touching the site-wide System Settings time format.
"""

from frappe.utils import get_time

EXPECTED_TIME_FIELDS = ("expected_checkin", "expected_checkout")


def strip_seconds(value):
    """Return ``value`` as an ``HH:MM:00`` string (or the falsy value unchanged)."""
    if not value:
        return value
    return get_time(value).strftime("%H:%M:00")


def format_time_hhmm(value, default=""):
    """Return ``value`` as ``HH:MM`` for display."""
    if not value:
        return default
    return get_time(value).strftime("%H:%M")


def strip_expected_time_seconds(doc):
    for fieldname in EXPECTED_TIME_FIELDS:
        if doc.get(fieldname):
            doc.set(fieldname, strip_seconds(doc.get(fieldname)))
