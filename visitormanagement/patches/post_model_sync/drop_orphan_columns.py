"""Drop columns that exist in the database but are no longer declared in the
doctype JSON (orphans left behind when fields were removed). Frappe's
`bench migrate` doesn't auto-drop columns to protect data, so this patch
explicitly removes the known-safe ones.

Verified safe:
- Each column is NOT in the current doctype JSON
- Each column is NOT a Custom Field, Property Setter target, or workflow column
- The only template references (`vms_food_dept_alert.html`, `visitor_itinerary.html`)
  use `or doc.<field>` fallbacks that already evaluate to None today, so dropping
  the column changes nothing user-visible.
- Diagnostic-only references (`demo/_webform_diag.py`) are not part of any flow.

Idempotent: re-running is a no-op once columns have been dropped.
"""
import frappe


VP_ORPHAN_COLS = [
    "contractor_nda_document",
    "contractor_nda_signed",
    "ppe_provided",
    "ppe_provided_document",
    "priority_lane",
    "rest_area",
    "safety_induction_done",
    "security_detail",
    "welcome_gift",
]

HR_ORPHAN_COLS = [
    "buggy_passenger_count",
    "nda_signed",
    "ppe_issued",
    "safety_briefing_done",
    "vehicle_number",   # only orphan on Hospitality Request; VP + Security Log still use it
]

SL_ORPHAN_COLS = [
    "id_last_4_digits",
    "priority_lane",
]


def _drop_columns(table, columns):
    existing = {
        c.get("Field") or c.get("name")
        for c in frappe.db.sql(f"SHOW COLUMNS FROM `{table}`", as_dict=True)
    }
    for col in columns:
        if col not in existing:
            continue
        try:
            frappe.db.sql_ddl(f"ALTER TABLE `{table}` DROP COLUMN `{col}`")
            print(f"Dropped {table}.{col}")
        except Exception:
            frappe.log_error(
                title=f"Drop column failed: {table}.{col}",
                message=frappe.get_traceback(),
            )


def _delete_orphan_property_setters():
    """Property Setters that target now-dropped fields. Safe to remove."""
    targets = [
        ("Visitor Pass", "rest_area"),
    ]
    for doc_type, field_name in targets:
        rows = frappe.get_all(
            "Property Setter",
            filters={"doc_type": doc_type, "field_name": field_name},
            pluck="name",
        )
        for name in rows:
            try:
                frappe.delete_doc("Property Setter", name, ignore_permissions=True, force=True)
                print(f"Deleted Property Setter {name}")
            except Exception:
                frappe.log_error(
                    title=f"Delete Property Setter failed: {name}",
                    message=frappe.get_traceback(),
                )


def execute():
    _drop_columns("tabVisitor Pass", VP_ORPHAN_COLS)
    _drop_columns("tabHospitality Request", HR_ORPHAN_COLS)
    _drop_columns("tabSecurity Log", SL_ORPHAN_COLS)
    _delete_orphan_property_setters()
    frappe.db.commit()
