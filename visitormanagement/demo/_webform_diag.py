import frappe


def run():
    wf = frappe.get_doc("Web Form", "visitor-pre-registration-form")
    removed = {
        "products_discussed", "meeting_outcome", "followup_date",
        "safety_induction_done", "contractor_nda_signed", "ppe_provided",
        "priority_lane", "mdceo_notified",
    }
    total = len(wf.web_form_fields)
    hits = [f.fieldname for f in wf.web_form_fields if f.fieldname in removed]
    has_vehicle = any(f.fieldname == "vehicle_number" for f in wf.web_form_fields)
    print(f"Web Form has {total} fields")
    print(f"Removed-field hits (should be empty): {hits}")
    print(f"vehicle_number present: {has_vehicle}")
    print()
    print("Section breaks in order:")
    for f in wf.web_form_fields:
        if f.fieldtype == "Section Break":
            print(f"  - {f.label!r}  hidden={f.hidden}")
