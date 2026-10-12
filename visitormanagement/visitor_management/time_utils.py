"""Expected check-in / check-out times are captured to the minute — no seconds.

These helpers keep stored values at ``HH:MM:00`` and render them as ``HH:MM``
in mails and messages, without touching the site-wide System Settings time format.
"""

import datetime

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


def is_auto_now_time(value):
    """True when a Time value is Frappe's automatic "now" stamp, not a time anyone picked.

    Frappe fills every *empty* Time field of a NEW document with ``nowtime()``
    (frappe/model/create_new.py), and that value always carries microseconds —
    ``14:50:37.813124``. Neither the desk time picker nor our own code ever
    produces those, so microseconds identify the stamp reliably.

    Without this, code that fills a Time field "only if it is empty" never gets
    its turn: the field already holds the moment the record happened to be
    created, which then reads as a real plan to everyone downstream.
    """
    if not value:
        return False
    if isinstance(value, datetime.timedelta):
        return value.microseconds > 0
    if isinstance(value, (datetime.time, datetime.datetime)):
        return value.microsecond > 0
    fraction = str(value).rsplit(".", 1)
    return len(fraction) == 2 and fraction[1].isdigit() and int(fraction[1]) > 0


def clear_auto_now_times(doc, *fieldnames):
    """Blank any of ``fieldnames`` that only hold Frappe's "now" stamp.

    Returns the fieldnames cleared, so a caller can log or react to it.
    """
    cleared = []
    for fieldname in fieldnames:
        if is_auto_now_time(doc.get(fieldname)):
            doc.set(fieldname, None)
            cleared.append(fieldname)
    return cleared
