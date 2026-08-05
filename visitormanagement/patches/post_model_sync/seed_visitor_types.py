import frappe


ROLES = [
	"HOD",
	"CEO",
]

VISITOR_TYPES = [
	{
		"visitor_type_name": "Contractor",
		"approver_role": "System Manager",
		"secondary_approver_role": None,
		"badge_prefix": "CON",
		"badge_colour": "Orange",
		"default_gate": "Back Gate",
		"is_active": 1,
	},
	{
		"visitor_type_name": "Candidate",
		"approver_role": "HR Manager",
		"secondary_approver_role": None,
		"badge_prefix": "CAN",
		"badge_colour": "Purple",
		"default_gate": "Main Gate",
		"is_active": 1,
	},
	{
		"visitor_type_name": "Customer",
		"approver_role": "Sales Manager",
		"secondary_approver_role": None,
		"badge_prefix": "CUS",
		"badge_colour": "Green",
		"default_gate": "Main Gate",
		"is_active": 1,
	},
	{
		"visitor_type_name": "Supplier",
		"approver_role": "System Manager",
		"secondary_approver_role": None,
		"badge_prefix": "SUP",
		"badge_colour": "Teal",
		"default_gate": "Loading Dock",
		"is_active": 1,
	},
	{
		"visitor_type_name": "VIP",
		"approver_role": "HOD",
		"secondary_approver_role": "CEO",
		"badge_prefix": "VIP",
		"badge_colour": "Gold",
		"default_gate": "VIP Entrance",
		"is_active": 1,
	},
]


def execute():
	for role_name in ROLES:
		if not frappe.db.exists("Role", role_name):
			frappe.get_doc({"doctype": "Role", "role_name": role_name}).insert(ignore_permissions=True)

	for visitor_type in VISITOR_TYPES:
		if frappe.db.exists("Visitor Type", visitor_type["visitor_type_name"]):
			continue
		frappe.get_doc({"doctype": "Visitor Type", **visitor_type}).insert(ignore_permissions=True)
