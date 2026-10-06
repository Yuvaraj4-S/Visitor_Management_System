"""Picking and reading records of other apps' DocTypes from this app's forms.

Several of this app's DocTypes link to masters that HRMS and ERPNext own: the
host, guard, tour guide or driver (Employee), a job applicant (Job Applicant), a
supplier, contractor, cab vendor or hotel (Supplier) and a work order
(Maintenance Visit). The people who fill those fields hold this app's roles, and
nothing on those DocTypes.

Earlier versions closed that gap by granting the roles Select on Employee,
Supplier, Job Applicant and Maintenance Visit. That changed the permissions of
DocTypes this app does not own, and froze them: the grant copied each DocType's
whole standard permission set into Custom DocPerm, so later HRMS/ERPNext
permission updates stopped applying on the site.

The app now keeps to its own side:

* `link_query` is the search behind this app's own pickers (wired with
  `frm.set_query` in the app's form scripts, never through the global
  `standard_queries` hook, so other apps' pickers are untouched). It returns a
  record's name and a label — never contact, salary, bank or identity data — to
  a caller who may create or edit one of this app's records that links there.
* `get_link_details` returns the few fields a pass copies from a picked record,
  to the same callers.

Both apply the caller's User Permissions the way Frappe's own link search does,
unless the app's own Link field is marked "Ignore User Permissions".
"""

import frappe
from frappe import _
from frappe.query_builder import Criterion
from frappe.utils import cint

# This app's modules; their DocTypes are the only ones whose Link fields these
# helpers serve.
APP_MODULES = ("Visitor Management", "Conference Room")

# doctype -> the fields the browser may read from a picked record: a display
# name, plus the host's department and the candidate's job opening. Never a phone
# number or email address — a candidate's contact details are HR data, and a
# supplier's switchboard is not the visitor's number anyway. The host's login
# email is copied server-side on save (VisitorPass._fill_from_linked_records).
LINK_DETAIL_FIELDS = {
	"Employee": ("employee_name", "department"),
	"Job Applicant": ("applicant_name", "job_title"),
	"Supplier": ("supplier_name",),
}

# Who may pick at all, where "may edit an app record that links here" is not
# enough. A Job Applicant is a candidate's HR record, which HRMS opens to HR User
# only; any employee may raise a Visitor Pass, so without this every employee
# could list the company's applicants. Allowed: HR (who own the records), Front
# Office Executive (reception, who registers arriving candidates), System
# Manager, and whichever roles the Candidate-layout Visitor Types name as their
# approvers (read at runtime, as the approval workflow is — see
# `_candidate_approver_roles`).
PICKER_ROLES = {
	"Job Applicant": {"HR Manager", "HR User", "Front Office Executive", "System Manager"},
}

# doctype -> what the app's picker may search on, show, filter on and sort by.
# `show` is what appears in the dropdown: a name and a label, nothing personal.
LINK_SEARCH = {
	"Employee": {
		"search": ("employee_name",),
		"show": ("employee_name", "department"),
		"filters": ("status", "user_id", "department", "company"),
		"order_by": "employee_name",
	},
	"Supplier": {
		"search": ("supplier_name",),
		"show": ("supplier_name", "supplier_group"),
		"filters": ("supplier_group",),
		"order_by": "supplier_name",
	},
	"Job Applicant": {
		"search": ("applicant_name",),
		"show": ("applicant_name", "job_title"),
		"filters": ("status", "job_title"),
		"order_by": "applicant_name",
	},
	"Maintenance Visit": {
		"search": ("customer", "customer_name"),
		"show": ("customer_name", "mntc_date"),
		"filters": ("customer", "company", "completion_status", "status"),
		"order_by": "mntc_date",
	},
}

_FILTER_OPERATORS = ("=", "!=", "in", "not in")

# Not a filter on the searched DocType: the key under which a picker names the
# Link field it serves (see `link_query`). `_safe_filters` drops it like any
# other key that is not a searchable field.
LINK_FIELDNAME_FILTER = "link_fieldname"


def _app_doctypes():
	return frappe.get_all("DocType", filters={"module": ("in", APP_MODULES)}, pluck="name")


def _link_sources(doctype):
	"""{this app's DocType (child tables included): [its Link fields to `doctype`]}."""
	sources = {}
	for source in _app_doctypes():
		fields = [df for df in frappe.get_meta(source).get_link_fields() if df.options == doctype]
		if fields:
			sources[source] = fields
	return sources


def _governing_doctypes(source):
	"""The DocTypes whose permissions decide who edits `source`: itself, or a child table's parents."""
	if not frappe.get_meta(source).istable:
		return [source]
	return [
		parent
		for parent in _app_doctypes()
		if any(df.options == source for df in frappe.get_meta(parent).get_table_fields())
	]


def _candidate_approver_roles():
	"""Roles the Candidate-layout Visitor Types route their passes to."""
	roles = set()
	for row in frappe.get_all(
		"Visitor Type",
		filters={"detail_layout": "Candidate"},
		fields=["approver_role", "secondary_approver_role"],
	):
		roles.update(r for r in (row.approver_role, row.secondary_approver_role) if r)
	return roles


def _picker_roles(doctype):
	"""Roles allowed to pick `doctype` at all, or None when any app editor may."""
	roles = PICKER_ROLES.get(doctype)
	if roles is None:
		return None
	if doctype == "Job Applicant":
		return roles | _candidate_approver_roles()
	return set(roles)


def can_pick(doctype, reference_doctype=None):
	"""May the caller pick a `doctype` record in one of this app's Link fields?

	Yes for a System Manager, and for anyone who may create or edit a record of
	one of this app's DocTypes that links to `doctype` (the one named by
	`reference_doctype`, when it is one of them). Guests never.
	"""
	if doctype not in LINK_SEARCH or frappe.session.user == "Guest":
		return False
	roles = set(frappe.get_roles())
	if "System Manager" in roles:
		return True
	allowed_roles = _picker_roles(doctype)
	if allowed_roles is not None and not roles & allowed_roles:
		return False
	sources = _link_sources(doctype)
	candidates = [reference_doctype] if reference_doctype in sources else list(sources)
	governing = {parent for source in candidates for parent in _governing_doctypes(source)}
	return any(frappe.has_permission(dt, ptype) for dt in sorted(governing) for ptype in ("create", "write"))


def _ignores_user_permissions(doctype, reference_doctype, fieldname=None):
	"""True only when this app's own Link field says so — never on the caller's word."""
	fields = _link_sources(doctype).get(reference_doctype) or []
	if fieldname:
		fields = [df for df in fields if df.fieldname == fieldname]
	return any(cint(df.ignore_user_permissions) for df in fields)


def _user_permission_conditions(table, doctype, reference_doctype):
	"""The caller's User Permissions on `doctype`, applied as Frappe's own list query does.

	Mirrors frappe/model/db_query.py `DatabaseQuery.add_user_permissions`.
	"""
	from frappe.core.doctype.user_permission.user_permission import get_user_permissions

	user_permissions = get_user_permissions()
	if not user_permissions:
		return []
	strict = cint(frappe.get_system_settings("apply_strict_user_permissions"))
	links = [
		(df.fieldname, df.options)
		for df in frappe.get_meta(doctype).get_link_fields()
		if not cint(df.ignore_user_permissions)
	]
	links.append(("name", doctype))

	conditions = []
	for fieldname, options in links:
		docs = []
		for permission in user_permissions.get(options) or []:
			applicable_for = permission.get("applicable_for")
			if (
				not applicable_for
				or (fieldname == "name" and reference_doctype and applicable_for == reference_doctype)
				or applicable_for == doctype
			):
				docs.append(permission.get("doc"))
		if not docs:
			continue
		condition = table[fieldname].isin(docs)
		if not strict and fieldname != "name":
			condition = condition | table[fieldname].isnull() | (table[fieldname] == "")
		conditions.append(condition)
	return conditions


def _safe_filters(doctype, filters):
	"""The caller's filters, kept only where field, operator and value are expected.

	Anything else is dropped: the picker can only ever narrow its own results.
	"""
	if isinstance(filters, str):
		filters = frappe.parse_json(filters)
	items = []
	if isinstance(filters, dict):
		for fieldname, value in filters.items():
			if isinstance(value, list | tuple) and len(value) == 2:
				items.append((fieldname, value[0], value[1]))
			else:
				items.append((fieldname, "=", value))
	elif isinstance(filters, list | tuple):
		for row in filters:
			if isinstance(row, list | tuple) and len(row) == 4:
				row = row[1:]
			if isinstance(row, list | tuple) and len(row) == 3:
				items.append(tuple(row))

	allowed = LINK_SEARCH[doctype]["filters"]
	safe = []
	for fieldname, operator, value in items:
		operator = str(operator).strip().lower()
		if fieldname not in allowed or operator not in _FILTER_OPERATORS:
			continue
		values = value if isinstance(value, list | tuple) else [value]
		if not all(isinstance(v, str | int | float) for v in values):
			continue
		if operator in ("in", "not in") and not isinstance(value, list | tuple):
			continue
		# Which Employee belongs to which login is only ever asked about oneself.
		if fieldname == "user_id" and (operator != "=" or value != frappe.session.user):
			continue
		safe.append((fieldname, operator, value))
	return safe


def _find(
	doctype,
	txt="",
	filters=None,
	start=0,
	page_len=20,
	reference_doctype=None,
	ignore_user_permissions=False,
	name=None,
):
	"""(name, *show fields) rows of `doctype`, read without role permissions."""
	spec = LINK_SEARCH[doctype]
	meta = frappe.get_meta(doctype)
	table = frappe.qb.DocType(doctype)
	query = frappe.qb.from_(table).select(table.name, *(table[f] for f in spec["show"]))

	if meta.has_field("disabled"):
		query = query.where(table.disabled == 0)
	if meta.is_submittable:
		query = query.where(table.docstatus < 2)
	if name is not None:
		query = query.where(table.name == name)
	if txt:
		like = f"%{txt}%"
		matches = [table.name.like(like), *(table[f].like(like) for f in spec["search"])]
		query = query.where(Criterion.any(matches))

	for fieldname, operator, value in _safe_filters(doctype, filters):
		column = table[fieldname]
		if operator == "=":
			query = query.where(column == value)
		elif operator == "!=":
			query = query.where(column != value)
		elif operator == "in":
			query = query.where(column.isin(list(value) or [""]))
		else:
			query = query.where(column.notin(list(value) or [""]))

	if not ignore_user_permissions:
		for condition in _user_permission_conditions(table, doctype, reference_doctype):
			query = query.where(condition)

	return (
		query.orderby(table[spec["order_by"]])
		.orderby(table.name)
		.limit(cint(page_len) or 20)
		.offset(cint(start))
		.run()
	)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def link_query(
	doctype,
	txt,
	searchfield,
	start,
	page_len,
	filters,
	reference_doctype=None,
	ignore_user_permissions=False,
	link_fieldname=None,
):
	"""Search behind this app's pickers for Employee, Supplier, Job Applicant, Maintenance Visit.

	Standard custom-query signature. Frappe's `search_widget` calls a custom query
	without checking the caller's permission on `doctype`, so this does.

	The caller's User Permissions are set aside only for one named field: the
	Link field `link_fieldname` of `reference_doctype`, and only when that field
	itself is marked "Ignore User Permissions" — the rule `get_link_details`
	already follows. `ignore_user_permissions` is a request and is never taken
	on the caller's word. It used to be honoured when ANY Link field of the
	reference DocType to this DocType carried the mark, so a caller could borrow
	one field's exemption (Visitor Pass `person_to_visit`) for a search that
	belonged to no such field.

	Frappe's link control does not say which field is searching, and
	`search_widget` hands a custom query only the standard arguments, so the
	app's pickers name the field in the filters they already send
	(`filters.link_fieldname`, see public/js/core_link_pickers.js). A search that
	names no field keeps the caller's User Permissions.
	"""
	if not can_pick(doctype, reference_doctype):
		return []
	if isinstance(filters, str):
		filters = frappe.parse_json(filters)
	if not link_fieldname and isinstance(filters, dict):
		link_fieldname = filters.get(LINK_FIELDNAME_FILTER)
	ignore = bool(
		cint(ignore_user_permissions)
		and link_fieldname
		and isinstance(link_fieldname, str)
		and _ignores_user_permissions(doctype, reference_doctype, link_fieldname)
	)
	return _find(
		doctype,
		txt=txt,
		filters=filters,
		start=start,
		page_len=page_len,
		reference_doctype=reference_doctype,
		ignore_user_permissions=ignore,
	)


@frappe.whitelist()
def get_link_details(
	doctype: str, name: str, reference_doctype: str | None = None, fieldname: str | None = None
) -> dict:
	"""The fields a pass copies from a picked Employee / Job Applicant / Supplier."""
	fields = LINK_DETAIL_FIELDS.get(doctype)
	if not fields:
		frappe.throw(_("Details cannot be looked up for {0}.").format(doctype), frappe.PermissionError)
	if not name or not isinstance(name, str):
		return {}
	if not can_pick(doctype, reference_doctype):
		frappe.throw(_("You cannot select {0} records.").format(_(doctype)), frappe.PermissionError)
	if not frappe.db.exists(doctype, name):
		return {}
	# Checked against the record as well: the caller's User Permissions (e.g. a
	# user restricted to one Company's Employees) hide records from the picker, and
	# must hide their details too.
	ignore = _ignores_user_permissions(doctype, reference_doctype, fieldname) if fieldname else False
	if not _find(
		doctype,
		name=name,
		page_len=1,
		reference_doctype=reference_doctype,
		ignore_user_permissions=ignore,
	):
		frappe.throw(_("You cannot select this {0}.").format(_(doctype)), frappe.PermissionError)
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
