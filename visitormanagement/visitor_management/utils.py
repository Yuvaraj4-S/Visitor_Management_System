"""Shared helpers for the Visitor Management module.

Kept free of doctype controllers so Gate Entry Request, Visitor Pass, Security Log,
Hospitality Request and the gate APIs can all use the same rules.
"""

import frappe

# Same gate rules Security Log applies when it auto-assigns a gate.
GATE_BY_VISITOR_TYPE = {
	"VIP": "VIP Entrance",
	"Supplier": "Loading Dock",
	"Contractor": "Back Gate",
	"Candidate": "Main Gate",
	"Customer": "Main Gate",
}

SINGLE_PERSON = "Single Person"
GROUP = "Group"


# ─────────────────────────────────────────────────────────
# Employees / groups
# ─────────────────────────────────────────────────────────
def get_employee_for_user(user=None, active_only=True):
	"""Employee linked to a User (Active only by default)."""
	user = user or frappe.session.user
	if not user or user in ("Administrator", "Guest"):
		return None
	filters = {"user_id": user}
	if active_only:
		filters["status"] = "Active"
	return frappe.db.get_value("Employee", filters, "name")


def get_group_employees(employee_group):
	"""Active employees of an Employee Group, in the group's row order."""
	if not employee_group:
		return []
	rows = frappe.get_all(
		"Employee Group Table",
		filters={"parent": employee_group, "parenttype": "Employee Group"},
		fields=["employee"],
		order_by="idx asc",
	)
	employees = [r.employee for r in rows if r.employee]
	if not employees:
		return []
	active = set(frappe.get_all("Employee", filters={"name": ["in", employees], "status": "Active"}, pluck="name"))
	return [e for e in employees if e in active]


def get_employee_email(employee):
	if not employee:
		return None
	row = frappe.db.get_value("Employee", employee, ["company_email", "personal_email", "user_id"], as_dict=True)
	if not row:
		return None
	return row.company_email or row.personal_email or row.user_id


def get_employee_users(employees):
	"""Enabled login users of the given employees."""
	if not employees:
		return []
	users = frappe.get_all("Employee", filters={"name": ["in", list(employees)]}, pluck="user_id")
	users = [u for u in users if u]
	if not users:
		return []
	return frappe.get_all("User", filters={"name": ["in", users], "enabled": 1}, pluck="name")


def get_mapped_employees(doc):
	"""Employees a visit is mapped to: the person to visit, or every group member."""
	if (doc.get("mapping_type") or SINGLE_PERSON) == GROUP and doc.get("visitor_group"):
		return get_group_employees(doc.visitor_group)
	return [doc.person_to_visit] if doc.get("person_to_visit") else []


def is_group_member(employee_group, employee):
	if not (employee_group and employee):
		return False
	return bool(
		frappe.db.exists(
			"Employee Group Table",
			{"parent": employee_group, "parenttype": "Employee Group", "employee": employee},
		)
	)


# ─────────────────────────────────────────────────────────
# Roles / notifications
# ─────────────────────────────────────────────────────────
def get_role_users(roles):
	"""Enabled system users holding any of the roles (Administrator excluded)."""
	if isinstance(roles, str):
		roles = [roles]
	roles = [r for r in roles or [] if r]
	if not roles:
		return []
	users = frappe.get_all(
		"Has Role", filters={"role": ["in", roles], "parenttype": "User"}, pluck="parent", distinct=True
	)
	users = [u for u in users if u not in ("Administrator", "Guest")]
	if not users:
		return []
	return frappe.get_all("User", filters={"name": ["in", users], "enabled": 1}, pluck="name")


def user_emails(users):
	if not users:
		return []
	emails = frappe.get_all("User", filters={"name": ["in", list(users)]}, pluck="email")
	return sorted({e for e in emails if e and "@" in e})


def notify_users(users, subject, message, doctype=None, docname=None, email=True):
	"""Email + bell notification. Never raises: a mail failure must not block the flow."""
	users = [u for u in dict.fromkeys(users or []) if u and u not in ("Administrator", "Guest")]
	if not users:
		return
	for user in users:
		try:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"for_user": user,
					"type": "Alert",
					"subject": subject,
					"email_content": message,
					"document_type": doctype,
					"document_name": docname,
				}
			).insert(ignore_permissions=True)
		except Exception:
			frappe.log_error(frappe.get_traceback(), "VMS Notification Log")
	if email:
		send_mail(user_emails(users), subject, message, doctype, docname)


def send_mail(recipients, subject, message, doctype=None, docname=None, attachments=None, cc=None):
	recipients = [r for r in dict.fromkeys(recipients or []) if r]
	if not recipients:
		return
	try:
		frappe.sendmail(
			recipients=recipients,
			cc=cc or None,
			subject=subject,
			message=message,
			reference_doctype=doctype,
			reference_name=docname,
			attachments=attachments or None,
		)
	except Exception:
		frappe.log_error(frappe.get_traceback(), "VMS Email")


def doc_link(doctype, name):
	route = frappe.scrub(doctype).replace("_", "-")
	return f"{frappe.utils.get_url()}/app/{route}/{name}"


# ─────────────────────────────────────────────────────────
# ID proof masking
# ─────────────────────────────────────────────────────────
def mask_id_proof(id_proof_number, id_proof_type=None):
	"""Show only the last 4 characters; Aadhaar as `XXXX XXXX 1234`."""
	if not id_proof_number:
		return id_proof_number
	value = str(id_proof_number).strip()
	compact = value.replace(" ", "").replace("-", "")
	if len(compact) <= 4:
		return value
	visible = compact[-4:]
	if (id_proof_type or "").lower().startswith("aadhaar") and len(compact) == 12:
		return f"XXXX XXXX {visible}"
	return "X" * (len(compact) - 4) + visible


def is_masked(value):
	return bool(value) and "X" in str(value).upper() and str(value)[-4:].isalnum()


def can_view_full_id(doc, user=None):
	"""Owner, host / group member, System Manager, and Security during an active visit."""
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	roles = set(frappe.get_roles(user))
	if "System Manager" in roles:
		return True
	if doc.get("owner") == user:
		return True
	employee = get_employee_for_user(user, active_only=False)
	if employee:
		if doc.get("person_to_visit") == employee:
			return True
		if doc.get("mapping_type") == GROUP and is_group_member(doc.get("visitor_group"), employee):
			return True
	if "Security" in roles:
		status = doc.get("status")
		if doc.doctype == "Gate Entry Request":
			return status in ("Draft", "Pending Approval", "Approved")
		return status in ("Approved", "Items Verified", "Checked-In")
	return False


def mask_for_user(doc, value=None, user=None):
	value = doc.get("id_proof_number") if value is None else value
	if not value or can_view_full_id(doc, user):
		return value
	return mask_id_proof(value, doc.get("id_proof_type"))


def restore_masked_id(doc):
	"""A form opened with a masked ID sends the masked value back on save; keep the stored value."""
	if doc.is_new() or not doc.get("id_proof_number"):
		return
	stored = frappe.db.get_value(doc.doctype, doc.name, "id_proof_number")
	if stored and doc.id_proof_number != stored and doc.id_proof_number == mask_id_proof(stored, doc.get("id_proof_type")):
		doc.id_proof_number = stored


# ─────────────────────────────────────────────────────────
# Recurring visits
# ─────────────────────────────────────────────────────────
def recurring_window(vp):
	"""(start, end) dates for a recurring pass, else None."""
	from frappe.utils import cint, getdate

	if not cint(vp.get("is_recurring")) or not vp.get("recurring_end_date"):
		return None
	start = vp.get("recurring_start_date") or vp.get("visit_date")
	return getdate(start), getdate(vp.recurring_end_date)


def in_recurring_window(vp, on_date=None):
	from frappe.utils import getdate, today

	window = recurring_window(vp)
	if not window:
		return False
	day = getdate(on_date or today())
	return window[0] <= day <= window[1]


def checked_in_on(visitor_pass, on_date=None):
	"""Has the pass already checked in on this date?"""
	from frappe.utils import add_days, getdate, today

	day = getdate(on_date or today())
	return bool(
		frappe.db.exists(
			"Security Log",
			{
				"visitor_pass": visitor_pass,
				"event_type": "Check-In",
				"check_in_date_time": ["between", [f"{day} 00:00:00", f"{add_days(day, 1)} 00:00:00"]],
			},
		)
	)
