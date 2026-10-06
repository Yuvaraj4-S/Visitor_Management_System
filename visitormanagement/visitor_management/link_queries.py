"""Link pickers for the masters other apps own: Employee, Supplier, Job Applicant
and Maintenance Visit.

A VMS form has to let its users pick a host, a supplier or a candidate, but
those DocTypes belong to ERPNext / HRMS. Frappe's default link search needs
SELECT on the target DocType, and granting that means writing Custom DocPerm
rows onto another app's DocType — which freezes its permissions on the site
(Frappe reads Custom DocPerm *instead of* the shipped DocPerm once one row
exists), so the owning app's later permission changes silently stop applying.

So this app never grants anything on those DocTypes. Every such link field sets
`query` to `search` below (Frappe hands both the search and the link
validation of a field with a custom query to that query), and access is decided
by what the user may do in VMS instead:

- the target must be one of the four masters, reached from a VMS DocType that
  actually links to it (`LINK_CONTEXTS`);
- the user must be able to read that VMS DocType;
- the user's own User Permissions on the target (and on its Company) still apply;
- only the record name and its title are returned — never the master itself.
"""

import frappe
from frappe import _
from frappe.permissions import get_user_permissions

# target doctype -> VMS doctypes whose forms link to it
LINK_CONTEXTS = {
	"Employee": (
		"Visitor Pass",
		"Visitor Invitation",
		"Security Log",
		"Hospitality Request",
		"Factory Tour Area",
		"Conference Room Booking",
	),
	"Supplier": ("Visitor Pass", "Hospitality Request"),
	"Job Applicant": ("Visitor Pass", "Visitor Invitation"),
	"Maintenance Visit": ("Visitor Pass",),
}

# A child table is authorised through the form it is edited in.
CONTEXT_PERMISSION_DOCTYPE = {"Factory Tour Area": "Hospitality Request"}

# Report filters (no form) are authorised through Visitor Pass.
DEFAULT_CONTEXT = "Visitor Pass"

TITLE_FIELDS = {
	"Employee": "employee_name",
	"Supplier": "supplier_name",
	"Job Applicant": "applicant_name",
	"Maintenance Visit": "customer_name",
}

# The only fields a form may filter on. Anything else is refused rather than
# ignored, so a filter meant to narrow the list can never silently widen it.
FILTERABLE_FIELDS = {
	"Employee": {"name", "status", "user_id", "department", "company", "designation"},
	"Supplier": {"name", "disabled", "supplier_group"},
	"Job Applicant": {"name", "status", "job_title"},
	"Maintenance Visit": {"name", "docstatus", "customer", "completion_status"},
}

# Applied unless the form filters on the same field itself.
DEFAULT_FILTERS = {
	"Employee": {"status": "Active"},
	"Supplier": {"disabled": 0},
}


def authorize(doctype: str, reference_doctype: str | None = None) -> None:
	"""Raise PermissionError unless the user may pick `doctype` from `reference_doctype`."""
	context = reference_doctype or DEFAULT_CONTEXT
	if context not in LINK_CONTEXTS.get(doctype, ()):
		frappe.throw(_("{0} cannot be picked here.").format(_(doctype)), frappe.PermissionError)
	permission_doctype = CONTEXT_PERMISSION_DOCTYPE.get(context, context)
	if not frappe.has_permission(permission_doctype, "read"):
		frappe.throw(
			_("You need access to {0} to pick a {1}.").format(_(permission_doctype), _(doctype)),
			frappe.PermissionError,
		)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def search(doctype, txt, searchfield, start, page_len, filters=None, **kwargs):
	"""Custom link query (see module docstring) — returns (name, title) rows."""
	authorize(doctype, kwargs.get("reference_doctype"))

	title_field = TITLE_FIELDS[doctype]
	conditions = _filters(doctype, filters) + _user_permission_filters(doctype)
	or_filters = []
	if txt:
		or_filters = [[doctype, "name", "like", f"%{txt}%"], [doctype, title_field, "like", f"%{txt}%"]]

	return frappe.get_all(
		doctype,
		filters=conditions,
		or_filters=or_filters,
		fields=["name", title_field],
		order_by=f"{title_field} asc",
		limit_start=start,
		limit_page_length=page_len,
		as_list=True,
	)


def is_visible(doctype: str, name: str) -> bool:
	"""Whether `name` is among the records `search` would offer this user."""
	conditions = [[doctype, "name", "=", name], *_user_permission_filters(doctype)]
	return bool(frappe.get_all(doctype, filters=conditions, limit=1, pluck="name"))


def _filters(doctype, filters):
	allowed = FILTERABLE_FIELDS[doctype]
	conditions = []
	given = set()

	if isinstance(filters, str):
		filters = frappe.parse_json(filters)
	if isinstance(filters, dict):
		filters = [
			[doctype, field, *(value if isinstance(value, list | tuple) else ["=", value])]
			for field, value in filters.items()
		]

	for row in filters or []:
		row = list(row)
		if len(row) == 3:
			row = [doctype, *row]
		if len(row) != 4 or row[0] != doctype or row[1] not in allowed:
			frappe.throw(
				_("Filter {0} is not allowed for {1}.").format(row, _(doctype)), frappe.PermissionError
			)
		conditions.append(row)
		given.add(row[1])

	for field, value in DEFAULT_FILTERS.get(doctype, {}).items():
		if field not in given:
			conditions.append([doctype, field, "=", value])
	return conditions


def _user_permission_filters(doctype):
	"""The user's User Permissions on the target itself and on its Company."""
	if frappe.session.user == "Administrator":
		return []
	user_permissions = get_user_permissions(frappe.session.user)
	conditions = []

	names = _applicable(user_permissions.get(doctype), doctype)
	if names is not None:
		conditions.append([doctype, "name", "in", names])

	if frappe.get_meta(doctype).has_field("company"):
		companies = _applicable(user_permissions.get("Company"), doctype)
		if companies is not None:
			conditions.append([doctype, "company", "in", companies])
	return conditions


def _applicable(entries, doctype):
	"""Values a User Permission restricts `doctype` to, or None when unrestricted."""
	values = [
		e.get("doc")
		for e in entries or []
		if not e.get("applicable_for") or e.get("applicable_for") == doctype
	]
	return values or None
