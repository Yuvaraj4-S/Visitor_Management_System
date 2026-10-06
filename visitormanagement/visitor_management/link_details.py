"""The few fields a Visitor Pass copies from a record someone picked.

A pass links to masters other apps own — the host (Employee), a job applicant,
a supplier or contractor (Supplier) — and fills a handful of its own fields from
them: the host's name, department and login email; the candidate's or supplier's
name, phone and email. Both the desk's `fetch_from` and the form's own prefill
read those through Frappe's `get_value` / `get`, which need full READ on the
master.

This app grants nothing on those DocTypes (see link_queries.py): read on Employee
is the whole HR record — bank account, salary, PAN, date of birth — and read on
Job Applicant is every candidate's CV, while these roles only ever need to pick a
record and copy three fields from it.

This endpoint returns exactly those fields, to anyone the link picker would
offer the record to (link_queries.authorize + is_visible).
"""

import frappe
from frappe import _

from visitormanagement.visitor_management.link_queries import authorize, is_visible

# doctype -> the fields a pass may copy from it. Nothing else is ever returned.
LINK_DETAIL_FIELDS = {
	"Employee": ("employee_name", "department", "user_id"),
	"Job Applicant": ("applicant_name", "phone_number", "email_id", "job_title"),
	"Supplier": ("supplier_name", "mobile_no", "email_id"),
}


@frappe.whitelist()
def get_link_details(doctype: str, name: str, reference_doctype: str | None = None) -> dict:
	fields = LINK_DETAIL_FIELDS.get(doctype)
	if not fields:
		frappe.throw(_("Details cannot be looked up for {0}.").format(doctype), frappe.PermissionError)
	if not name or not isinstance(name, str):
		return {}
	authorize(doctype, reference_doctype)
	if not is_visible(doctype, name):
		return {}
	return frappe.db.get_value(doctype, name, list(fields), as_dict=True) or {}


def fill_from_link(doc, link_fieldname, doctype, mapping):
	"""Copy `mapping` {doc field: master field} from the linked record on save.

	The server-side half of the old `fetch_from`: runs whoever saves the pass,
	so the stored values never depend on the saver's rights on the master.
	"""
	name = doc.get(link_fieldname)
	if not name:
		return  # as fetch_from: nothing linked, nothing overwritten
	values = frappe.db.get_value(doctype, name, list(mapping.values()), as_dict=True) or {}
	for fieldname, source in mapping.items():
		doc.set(fieldname, values.get(source))


@frappe.whitelist()
def get_own_employee() -> str | None:
	"""The caller's own active Employee, for defaulting "host" pickers.

	The desk used `frappe.client.get_value("Employee", ...)`, which needs READ on
	Employee — a host account holding only the VMS roles had none.
	"""
	if frappe.session.user in ("Guest", "Administrator"):
		return None
	return frappe.db.get_value("Employee", {"user_id": frappe.session.user, "status": "Active"}, "name")
