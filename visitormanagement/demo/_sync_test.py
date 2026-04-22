import frappe


def run():
    vp = frappe.new_doc("Visitor Pass")
    vp.update({
        "visitor_type": "Customer",
        "visitor_full_name": "Test Sync A",
        "mobile_number": "+91 9999999981",
        "email_id": "tsync_a@example.com",
        "id_proof_type": "Aadhaar",
        "id_proof_number": "7" + str(abs(hash(frappe.generate_hash(length=8))))[:11].ljust(11, "0"),
        "id_proof_scan": "/files/vms_demo_placeholder.png",
        "visitor_photo": "/files/vms_demo_placeholder.png",
        "purpose_of_visit": "sync test",
        "person_to_visit": frappe.db.get_value("Employee", {}, "name"),
        "visit_date": "2026-05-10",
        "expected_checkin": "10:00:00",
        "expected_checkout": "13:00:00",
    })
    vp.append("visitor_items", {"item_name": "Dell laptop, USB drive, toolkit", "quantity": 1})
    vp.flags.ignore_permissions = True
    vp.insert()
    frappe.db.commit()

    print(f"[A] items_carried={vp.items_carried!r}")
    print(f"[A] vi0.item_name={vp.visitor_items[0].item_name!r}")
    print(f"[A] status={vp.status} workflow_state={vp.workflow_state} docstatus={vp.docstatus}")

    vp.items_carried = "Laptop, phone, car keys"
    vp.save()
    frappe.db.commit()

    # Reload from DB to verify persistence
    vp.reload()
    print(f"[B] items_carried={vp.items_carried!r}")
    print(f"[B] vi0.item_name={vp.visitor_items[0].item_name!r}")
    print(f"[B] visitor_items count={len(vp.visitor_items)}")

    # Cleanup
    frappe.delete_doc("Visitor Pass", vp.name, ignore_permissions=True)
    frappe.db.commit()
    print("[cleanup] done")
