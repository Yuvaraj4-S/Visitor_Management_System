"""End-to-end probe: invitation → portal submit → Visitor Pass.

Run via:
    bench --site vms.local execute visitormanagement._e2e_invitation_check.run

Confirms every host-set field on the Visitor Invitation propagates to the
resulting Visitor Pass, plus the guest's portal entries.
"""
import json
import secrets

import frappe
from frappe.utils import add_days, nowdate

from visitormanagement.visitor_management import portal


_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def run():
    host = frappe.db.get_value("Employee", {"status": "Active"}, "name")
    room = frappe.db.get_value("Conference Room", {"is_active": 1}, "name")
    if not host or not room:
        return f"need active Employee + Conference Room (host={host}, room={room})"

    visitor_email = f"e2e.test.{secrets.token_hex(3)}@example.com"
    inv = frappe.new_doc("Visitor Invitation")
    inv.update({
        "visitor_type":          "Customer",
        "visitor_email":         visitor_email,
        "host_employee":         host,
        "visit_date":            add_days(nowdate(), 7),
        "expected_checkin":      "10:00:00",
        "expected_checkout":     "12:00:00",
        "purpose_of_visit":      "E2E test — product demo",
        "meal_required":         1,
        "refreshments_required": 1,
        "conference_room":       room,
    })
    inv.insert(ignore_permissions=True)

    # Simulate "send" by stamping a token without firing the email infra.
    token = secrets.token_urlsafe(24)
    inv.db_set({"invitation_token": token, "invitation_status": "Sent"}, update_modified=False)
    inv.reload()

    data_url = f"data:image/png;base64,{_TINY_PNG_B64}"
    payload = {
        "invitation_token":      token,
        "submission_action":     "submit",
        "visitor_full_name":     "E2E Test Visitor",
        "mobile_number":         "9876543210",
        "company__organisation": "TechSpark Industries",
        "id_proof_type":         "PAN Card",
        "id_proof_number":       "AABCD1234E",
        "id_proof_scan":         data_url,
        "visitor_photo":         data_url,
        "vehicle_number":        "TN-99-AB-1234",
        "visit_category":        "Product Demo",
        "visitor_items":         [{"item_name": "Laptop", "quantity": 1}],
    }

    result = portal.submit_pre_registration(payload=json.dumps(payload))
    frappe.db.commit()
    vp_name = result["name"]
    vp = frappe.get_doc("Visitor Pass", vp_name)

    expectations = [
        # host-set fields, must come from invitation
        ("visitor_type",          inv.visitor_type),
        ("email_id",               inv.visitor_email),
        ("visit_date",             str(inv.visit_date)),
        ("person_to_visit",        inv.host_employee),
        ("purpose_of_visit",       inv.purpose_of_visit),
        ("meal_required",          int(inv.meal_required or 0)),
        ("refreshments_required",  int(inv.refreshments_required or 0)),
        ("conference_room",        inv.conference_room),
        # guest-entered fields, must come from form payload
        ("visitor_full_name",      payload["visitor_full_name"]),
        ("company__organisation",  payload["company__organisation"]),
        ("id_proof_number",        payload["id_proof_number"]),
        ("vehicle_number",         payload["vehicle_number"]),
        ("visit_category",         payload["visit_category"]),
    ]

    lines = ["", "FIELD                       EXPECTED                  GOT                       OK?"]
    fail = 0
    for field, expected in expectations:
        got = vp.get(field)
        # Normalise ints
        if isinstance(expected, int):
            ok = (int(got or 0) == expected)
        else:
            ok = (str(got) == str(expected))
        lines.append(f"  {field:25s} {str(expected)[:25]:25s} {str(got)[:25]:25s} {'OK' if ok else 'MISS'}")
        if not ok:
            fail += 1

    items = [r.item_name for r in (vp.get("visitor_items") or [])]
    items_ok = "Laptop" in items
    lines.append(f"  visitor_items             ['Laptop']                {items}                  {'OK' if items_ok else 'MISS'}")
    if not items_ok:
        fail += 1

    # Cleanup
    frappe.delete_doc("Visitor Pass", vp_name, force=True, ignore_permissions=True)
    frappe.delete_doc("Visitor Invitation", inv.name, force=True, ignore_permissions=True)
    frappe.db.commit()

    summary = f"\nE2E RESULT: {len(expectations) + 1 - fail}/{len(expectations) + 1} field checks passed; {fail} mismatches"
    return "\n".join(lines) + summary
