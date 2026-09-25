"""The few fields a Visitor Pass copies from a record someone picked.

A pass links to masters other apps own — the host (Employee), a job applicant,
a supplier or contractor (Supplier) — and fills a handful of its own fields from
them: the host's name, department and login email; the candidate's or supplier's
name, phone and email. Both the desk's `fetch_from` and the form's own prefill
read those through Frappe's `get_value` / `get`, which need full READ on the
master.

So setup used to grant READ on Employee, Supplier, Job Applicant and Maintenance
Visit to every role that raises or handles a pass. Read on Employee is the whole
HR record — bank account, salary, PAN, date of birth, health details — and read
on Job Applicant is every candidate's CV. Those roles only ever needed to pick a
record and copy three fields from it.

This endpoint returns exactly those fields, to anyone allowed to pick the record
(SELECT, which READ implies), and setup now grants SELECT only.
"""

import frappe
from frappe import _

# doctype -> the fields a pass may copy from it. Nothing else is ever returned.
LINK_DETAIL_FIELDS = {
	"Employee": ("employee_name", "department", "user_id"),
	"Job Applicant": ("applicant_name", "phone_number", "email_id", "job_title"),
	"Supplier": ("supplier_name", "mobile_no", "email_id"),
}


@frappe.whitelist()
def get_link_details(doctype: str, name: str) -> dict:
	fields = LINK_DETAIL_FIELDS.get(doctype)
	if not fields:
		frappe.throw(_("Details cannot be looked up for {0}.").format(doctype), frappe.PermissionError)
	if not name or not isinstance(name, str):
		return {}
	if not frappe.has_permission(doctype, "select"):
		frappe.throw(_("You cannot select {0} records.").format(_(doctype)), frappe.PermissionError)
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
