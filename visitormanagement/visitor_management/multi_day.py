"""Multi-Day Visit rules shared by Visitor Invitation, Visitor Pass and the gate.

A multi-day visit has ONE pass, valid every day from ``visit_date`` (start date)
to ``pass_valid_until`` (end date). The visitor checks in and out at the gate each
day through Security Log; a Checked-Out multi-day pass may check in again while
today is still inside the date range.
"""

import frappe
from frappe import _
from frappe.utils import cint, date_diff, getdate, today


def is_multi_day(doc):
	return bool(cint(doc.get("multi_day_pass")))


def get_visit_end_date(doc):
	"""Last valid day of the visit — the end date for multi-day, else the visit date."""
	if is_multi_day(doc) and doc.get("pass_valid_until"):
		return getdate(doc.get("pass_valid_until"))
	return getdate(doc.get("visit_date")) if doc.get("visit_date") else None


def is_valid_on(doc, on_date=None):
	"""True when ``on_date`` (default today) falls inside the visit's date range."""
	if not doc.get("visit_date"):
		return True
	day = getdate(on_date or today())
	return getdate(doc.get("visit_date")) <= day <= get_visit_end_date(doc)


def can_check_in_again(doc, on_date=None):
	"""A multi-day pass returns to the gate after each day's check-out."""
	return doc.get("status") == "Checked-Out" and is_multi_day(doc) and is_valid_on(doc, on_date)


def date_range_label(doc):
	if is_multi_day(doc) and doc.get("pass_valid_until"):
		return _("{0} to {1}").format(doc.get("visit_date"), doc.get("pass_valid_until"))
	return str(doc.get("visit_date") or "")


def validate_multi_day_range(doc):
	"""Normalise and validate the start/end dates of a multi-day visit."""
	if not is_multi_day(doc):
		doc.pass_valid_until = None
		return

	if not doc.get("visit_date"):
		return

	if not doc.get("pass_valid_until"):
		frappe.throw(_("Visit End Date is required for a Multi-Day Visit."), title=_("Missing End Date"))

	start, end = getdate(doc.visit_date), getdate(doc.pass_valid_until)
	if end <= start:
		frappe.throw(
			_("Visit End Date ({0}) must be after the start date ({1}) for a Multi-Day Visit.").format(
				doc.pass_valid_until, doc.visit_date
			),
			title=_("Invalid Date Range"),
		)

	max_days = cint(frappe.db.get_single_value("VMS Settings", "max_multi_day_visit_days"))
	span_days = date_diff(end, start) + 1
	if max_days and span_days > max_days:
		frappe.throw(
			_("A Multi-Day Visit can cover at most {0} days (this one covers {1}). "
			  "Change the dates or the limit in VMS Settings.").format(max_days, span_days),
			title=_("Visit Too Long"),
		)
