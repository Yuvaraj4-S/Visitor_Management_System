import frappe


def run():
    frappe.clear_cache(doctype="Visitor Pass")
    # Simulate what the browser gets via /api/method/frappe.desk.form.load.getdoctype
    from frappe.desk.form.load import get_meta_bundle

    bundle = get_meta_bundle("Visitor Pass")
    vp = bundle[0]
    print("=== Meta served to the browser ===")
    targets = (
        "meal_required", "meal_type", "assigned_meal_slots", "hospitality_type",
        "number_of_people", "special_diet", "refreshments_required",
        "conference_room", "service_time", "food_dept_staff_assigned",
        "food_status", "hospitality_notes", "hospitality_request",
    )
    for f in vp.fields:
        if f.fieldname in targets:
            print(f"  {f.fieldname:30} hidden={f.hidden} depends_on={f.depends_on!r}")
