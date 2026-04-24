import frappe
from frappe.permissions import add_permission, update_permission_property


def execute():
    roles = [
        "Security",
        "Hospitality Manager",
        "Host Employee",
        "Facility Manager",
    ]

    for role_name in roles:
        if not frappe.db.exists("Role", role_name):
            role = frappe.get_doc({"doctype": "Role", "role_name": role_name})
            role.insert(ignore_permissions=True)

    # VMS roles that need read access to Employee (for fetched fields like
    # person_to_visit on Visitor Pass / Security Log link widgets).
    employee_readers = [
        "Security",
        "Hospitality Manager",
        "Host Employee",
        "Facility Manager",
        "HOD",
        "HR Manager",
        "Sales Manager",
        "CEO",
    ]
    for role_name in employee_readers:
        if not frappe.db.exists("Role", role_name):
            continue
        existing = frappe.db.exists(
            "Custom DocPerm",
            {"parent": "Employee", "role": role_name, "permlevel": 0},
        )
        if not existing:
            add_permission("Employee", role_name, 0)
        update_permission_property("Employee", role_name, 0, "read", 1)

    _trim_security_role_scope()
    _provision_employee_for_security_users()


def _trim_security_role_scope():
    """Gate security only needs Security Log + what's required to fill it.
    Strip stray Custom DocPerms that leaked from prior setups.
    """
    allowed_custom_perms = {"Employee", "Page"}
    stray = frappe.get_all(
        "Custom DocPerm",
        filters={"role": "Security"},
        fields=["name", "parent"],
    )
    for row in stray:
        if row.parent not in allowed_custom_perms:
            frappe.delete_doc("Custom DocPerm", row.name, ignore_permissions=True, force=True)


def _provision_employee_for_security_users():
    """Every Security user must have an Employee record — without it, the
    `security_officer` self-filter breaks and ERPNext auto-strips the Employee
    role on next User save. Auto-create a minimal Employee so the flow works
    out of the box for any newly-hired security staff.
    """
    default_company = (
        frappe.defaults.get_global_default("company")
        or frappe.db.get_value("Company", {}, "name")
    )
    if not default_company:
        return

    designation = "Security Engineer" if frappe.db.exists("Designation", "Security Engineer") else None

    security_users = frappe.get_all(
        "Has Role",
        filters={"role": "Security", "parenttype": "User"},
        fields=["parent as user"],
    )

    for row in security_users:
        user_name = row.user
        if user_name in ("Administrator", "Guest"):
            continue
        if not frappe.db.exists("User", user_name):
            continue
        user_doc = frappe.get_doc("User", user_name)
        if not user_doc.enabled:
            continue

        emp_name = frappe.db.get_value("Employee", {"user_id": user_name}, "name")
        if not emp_name:
            first_name = user_doc.first_name or (user_doc.full_name or "").split(" ")[0] or user_name.split("@")[0]
            last_name = user_doc.last_name or ""
            emp = frappe.get_doc({
                "doctype": "Employee",
                "employee_name": user_doc.full_name or first_name,
                "first_name": first_name,
                "last_name": last_name,
                "company": default_company,
                "status": "Active",
                "gender": "Prefer not to say",
                "date_of_birth": "1990-01-01",
                "date_of_joining": frappe.utils.today(),
                "designation": designation,
                "user_id": user_name,
                "personal_email": user_name if "@" in user_name else None,
            })
            emp.flags.ignore_permissions = True
            emp.flags.ignore_mandatory = True
            try:
                emp.insert()
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    f"VMS: auto-provision Employee failed for Security user {user_name}",
                )
                continue

        # ERPNext usually auto-adds Employee role on Employee.user_id assignment,
        # but re-check in case the User was saved without the hook firing.
        if not frappe.db.exists("Has Role", {"parent": user_name, "role": "Employee"}):
            user_doc.append("roles", {"role": "Employee"})
            user_doc.flags.ignore_permissions = True
            try:
                user_doc.save()
            except Exception:
                frappe.log_error(
                    frappe.get_traceback(),
                    f"VMS: could not add Employee role to {user_name}",
                )

        # ERPNext's Employee.update_user_permissions() auto-creates a global
        # User Permission (Employee=<self>, applicable_for=None) that would
        # restrict Alan to only Security Logs linked to his own Employee record.
        # Gate security must process EVERY visitor, so strip the Employee-level
        # User Permission. Keep the Company-level one so they stay scoped to
        # their own company's data.
        frappe.db.delete(
            "User Permission",
            {"user": user_name, "allow": "Employee"},
        )
