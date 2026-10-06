import re

import frappe
from frappe import _
from frappe.utils import cint


def _has_any_role(user_roles, roles):
	return any(role in user_roles for role in roles)


def _is_admin(user):
	return user == "Administrator"


def _owner_condition(table_name, user):
	return f"`{table_name}`.`owner` = {frappe.db.escape(user)}"


def _employee_for_user(user):
	"""Return the Employee record linked to a User, if any."""
	if not user or user in ("Administrator", "Guest"):
		return None
	return frappe.db.get_value("Employee", {"user_id": user}, "name")


def get_visitor_pass_permission_query_conditions(user=None):
	user = user or frappe.session.user
	if _is_admin(user):
		return None

	roles = set(frappe.get_roles(user))
	table = "tabVisitor Pass"

	# System Manager is the site's administrative role and its sibling
	# get_visitor_invitation_permission_query_conditions already exempts it. This
	# one did not, so a System Manager who was not literally logged in as
	# Administrator saw only the passes they owned or hosted — 118 of 185 on this
	# site. The dashboard was the visible symptom: "Checked-In Visitors" and
	# "Pending Visitor Approvals" quietly under-reported while presenting
	# themselves as org-wide operations totals.
	if "System Manager" in roles:
		return None

	_refuse_full_id_filters()

	conditions = [_owner_condition(table, user)]

	# Hospitality Manager and Facility Manager used to see every Visitor Pass, so
	# the pass link on Hospitality Request / Conference Room Booking would work.
	# That let both roles read every visitor's record. They now see only the
	# passes their own work is about (see _needs_hospitality / _uses_a_room): the
	# link, its search and the values fetched through it keep working for those,
	# and every other pass is closed to them like to anyone else.
	if "Hospitality Manager" in roles:
		flags = " or ".join(f"ifnull(`{table}`.`{f}`, 0) = 1" for f in _HOSPITALITY_FLAG_FIELDS)
		conditions.append(
			f"(ifnull(`{table}`.`hospitality_request`, '') != '' or {flags} or `{table}`.`name` in "
			"(select `visitor_pass` from `tabHospitality Request` where `visitor_pass` is not null))"
		)
	if "Facility Manager" in roles:
		conditions.append(
			f"(ifnull(`{table}`.`conference_room`, '') != '' or `{table}`.`name` in "
			"(select `visitor_pass` from `tabConference Room Booking` where `visitor_pass` is not null))"
		)

	# Host scope — user can see any pass where they are person_to_visit (the host)
	employee = _employee_for_user(user)
	if employee:
		conditions.append(f"`{table}`.`person_to_visit` = {frappe.db.escape(employee)}")

	# Approver scope — each role owns the Visitor Types where it is the
	# configured approver_role/secondary_approver_role, end-to-end.
	owned_types = frappe.get_all(
		"Visitor Type",
		or_filters=[["approver_role", "in", list(roles)], ["secondary_approver_role", "in", list(roles)]],
		pluck="name",
	)
	if owned_types:
		type_list = ", ".join(frappe.db.escape(t) for t in owned_types)
		conditions.append(f"`{table}`.`visitor_type` in ({type_list})")
	if "Security" in roles:
		conditions.append(
			f"`{table}`.`status` in ('Approved', 'Items Verified', 'Checked-In', 'Checked-Out')"
		)

	# Parenthesised: Frappe ANDs this fragment with its own clauses (User
	# Permissions, share filters). Unbracketed, `AND` binds tighter than `OR`, so
	# a User Permission would constrain only the first disjunct and every other
	# branch would widen the result set past it.
	return "(" + " or ".join(conditions) + ")" if conditions else "1=0"


def get_visitor_invitation_permission_query_conditions(user=None):
	"""Scope Visitor Invitation to the host who raised it.

	An invitation row carries `invitation_token` — the bearer credential that
	opens the visitor's pre-registration form. Employee holds read+write on this
	doctype so hosts can invite their own guests, and without a row filter that
	meant every member of staff could read every token and edit anyone's
	invitation. The token is a capability, so listing it is disclosing it.
	"""
	user = user or frappe.session.user
	if _is_admin(user):
		return None

	roles = set(frappe.get_roles(user))
	table = "tabVisitor Invitation"

	if "System Manager" in roles:
		return None

	conditions = [_owner_condition(table, user)]
	employee = _employee_for_user(user)
	if employee:
		conditions.append(f"`{table}`.`host_employee` = {frappe.db.escape(employee)}")

	# Approvers oversee the visitor types they are responsible for — the same
	# scope they get on Visitor Pass, rather than blanket visibility.
	owned_types = _approver_visitor_types(roles)
	if owned_types:
		type_list = ", ".join(frappe.db.escape(t) for t in owned_types)
		conditions.append(f"`{table}`.`visitor_type` in ({type_list})")

	return "(" + " or ".join(conditions) + ")"


def _approver_visitor_types(roles):
	return frappe.get_all(
		"Visitor Type",
		or_filters=[["approver_role", "in", list(roles)], ["secondary_approver_role", "in", list(roles)]],
		pluck="name",
	)


def has_visitor_invitation_permission(doc, user=None, ptype=None, debug=False):
	user = user or frappe.session.user
	if _is_admin(user):
		return True

	roles = set(frappe.get_roles(user))
	if "System Manager" in roles:
		return True

	if doc.owner == user:
		return True

	employee = _employee_for_user(user)
	if employee and doc.host_employee == employee:
		return True

	# Approver oversight is visibility, not custody. Without this the approver
	# branch granted write as well, so any approver role could edit another
	# host's invitation — including repointing `visitor_email` and triggering a
	# resend, which mails the bearer token to an address of their choosing.
	if ptype in _READ_LIKE_PTYPES:
		return bool(doc.visitor_type and doc.visitor_type in _approver_visitor_types(roles))

	return False


# Permission types that count as "read-like" — holding one of these does NOT let
# the user write/submit/cancel/delete the doc.
#
# `None` is deliberately absent. Frappe invokes this hook as
# `frappe.call(method, doc=..., ptype=..., user=...)`, and `frappe.call` drops
# any keyword the function does not declare — so while the parameter here was
# named `permission_type`, it was *always* None, None was in this set, and every
# read-only branch below returned True for write and submit as well. An unknown
# permission type now falls through to the deny path instead.
#
# `share` is absent for the same reason it matters here: sharing a Visitor
# Invitation hands over the `invitation_token` inside it, which is a bearer
# credential, not a view.
_READ_LIKE_PTYPES = {"read", "select", "print", "email", "report", "export"}

# What raising a pass entitles you to on your own pass. Deliberately excludes
# `submit`, `cancel` and `amend`: those are approval authority, and approval must
# come from the role the Visitor Type names, never from having created the
# record. Without this boundary an owner or host who happened to hold any
# blanket submit role (System Manager, Sales Manager, HR Manager, HOD, CEO —
# for any unrelated reason) could take a pass sitting in someone else's approval
# lane, set docstatus=1, save, and land it at "Approved" with the configured
# approver never involved. Frappe's own machinery then completes the illusion:
# `set_workflow_state_on_action` force-sets workflow_state to whichever state
# carries doc_status 1, so the pass reads as legitimately approved and every
# downstream control — badge minting, the gate — correctly trusts it.
_OWNER_PTYPES = _READ_LIKE_PTYPES | {"write", "create", "delete"}

# What Hospitality Manager and Facility Manager get on a pass their work is about:
# enough for the pass link on their own forms (validate, fetch the visitor's name,
# open the pass), nothing that takes data out in bulk.
_LINK_READER_PTYPES = {"read", "select"}

# The requests on a pass that a Hospitality Request is raised for (the same list
# lifecycle.ensure_hospitality_request reads, without the room, which is the
# Facility Manager's).
_HOSPITALITY_FLAG_FIELDS = (
	"meal_required",
	"refreshments_required",
	"cab_required",
	"hotel_required",
	"factory_tour_required",
	"buggy_required",
	"greeting_required",
)


def _needs_hospitality(doc):
	"""A pass the hospitality desk works on: it asks for a service, or has a request."""
	if doc.get("hospitality_request") or any(cint(doc.get(f)) for f in _HOSPITALITY_FLAG_FIELDS):
		return True
	return bool(doc.name and frappe.db.exists("Hospitality Request", {"visitor_pass": doc.name}))


def _uses_a_room(doc):
	"""A pass the facility desk works on: it asks for a room, or a booking names it."""
	if doc.get("conference_room"):
		return True
	return bool(doc.name and frappe.db.exists("Conference Room Booking", {"visitor_pass": doc.name}))


# Request parameters through which a list, count, search or report query names
# the fields it filters, sorts or groups on.
_QUERY_SHAPE_PARAMS = (
	"filters",
	"or_filters",
	"current_filters",
	"order_by",
	"group_by",
	"group_by_field",
	"field",
	"stats",
)
_FULL_ID_FIELD = re.compile(r"id_proof_number(?!_masked|_entry)")


def _refuse_full_id_filters():
	"""Refuse a list query that filters or sorts on the stored (full) ID number.

	The full number is unreadable (permlevel 1), but Frappe 15 removes a field a
	user may not read only from the selected columns; it still lets the query
	filter on it (model/db_query.py: apply_fieldlevel_read_permissions strips
	`fields`, prepare_filter_condition has no such check). `like "1234%"` would
	then answer yes or no by the row count, and the number could be recovered a
	character at a time. A System Manager, who may read the number through the
	audited reveal, is not restricted.

	This looks at the current request, so it does not reach a filter stored in a
	saved Number Card, Dashboard Chart or Report Builder report; those are created
	by people who already hold wider rights.
	"""
	form_dict = getattr(frappe.local, "form_dict", None)
	if not form_dict or not getattr(frappe.local, "request", None):
		return
	# A query that names another DocType is about that DocType's own fields (the
	# Security Log's `id_proof_number` holds a masked value, for one).
	asked = form_dict.get("doctype")
	if isinstance(asked, str) and asked and asked != "Visitor Pass":
		return
	from visitormanagement.visitor_management.id_masking import can_view_full_id

	if can_view_full_id():
		return
	shape = " ".join(frappe.as_unicode(form_dict.get(key) or "") for key in _QUERY_SHAPE_PARAMS)
	if _FULL_ID_FIELD.search(shape):
		frappe.throw(_("Records cannot be filtered or sorted on the full ID number."), frappe.PermissionError)


def has_visitor_pass_permission(doc, user=None, ptype=None, debug=False):
	user = user or frappe.session.user
	if _is_admin(user):
		return True

	# Owner and host scope. Note these *fall through* rather than returning False
	# for anything outside _OWNER_PTYPES — a Sales Manager who happens to own a
	# Customer pass is still that pass's configured approver, and the approver
	# branch further down is what should decide that, not an early denial here.
	if doc.owner == user and ptype in _OWNER_PTYPES:
		return True

	# Host scope — user is the person the visitor is meeting
	employee = _employee_for_user(user)
	if employee and doc.person_to_visit == employee and ptype in _OWNER_PTYPES:
		return True

	roles = set(frappe.get_roles(user))

	# System Manager administers the site, so it gets the same reach here that it
	# has in the query conditions — but deliberately not `submit`/`cancel`/
	# `amend`. Those stay with the Visitor Type's configured approver for exactly
	# the reason _OWNER_PTYPES exists: System Manager is one of the blanket-submit
	# roles, and granting it here would reopen the self-approval bypass from the
	# other side. A System Manager who genuinely belongs to an approval lane still
	# reaches it through the approver branch below, and the workflow action itself
	# is unaffected — this only refuses a bare docstatus flip.
	if "System Manager" in roles and ptype in _OWNER_PTYPES:
		return True

	# Hospitality Manager and Facility Manager: open (not print, email or export)
	# the passes their own work is about, and no others. The ID number on those
	# passes is masked for them like for everyone, and the ID scan and visa copy
	# stay closed (can_open_id_documents).
	if ptype in _LINK_READER_PTYPES:
		if "Hospitality Manager" in roles and _needs_hospitality(doc):
			return True
		if "Facility Manager" in roles and _uses_a_room(doc):
			return True

	# Approver scope — role owns this doc's Visitor Type as approver_role or
	# secondary_approver_role.
	visitor_type_doc = None
	if doc.visitor_type:
		try:
			visitor_type_doc = frappe.get_cached_doc("Visitor Type", doc.visitor_type)
		except frappe.DoesNotExistError:
			visitor_type_doc = None
	if visitor_type_doc and (
		visitor_type_doc.approver_role in roles
		or (visitor_type_doc.secondary_approver_role and visitor_type_doc.secondary_approver_role in roles)
	):
		return True
	# Security role is read-only on Visitor Pass — gate workflow happens through
	# Security role is read-only on Visitor Pass. Gate workflow happens through
	# Security Log (where they have write). Without this guard, has_permission
	# would short-circuit `True` for every ptype and let Security write/submit
	# any approved pass.
	if "Security" in roles and doc.status in {
		"Approved",
		"Items Verified",
		"Checked-In",
		"Checked-Out",
	}:
		return ptype in _READ_LIKE_PTYPES

	return False


def check_visitor_pass_link(doc, overseer_roles=(), fieldname="visitor_pass"):
	"""Refuse a new link from `doc` to a Visitor Pass its saver has no business with.

	Hospitality Request and Conference Room Booking both name a Visitor Pass.
	Frappe validates such a link by existence alone, and both forms act on it:
	a Hospitality Request copies the visitor's name, mobile number and visit
	times from the pass (fetch_from and lifecycle.populate_hospitality_request_
	from_pass, neither of which asks who is saving) and writes the arrangements
	back onto it (lifecycle.sync_hospitality_to_pass); a booking answers with the
	pass's status. Any member of staff may raise either document, so naming
	somebody else's pass was a way to read and to change a pass the user could
	not open.

	A link may be set, or changed, by:

	* the roles that run the service (`overseer_roles`: raising a request for a
	  visitor is their work, whichever pass it is — a pass they link becomes one
	  they can open, see `_needs_hospitality` / `_uses_a_room`);
	* anyone who may read that pass (its owner, host, approvers, Security);
	* the app itself (`ignore_permissions`: the request and booking the pass
	  creates for itself, lifecycle.ensure_*).

	An existing document whose link is not being changed is not looked at, so
	staff assigned to deliver a request can still save it.
	"""
	pass_name = doc.get(fieldname)
	if not pass_name or doc.flags.ignore_permissions:
		return
	user = frappe.session.user
	if _is_admin(user):
		return
	if not doc.is_new() and frappe.db.get_value(doc.doctype, doc.name, fieldname) == pass_name:
		return
	if _has_any_role(set(frappe.get_roles(user)), overseer_roles):
		return
	if not frappe.db.exists("Visitor Pass", pass_name):
		return  # Frappe's own link validation reports a pass that does not exist
	if frappe.has_permission("Visitor Pass", "read", doc=pass_name, user=user):
		return
	frappe.throw(
		_("You do not have access to Visitor Pass {0}, so it cannot be linked to this {1}.").format(
			pass_name, _(doc.doctype)
		),
		frappe.PermissionError,
		title=_("Visitor Pass Not Available"),
	)


# ─────────────────────────────────────────────────────────
# Hospitality Request
# ─────────────────────────────────────────────────────────
# `Employee` holds read+write+create on Hospitality Request with if_owner = 0,
# and no query condition was registered — so every member of staff could open
# and overwrite every other host's request. That is not an abstract exposure:
# these rows carry dietary allergies, accessibility needs, hotel cost, and
# drivers' phone numbers. An audit proved it by editing another host's request
# from a Hospitality User account and pushing it into an approval queue.
#
# Scoped the same way Visitor Pass is: the people who run hospitality see
# everything, and everyone else sees the requests that are theirs — raised by
# them, assigned to them, or belonging to a visitor they are hosting.
HOSPITALITY_OVERSEERS = ("System Manager", "Hospitality Manager")


def get_hospitality_request_permission_query_conditions(user=None):
	user = user or frappe.session.user
	if _is_admin(user):
		return None

	roles = set(frappe.get_roles(user))
	if _has_any_role(roles, HOSPITALITY_OVERSEERS):
		return None

	table = "tabHospitality Request"
	conditions = [_owner_condition(table, user)]

	employee = _employee_for_user(user)
	if employee:
		escaped = frappe.db.escape(employee)
		# Assigned to them to deliver.
		conditions.append(f"`{table}`.`assigned_staff` = {escaped}")
		# Or raised for a visitor they are hosting. The host is on the pass, not
		# on the request, so this has to go through the link.
		conditions.append(
			f"`{table}`.`visitor_pass` in "
			f"(select `name` from `tabVisitor Pass` where `person_to_visit` = {escaped})"
		)

	# Parenthesised for the same reason as Visitor Pass: Frappe ANDs this with
	# its own clauses, and unbracketed `AND` would bind tighter than `OR`.
	return "(" + " or ".join(conditions) + ")"


def has_hospitality_request_permission(doc, user=None, ptype=None, debug=False):
	user = user or frappe.session.user
	if _is_admin(user):
		return True

	roles = set(frappe.get_roles(user))
	if _has_any_role(roles, HOSPITALITY_OVERSEERS):
		return True

	if doc.owner == user:
		return True

	employee = _employee_for_user(user)
	if employee:
		if doc.get("assigned_staff") == employee:
			return True
		if (
			doc.get("visitor_pass")
			and frappe.db.get_value("Visitor Pass", doc.visitor_pass, "person_to_visit") == employee
		):
			return True

	# If you are allowed to see the visit, you are allowed to see what was
	# arranged for it. This is not a convenience: `ensure_hospitality_request`
	# creates the request inside the approving user's own request, and Frappe's
	# `validate_workflow` calls `get_transitions`, which needs read on the new
	# document. Without this an approver who is not also the host — a CEO signing
	# off a VIP visit, say — got a PermissionError and the approval itself failed.
	# Visitor Pass is already row-scoped to owner/host/approver/Security, so this
	# inherits that boundary rather than widening past it.
	if ptype in _READ_LIKE_PTYPES and doc.get("visitor_pass"):
		# `has_permission` raises DoesNotExistError rather than returning False for
		# a missing document, so an orphaned request — one whose pass was deleted —
		# would throw out of a permission check and take the whole list view with
		# it. A permission question about a record that is not there is "no".
		if not frappe.db.exists("Visitor Pass", doc.visitor_pass):
			return False
		return bool(frappe.has_permission("Visitor Pass", "read", doc=doc.visitor_pass, user=user))

	return False


# ─────────────────────────────────────────────────────────
# Conference Room Booking
# ─────────────────────────────────────────────────────────
# `Employee` holds create/read/write on Conference Room Booking doctype-wide
# (no `if_owner`), and — unlike Visitor Pass, Visitor Invitation and
# Hospitality Request above — no query condition or has_permission hook was
# ever registered for it. So any member of staff could open any other
# employee's booking: its `meeting_title`, `department`, attendee count and
# `booked_by` are all visible with no scoping at all — a title such as
# "Board interview — CFO candidate" was readable by every Employee.
#
# Scoped the same way Hospitality Request is: the people who run the rooms
# see everything, and everyone else sees only their own bookings — made by
# them, or organised by them. Deliberately narrower than Visitor Pass's own
# conditions: there is no host/approver concept to extend visibility to here,
# and no read-only role like Security to carve an exception for — a booking
# is either yours to run or it isn't.
#
# Raw-SQL readers bypass this scoping, so each one applies it itself: the
# calendar (`get_booking_events`) and the Daily Booking Schedule report keep
# every booking visible but show "Busy" instead of the title unless the
# condition below matches, and the room-clash messages on Conference Room
# Booking and Visitor Pass name the other meeting only when
# `has_conference_room_booking_permission` allows it.
CONFERENCE_ROOM_BOOKING_OVERSEERS = ("System Manager", "Facility Manager")


def get_conference_room_booking_permission_query_conditions(user=None):
	user = user or frappe.session.user
	if _is_admin(user):
		return None

	roles = set(frappe.get_roles(user))
	if _has_any_role(roles, CONFERENCE_ROOM_BOOKING_OVERSEERS):
		return None

	table = "tabConference Room Booking"
	conditions = [_owner_condition(table, user)]

	employee = _employee_for_user(user)
	if employee:
		conditions.append(f"`{table}`.`booked_by` = {frappe.db.escape(employee)}")

	# Parenthesised for the same reason as the other three doctypes above:
	# Frappe ANDs this with its own clauses, and unbracketed `AND` would bind
	# tighter than `OR`.
	return "(" + " or ".join(conditions) + ")"


def has_conference_room_booking_permission(doc, user=None, ptype=None, debug=False):
	user = user or frappe.session.user
	if _is_admin(user):
		return True

	roles = set(frappe.get_roles(user))
	if _has_any_role(roles, CONFERENCE_ROOM_BOOKING_OVERSEERS):
		return True

	if doc.owner == user:
		return True

	employee = _employee_for_user(user)
	if employee and doc.get("booked_by") == employee:
		return True

	return False


# ─────────────────────────────────────────────────────────
# ID scans and visa copies
# ─────────────────────────────────────────────────────────
# A scan of a passport or an Aadhaar card is the most sensitive thing the app
# stores. Frappe decides who may open a private file by asking whether the user
# can read the record it is attached to (frappe/core/doctype/file/file.py
# has_permission -> ref_doc.has_permission("read")), so every host, and anyone
# else who could read a pass, could open the visitor's ID scan and visa copy.
#
# Only these may open them: Security, System Manager, and the approver roles of
# that pass's Visitor Type. The visitor's photo is not restricted (the host has
# to recognise the visitor).
#
# Two places enforce it, because Frappe has two ways in:
#   - `has_id_document_file_permission`, a `has_permission` hook on File, covers
#     everything that asks frappe.has_permission about a File row (reading the
#     File document, the API, sharing it);
#   - `guard_id_document_request`, an `auth_hooks` entry, covers the requests
#     that serve the bytes. Those call File.is_downloadable(), which runs core's
#     own check directly and never consults `has_permission` hooks
#     (frappe/utils/response.py download_private_file, frappe/handler.py
#     download_file -> core/doctype/file/utils.py find_file_by_url).
#
# Both look only at File rows attached to this app's own DocTypes below. A file
# of any other app, or of the site, is never examined beyond one indexed lookup
# that finds nothing, and never refused.

# DocType -> its fields that hold an ID document.
ID_DOCUMENT_FIELDS = {
	"Visitor Pass": ("id_proof_scan", "custom_visa_copy"),
	# The gate log shows the pass's scan (fetch_from) and has its own File row for it.
	"Security Log": ("id_proof_scan",),
}

# Roles that may open any ID document; the approvers of the pass's Visitor Type
# are added per document.
ID_DOCUMENT_ROLES = ("Security", "System Manager")

_PRIVATE_FILE_PREFIX = "/private/files/"


def _visitor_type_of(doctype, doc_or_name):
	"""The Visitor Type of a pass, or of the pass a Security Log belongs to."""
	if doctype == "Visitor Pass":
		if isinstance(doc_or_name, str):
			return frappe.db.get_value("Visitor Pass", doc_or_name, "visitor_type")
		return doc_or_name.get("visitor_type")
	if doctype == "Security Log":
		visitor_pass = (
			frappe.db.get_value("Security Log", doc_or_name, "visitor_pass")
			if isinstance(doc_or_name, str)
			else doc_or_name.get("visitor_pass")
		)
		return frappe.db.get_value("Visitor Pass", visitor_pass, "visitor_type") if visitor_pass else None
	return None


def can_open_id_documents(doc_or_name=None, user=None, doctype=None) -> bool:
	"""May `user` open the ID scan and visa copy of this pass (or gate log)?

	True for Security, System Manager (and Administrator), and for a user who
	holds the approver or secondary approver role of the pass's Visitor Type.
	Having raised the pass, hosting the visitor or having uploaded the file does
	not count. `doc_or_name` is a Visitor Pass or Security Log document, or a
	name (a Visitor Pass unless `doctype` says otherwise); with None the answer
	is for the roles alone.
	"""
	user = user or frappe.session.user
	if user == "Administrator":
		return True
	if not user or user == "Guest":
		return False

	roles = set(frappe.get_roles(user))
	if roles.intersection(ID_DOCUMENT_ROLES):
		return True
	if not doc_or_name:
		return False

	if not isinstance(doc_or_name, str):
		doctype = doc_or_name.doctype
	visitor_type = _visitor_type_of(doctype or "Visitor Pass", doc_or_name)
	if not visitor_type:
		return False
	approvers = frappe.db.get_value(
		"Visitor Type", visitor_type, ["approver_role", "secondary_approver_role"], cache=True
	)
	return bool(approvers and roles.intersection(role for role in approvers if role))


def _is_id_document_row(file_row):
	"""Is this File row an ID document of one of this app's records?

	By the field it was attached through, or — for a row with no field, such as
	the same file added again from the sidebar — because the record's ID
	document field holds this very URL.
	"""
	fields = ID_DOCUMENT_FIELDS.get(file_row.get("attached_to_doctype"))
	if not fields or not file_row.get("attached_to_name"):
		return False
	if file_row.get("attached_to_field") in fields:
		return True
	if not file_row.get("file_url"):
		return False
	stored = frappe.db.get_value(
		file_row.get("attached_to_doctype"), file_row.get("attached_to_name"), list(fields), as_dict=True
	)
	return bool(stored and file_row.get("file_url") in stored.values())


def _may_open_id_document_row(file_row, user):
	doctype, name = file_row.get("attached_to_doctype"), file_row.get("attached_to_name")
	if not frappe.db.exists(doctype, name):
		# Still on an unsaved form ("new-visitor-pass-..."): no visitor record
		# exists yet, and the only person who can reach the file is whoever just
		# uploaded it. Once the record is saved, the rule above applies to them too.
		return file_row.get("owner") == user
	return can_open_id_documents(name, user=user, doctype=doctype)


def _in_audience_of_any(id_rows, user):
	"""`_may_open_id_document_row` for many File rows at once, in a fixed number of queries.

	Identical bytes share one URL, so one scan uploaded for a returning visitor,
	or copied to the gate log, is many File rows. Asking about them one by one
	costs several queries a row on every image request.
	"""
	if not user or user == "Guest":
		return False
	roles = set(frappe.get_roles(user))
	if roles.intersection(ID_DOCUMENT_ROLES):
		return True

	pass_names = {row.attached_to_name for row in id_rows if row.attached_to_doctype == "Visitor Pass"}
	log_names = {row.attached_to_name for row in id_rows if row.attached_to_doctype == "Security Log"}
	log_passes = {}
	if log_names:
		log_passes = dict(
			frappe.get_all(
				"Security Log",
				filters={"name": ("in", list(log_names))},
				fields=["name", "visitor_pass"],
				as_list=True,
			)
		)
	wanted = pass_names | {name for name in log_passes.values() if name}
	pass_types = {}
	if wanted:
		pass_types = dict(
			frappe.get_all(
				"Visitor Pass",
				filters={"name": ("in", list(wanted))},
				fields=["name", "visitor_type"],
				as_list=True,
			)
		)

	visitor_types = set()
	for row in id_rows:
		if row.attached_to_doctype == "Visitor Pass":
			exists, visitor_pass = row.attached_to_name in pass_types, row.attached_to_name
		else:
			exists, visitor_pass = row.attached_to_name in log_passes, log_passes.get(row.attached_to_name)
		if not exists:
			# Still on an unsaved form: its uploader only (see _may_open_id_document_row).
			if row.owner == user:
				return True
			continue
		if pass_types.get(visitor_pass):
			visitor_types.add(pass_types[visitor_pass])
	if not visitor_types:
		return False

	for approvers in frappe.get_all(
		"Visitor Type",
		filters={"name": ("in", list(visitor_types))},
		fields=["approver_role", "secondary_approver_role"],
	):
		if roles.intersection(r for r in (approvers.approver_role, approvers.secondary_approver_role) if r):
			return True
	return False


def has_id_document_file_permission(doc, user=None, ptype=None, debug=False):
	"""`has_permission` hook on File: close ID documents to everyone but their audience.

	Frappe calls the hooks of every app and stops at the first that answers
	(frappe/permissions.py has_controller_permissions); an answer can only deny.
	So this returns False for an ID document the user may not open, and None —
	"no opinion, ask the next one" — in every other case, including every file
	that is not this app's. It never returns True.

	Only reading is decided here. Replacing or removing the attachment stays
	with the record's own write permission, as in core, so a host can still
	correct the scan on a pass they are filling in.
	"""
	if ptype not in ("read", "select", "print", "email", "export", "share", "report"):
		return None
	user = user or frappe.session.user
	if user == "Administrator" or not doc or not cint(doc.get("is_private")):
		return None
	if not _is_id_document_row(doc):
		return None
	return None if _may_open_id_document_row(doc, user) else False


def id_document_access(file_url, user=None):
	"""Whether `user` may be served the private file at `file_url`.

	None when the URL is not an ID document of this app (not this app's
	decision). Otherwise True or False:

	* True when the user belongs to the audience of one of the records that
	  hold it as an ID document;
	* True when the same stored file is also attached to a record of another
	  app, or is someone's own unattached upload, and Frappe's own rule lets the
	  user read that row — identical bytes share one URL, and what another app
	  attached is that app's to govern;
	* False otherwise. A row on one of this app's records that is not an ID
	  document field (the same URL pasted into another pass's photo field, say)
	  does not open an ID document.
	"""
	user = user or frappe.session.user
	rows = frappe.get_all(
		"File",
		filters={"file_url": file_url, "attached_to_doctype": ("in", list(ID_DOCUMENT_FIELDS))},
		fields=["name", "file_url", "owner", "attached_to_doctype", "attached_to_name", "attached_to_field"],
	)
	id_rows = [row for row in rows if _is_id_document_row(row)]
	if not id_rows:
		return None
	if user == "Administrator":
		return True
	if _in_audience_of_any(id_rows, user):
		return True

	from frappe.core.doctype.file.file import has_permission as core_file_permission

	from visitormanagement.visitor_management.link_details import APP_MODULES

	for other in frappe.get_all("File", filters={"file_url": file_url}, fields=["*"]):
		attached_to = other.get("attached_to_doctype")
		if attached_to and frappe.db.get_value("DocType", attached_to, "module", cache=True) in APP_MODULES:
			continue
		if core_file_permission(frappe.get_doc(doctype="File", **other), "read", user=user):
			return True
	return False


def _requested_private_file():
	"""The private file URL this request asks to be served, if any."""
	request = getattr(frappe.local, "request", None)
	if not request:
		return None
	path = request.path or ""
	if path.startswith(_PRIVATE_FILE_PREFIX):
		return path
	# /api/method/download_file and frappe.core.doctype.file.file.download_file
	file_url = (getattr(frappe.local, "form_dict", None) or {}).get("file_url")
	if isinstance(file_url, str) and file_url.startswith(_PRIVATE_FILE_PREFIX):
		return file_url
	return None


def guard_id_document_request():
	"""`auth_hooks` entry: refuse a request for an ID document from outside its audience.

	Runs once per request from frappe.auth.validate_auth, after the session,
	API key or token has been resolved, so the user is final. Every request that
	is not for a private file returns at the first line; one that is costs a
	single indexed File lookup, and is left to Frappe unless the file is an ID
	document of this app. A Guest is left to Frappe too, which refuses every
	private file to guests.
	"""
	file_url = _requested_private_file()
	if not file_url or frappe.session.user in ("Administrator", "Guest"):
		return
	if id_document_access(file_url) is False:
		raise frappe.PermissionError(_("You don't have permission to access this file"))
