import frappe

# VMS Manager replaces Hospitality Manager; VMS User replaces the coordinator roles
# (the VMS Manager assigns each Hospitality Request to a VMS User for data entry).
MANAGER_FROM = ("Hospitality Manager",)
USER_FROM = (
	"Hospitality User",
	"Transport Coordinator",
	"Front Office Executive",
	"Factory Tour Coordinator",
	"Greeting Staff",
)


def execute():
	for role in ("VMS Manager", "VMS User"):
		if not frappe.db.exists("Role", role):
			frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(ignore_permissions=True)

	_grant("VMS Manager", MANAGER_FROM)
	_grant("VMS User", USER_FROM)

	# Notifications that still mail the old roles now go to the VMS Manager.
	frappe.db.sql(
		"""update `tabNotification Recipient` set receiver_by_role = 'VMS Manager'
		where receiver_by_role in %(roles)s""",
		{"roles": MANAGER_FROM + USER_FROM},
	)


def _grant(new_role, old_roles):
	"""Give ``new_role`` to every enabled user holding one of ``old_roles``. Old roles are
	left in place (not removed) so nothing else that still uses them breaks."""
	users = frappe.get_all(
		"Has Role", filters={"role": ["in", old_roles], "parenttype": "User"}, pluck="parent", distinct=True
	)
	for name in users:
		if name in ("Administrator", "Guest") or not frappe.db.exists("User", name):
			continue
		user = frappe.get_doc("User", name)
		if new_role not in {r.role for r in user.roles}:
			user.append("roles", {"role": new_role})
			user.flags.ignore_permissions = True
			user.save()
