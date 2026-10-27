"""Who approves a Visitor Pass right now, and the scheduled jobs that follow from it.

A pending pass is approved by everyone holding its lane's role — unless the
pass names its approvers:

* VMS Settings -> "Send approvals to the host's own manager" sends the FIRST
  lane to the host's `reports_to` alone. It falls back to the lane role when the
  host has no manager, the manager has no enabled user, or the manager could not
  approve it anyway (they host the visit or raised the pass).
* A VMS Approval Delegation lets an approver on leave hand their approvals to a
  stand-in for a date range. The stand-in can act wherever the approver could:
  the passes routed to that approver, and every lane of a role the approver holds.

`resolve_approvers` is the one place that works this out. VisitorPass stores its
answer on every save (`routed_approver`, `current_approvers`) because a workflow
condition can only read the record, and checks the live answer again when a
decision is made. Reminders reach the same people.

Why a dedicated role for stand-ins and managers
-----------------------------------------------
Frappe offers a transition only to a role. Handing the stand-in the approver's
own role (CEO, HR Manager, ...) would give them everything that role does in
every app, and someone would have to remember to take it back. Instead the
named approvers hold ASSIGNED_APPROVER_ROLE, whose transitions and row access
all require the user to be named on that pass, and the name is recomputed from
the delegation's dates. The role is handed out when a user is first named and
taken back by `release_assigned_role` once nothing names them any more.
"""

import frappe
from frappe import _
from frappe.query_builder import CustomFunction
from frappe.query_builder.functions import Coalesce, Concat, IfNull
from frappe.utils import add_to_date, get_url_to_form, getdate, now_datetime, nowdate

from visitormanagement.visitor_management import settings as vms_settings
from visitormanagement.visitor_management.mail import esc, send_after_commit
from visitormanagement.visitor_management.workflow_builder import (
	ASSIGNED_APPROVER_ROLE,
	approver_roles,
	lane_for_role,
	pending_lanes,
)

VISITOR_PASS = "Visitor Pass"
DELEGATION = "VMS Approval Delegation"
LANE_PREFIX = lane_for_role("")

# Passes handled per hourly run, so a backlog drains over a few runs.
REMINDER_BATCH = 100
REFRESH_BATCH = 500

# Remembers which users this app gave ASSIGNED_APPROVER_ROLE to, so the clean-up
# never takes away a role an administrator assigned by hand.
_GRANTED_MARKER = "vms_assigned_approver_granted:{user}"

Locate = CustomFunction("LOCATE", ["substring", "string"])


# ─────────────────────────────────────────────────────────
# Resolution
# ─────────────────────────────────────────────────────────
def lane_role(state):
	"""The role whose lane `state` is, or None for any other state."""
	if not state or state not in pending_lanes():
		return None
	return state[len(LANE_PREFIX) :]


def _employee_user(employee):
	return frappe.db.get_value("Employee", employee, "user_id") if employee else None


def routed_manager(doc):
	"""The host's manager when this pass's current lane goes to them alone; else None."""
	if not vms_settings.route_approval_to_manager() or not doc.get("visitor_type"):
		return None
	primary = frappe.db.get_value("Visitor Type", doc.visitor_type, "approver_role")
	if not primary or doc.get("workflow_state") != lane_for_role(primary) or not doc.get("person_to_visit"):
		return None
	manager = frappe.db.get_value("Employee", doc.person_to_visit, "reports_to")
	user = _employee_user(manager)
	if not user or not frappe.db.get_value("User", user, "enabled"):
		return None
	# Neither may approve this pass (workflow_builder._NOT_THE_HOST, allow_self_approval),
	# so routing it to them would leave it with nobody.
	if user in (_employee_user(doc.person_to_visit), doc.get("owner")):
		return None
	return user


def role_holders(role):
	"""Enabled desk users holding `role`."""
	has_role = frappe.qb.DocType("Has Role")
	user = frappe.qb.DocType("User")
	return (
		frappe.qb.from_(has_role)
		.join(user)
		.on(user.name == has_role.parent)
		.select(user.name)
		.distinct()
		.where(has_role.parenttype == "User")
		.where(has_role.role == role)
		.where(user.enabled == 1)
		.where(user.user_type == "System User")
		.where(user.name.notin(("Administrator", "Guest")))
		.orderby(user.name)
		.run(pluck=True)
	)


def active_delegates(approvers, on_date=None):
	"""{approver: [stand-ins]} for the delegations in force on `on_date` (today)."""
	approvers = [a for a in approvers or () if a]
	if not approvers:
		return {}
	on_date = getdate(on_date or nowdate())
	rows = frappe.get_all(
		DELEGATION,
		filters={
			"approver": ("in", approvers),
			"is_active": 1,
			"from_date": ("<=", on_date),
			"to_date": (">=", on_date),
		},
		fields=["approver", "delegate"],
		order_by="from_date asc, creation asc",
	)
	if not rows:
		return {}
	enabled = set(
		frappe.get_all(
			"User", filters={"name": ("in", {r.delegate for r in rows}), "enabled": 1}, pluck="name"
		)
	)
	delegates = {}
	for row in rows:
		if row.delegate in enabled and row.delegate != row.approver:
			listed = delegates.setdefault(row.approver, [])
			if row.delegate not in listed:
				listed.append(row.delegate)
	return delegates


def resolve_approvers(doc):
	"""Who may decide this pass in the lane it is in.

	Returns a dict with:
	  role      the lane role (None when the pass is not waiting in a lane)
	  routed    the manager the lane is routed to, or None
	  named     the approvers the pass names: the routed manager and every
	            stand-in, in that order — stored as `current_approvers`
	  approvers everyone who can act: `named`, plus the role holders when the
	            lane is not routed — who reminders go to
	"""
	role = lane_role(doc.get("workflow_state"))
	if not role:
		return frappe._dict(role=None, routed=None, named=[], approvers=[])
	routed = routed_manager(doc)
	base = [routed] if routed else role_holders(role)
	# Neither the host nor whoever raised the pass may approve it, so neither is
	# named for it — not even as somebody's stand-in.
	cannot = {_employee_user(doc.get("person_to_visit")), doc.get("owner")}
	named = [routed] if routed else []
	for delegates in active_delegates(base).values():
		named.extend(d for d in delegates if d not in named and d not in cannot)
	approvers = [u for u in base if u not in cannot]
	approvers.extend(u for u in named if u not in approvers)
	return frappe._dict(role=role, routed=routed, named=named, approvers=approvers)


def may_decide(doc, user=None):
	"""Whether `user` may approve or reject this pass in its current lane, as of now.

	The stored `current_approvers` can trail reality (a delegation that ended an
	hour ago), so a decision is judged on this, never on the stored list alone.
	"""
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	resolved = resolve_approvers(doc)
	if not resolved.role:
		return False
	if user in resolved.named:
		return True
	return not resolved.routed and resolved.role in frappe.get_roles(user)


def is_named(doc, user=None):
	"""Whether the pass, as stored, names `user` as one of its approvers."""
	user = user or frappe.session.user
	return user in (doc.get("current_approvers") or "").split(",")


def is_approver(user=None):
	"""Whether `user` approves passes at all: an approver role, or a manager approvals can go to."""
	user = user or frappe.session.user
	roles = set(frappe.get_roles(user))
	if roles & set(approver_roles()) or ASSIGNED_APPROVER_ROLE in roles:
		return True
	if not vms_settings.route_approval_to_manager():
		return False
	employees = frappe.get_all("Employee", filters={"user_id": user}, pluck="name")
	return bool(
		employees and frappe.db.exists("Employee", {"reports_to": ("in", employees), "status": "Active"})
	)


# ─────────────────────────────────────────────────────────
# The role named approvers act through
# ─────────────────────────────────────────────────────────
def grant_assigned_role(users):
	"""Give each of `users` ASSIGNED_APPROVER_ROLE, if they do not hold it yet.

	Written as a Has Role row rather than through User.save, which would run the
	whole User validation (and the HR apps' role clean-up) inside a pass save.
	"""
	if not frappe.db.exists("Role", ASSIGNED_APPROVER_ROLE):
		return
	for user in {u for u in users or () if u and u not in ("Administrator", "Guest")}:
		if frappe.db.exists(
			"Has Role", {"parent": user, "parenttype": "User", "role": ASSIGNED_APPROVER_ROLE}
		):
			continue
		frappe.get_doc(
			{
				"doctype": "Has Role",
				"parent": user,
				"parenttype": "User",
				"parentfield": "roles",
				"role": ASSIGNED_APPROVER_ROLE,
			}
		).db_insert()
		frappe.db.set_default(_GRANTED_MARKER.format(user=user), "1")
		frappe.clear_cache(user=user)


def _still_named(user):
	"""Whether anything still names `user`: a pending pass, or a delegation not yet over."""
	if frappe.db.exists(
		DELEGATION, {"delegate": user, "is_active": 1, "to_date": (">=", getdate(nowdate()))}
	):
		return True
	visitor_pass = frappe.qb.DocType(VISITOR_PASS)
	return bool(
		frappe.qb.from_(visitor_pass)
		.select(visitor_pass.name)
		.where(visitor_pass.docstatus == 0)
		.where(visitor_pass.workflow_state.isin(sorted(pending_lanes()) or [""]))
		.where(named_condition(visitor_pass, user))
		.limit(1)
		.run()
	)


def named_condition(table, user):
	"""A query-builder condition: `table.current_approvers` lists `user` exactly."""
	# An exact match within the comma-separated list. Not LIKE: `_`, common in
	# user ids, is a LIKE wildcard and would match somebody else's id.
	return Locate(Concat(",", user, ","), Concat(",", IfNull(table.current_approvers, ""), ",")) > 0


def release_assigned_role():
	"""Take ASSIGNED_APPROVER_ROLE back from the users this app gave it to and nothing names any more."""
	prefix = _GRANTED_MARKER.split("{", 1)[0]
	released = 0
	for key in frappe.get_all(
		"DefaultValue", filters={"parent": "__default", "defkey": ("like", prefix + "%")}, pluck="defkey"
	):
		user = key[len(prefix) :]
		if _still_named(user):
			continue
		frappe.db.delete("Has Role", {"parent": user, "parenttype": "User", "role": ASSIGNED_APPROVER_ROLE})
		frappe.db.delete("DefaultValue", {"parent": "__default", "defkey": key})
		frappe.clear_cache(user=user)
		released += 1
	return released


# ─────────────────────────────────────────────────────────
# Keeping the stored names current
# ─────────────────────────────────────────────────────────
def refresh_pending_assignments(limit=REFRESH_BATCH):
	"""Re-resolve the approvers of passes waiting in a lane, and store any change.

	A delegation starts or ends by date, a manager changes in HR, the routing
	setting is switched: none of that saves the pass. Written straight to the row
	(update_modified=False) — it corrects derived fields, it is not an edit.
	Returns the number of passes changed.
	"""
	lanes = sorted(pending_lanes())
	if not lanes:
		return 0
	changed = 0
	for row in frappe.get_all(
		VISITOR_PASS,
		filters={"docstatus": 0, "workflow_state": ("in", lanes)},
		fields=["name", "routed_approver", "current_approvers"],
		order_by="modified asc",
		limit=limit,
	):
		doc = frappe.get_doc(VISITOR_PASS, row.name)
		resolved = resolve_approvers(doc)
		values = {"routed_approver": resolved.routed, "current_approvers": ",".join(resolved.named) or None}
		if (row.routed_approver or None, row.current_approvers or None) == (
			values["routed_approver"],
			values["current_approvers"],
		):
			continue
		grant_assigned_role(resolved.named)
		frappe.db.set_value(VISITOR_PASS, row.name, values, update_modified=False)
		changed += 1
	return changed


# ─────────────────────────────────────────────────────────
# Reminders
# ─────────────────────────────────────────────────────────
def remind_pending_approvers():
	"""Hourly: remind the approvers of passes that have waited too long in a lane.

	"Too long" is VMS Settings -> approval_reminder_hours (0 switches reminders
	off). Each pass is reminded at most once per that many hours, and only the
	people who can act on it are told — its routed manager and stand-ins, or the
	lane role's holders — in the bell and by email.

	Runs the delegation housekeeping first, so the reminders reach this hour's
	approvers and a stand-in whose window has closed is no longer named anywhere.
	"""
	refresh_pending_assignments()
	release_assigned_role()

	hours = vms_settings.approval_reminder_hours()
	lanes = sorted(pending_lanes())
	if not hours or not lanes:
		return 0

	now = now_datetime()
	cutoff = add_to_date(now, hours=-hours)
	visitor_pass = frappe.qb.DocType(VISITOR_PASS)
	waiting_since = Coalesce(visitor_pass.pending_since, visitor_pass.modified)
	names = (
		frappe.qb.from_(visitor_pass)
		.select(visitor_pass.name)
		.where(visitor_pass.docstatus == 0)
		.where(visitor_pass.workflow_state.isin(lanes))
		.where(waiting_since <= cutoff)
		.where(
			visitor_pass.approval_reminder_sent_on.isnull()
			| (visitor_pass.approval_reminder_sent_on <= cutoff)
		)
		.orderby(waiting_since)
		.limit(REMINDER_BATCH)
		.run(pluck=True)
	)

	reminded = 0
	for name in names:
		doc = frappe.get_doc(VISITOR_PASS, name)
		recipients = resolve_approvers(doc).approvers
		# Stamped even with nobody to tell, so the pass is not picked up every hour.
		frappe.db.set_value(VISITOR_PASS, name, "approval_reminder_sent_on", now, update_modified=False)
		if not recipients:
			continue
		try:
			_send_reminder(doc, recipients, hours)
			reminded += 1
		except Exception:
			frappe.log_error(
				title=f"VMS: approval reminder for {name} not sent",
				message=frappe.get_traceback(with_context=True),
			)
	return reminded


def _send_reminder(doc, users, hours):
	subject = _("Waiting for your approval: visitor pass {0} ({1})").format(
		doc.name, doc.visitor_full_name or ""
	)
	url = get_url_to_form(doc.doctype, doc.name)
	rows = (
		(_("Visitor"), doc.visitor_full_name),
		(_("Company"), doc.company__organisation),
		(_("Visit date"), doc.visit_date),
		(_("Host"), doc.get("host_name") or doc.person_to_visit),
		(_("Purpose"), doc.purpose_of_visit),
	)
	message = (
		"<div style='font-family:Arial,sans-serif;font-size:13px;color:#1f2933;'>"
		"<p style='margin:0 0 10px;'>"
		+ esc(_("This visitor pass has been waiting for approval for more than {0} hours.").format(hours))
		+ "</p><p style='margin:0 0 10px;line-height:1.7;'>"
		+ "<br>".join(
			f"<b style='color:#5b6b7b;'>{esc(label)}:</b> {esc(value, '-')}" for label, value in rows
		)
		+ "</p>"
		+ f"<p style='margin:12px 0 0;'><a href='{esc(url)}' style='color:#2563eb;'>"
		+ esc(_("Open the pass to approve or reject it"))
		+ "</a></p></div>"
	)

	emails = [
		email for email in frappe.get_all("User", filters={"name": ("in", users)}, pluck="email") if email
	]
	if emails:
		messages_before = list(frappe.message_log)
		try:
			send_after_commit(
				recipients=emails,
				subject=subject,
				message=message,
				reference_doctype=doc.doctype,
				reference_name=doc.name,
			)
		except Exception:
			# No outgoing Email Account: the bell entries below still go out.
			frappe.local.message_log = messages_before
			frappe.log_error(title=f"VMS: approval reminder mail for {doc.name} not sent")

	for user in users:
		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"for_user": user,
				"type": "Alert",
				"document_type": doc.doctype,
				"document_name": doc.name,
				"subject": esc(subject),
				"email_content": message,
			}
		).insert(ignore_permissions=True)
