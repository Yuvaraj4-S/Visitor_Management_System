import frappe
from frappe.utils import now_datetime


# ─────────────────────────────────────────────────────────
# CHECK-IN
# ─────────────────────────────────────────────────────────
@frappe.whitelist()
def visitor_checkin(docname):
    """Metro-style check-in: only valid from 'Items Verified' status."""

    doc = frappe.get_doc("Visitor Pass", docname)

    if doc.status != "Items Verified":
        frappe.throw("Items must be verified by gate security before Check-In.")

    doc.db_set("status", "Checked-In")
    doc.db_set("actual_checkin", now_datetime())

    # Notify host employee
    host_email = frappe.db.get_value(
        "Employee", doc.person_to_visit, "company_email"
    )

    if host_email:
        frappe.sendmail(
            recipients=[host_email],
            subject=f"Your Visitor Arrived: {doc.visitor_full_name}",
            message=(
                f"<b>{doc.visitor_full_name}</b> has arrived at the gate.<br>"
                f"Badge: {doc.badge_number}<br>"
                f"Purpose: {doc.purpose_of_visit}"
            ),
        )

    frappe.msgprint("Checked In! Host notified.", alert=True)
    return "ok"


# ─────────────────────────────────────────────────────────
# CHECK-OUT
# ─────────────────────────────────────────────────────────
@frappe.whitelist()
def visitor_checkout(docname):
    """Metro-style check-out: only valid from 'Checked-In' status."""

    doc = frappe.get_doc("Visitor Pass", docname)

    if doc.status != "Checked-In":
        frappe.throw("Visitor is not currently checked in.")

    doc.db_set("status", "Checked-Out")
    doc.db_set("actual_checkout", now_datetime())

    frappe.msgprint("Checked Out successfully!", alert=True)
    return "ok"


# ─────────────────────────────────────────────────────────
# QR SCAN LOGIC
# ─────────────────────────────────────────────────────────
@frappe.whitelist()
def scan_qr_checkin(qr_data):
    """
    Gatekeeper QR Scan Logic.
    Called when QR code is scanned.

    Example QR:
    PASS:VMS-VP-2026-00001|VISITOR:John|VISIT_DATE:2026-06-01
    """

    try:
        if not qr_data:
            frappe.throw("Invalid QR Data.")

        # Convert QR string into dictionary safely
        parts = {}
        for p in qr_data.split("|"):
            if ":" in p:
                key, value = p.split(":", 1)
                parts[key.strip()] = value.strip()

        pass_id = parts.get("PASS")
        visitor_name = parts.get("VISITOR")
        visit_date = parts.get("VISIT_DATE")

        if not pass_id and (not visitor_name or not visit_date):
            frappe.throw("QR Code missing required information (PASS or VISITOR+DATE).")

        # Find matching Visitor Pass
        if pass_id:
            doc_name = frappe.db.exists("Visitor Pass", pass_id)
        else:
            doc_name = frappe.db.get_value(
                "Visitor Pass",
                {
                    "visitor_full_name": visitor_name,
                    "visit_date": visit_date,
                    "status": "Items Verified",
                },
                "name",
            )

        if not doc_name:
            frappe.throw(
                "No valid Visitor Pass found for this QR code."
            )

        # Re-check status if found by name, or check it for the first time if found by ID
        doc_status = frappe.db.get_value("Visitor Pass", doc_name, "status")
        if doc_status != "Items Verified":
             frappe.throw(
                f"Visitor Pass {doc_name} is in '{doc_status}' state. "
                "Must be 'Items Verified' to Check-In."
            )

        # Perform check-in
        return visitor_checkin(doc_name)

    except Exception as e:
        frappe.throw(f"QR Scan Error: {str(e)}")