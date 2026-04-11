import frappe
from frappe import _


# ─────────────────────────────────────────────────────────
# CHECK-IN (via Security Log)
# ─────────────────────────────────────────────────────────
@frappe.whitelist()
def visitor_checkin(docname):
    """Create a Security Log for check-in. Gate officer must complete
    verification (photo, ID match, items) on the Security Log form."""

    doc = frappe.get_doc("Visitor Pass", docname)

    if doc.status not in ("Approved", "Items Verified"):
        frappe.throw(
            _("Pass must be 'Approved' or 'Items Verified' to Check-In. Current status: {0}").format(doc.status)
        )

    sl = frappe.new_doc("Security Log")
    sl.visitor_pass = docname
    sl.event_type = "Check-In"
    sl.gate_name = "Main Gate"
    sl.insert(ignore_permissions=True)

    frappe.msgprint(
        _("Security Log {0} created. Complete gate verification to check in.").format(
            '<a href="/app/security-log/{0}">{0}</a>'.format(sl.name)
        ),
        alert=True,
        indicator="blue",
    )
    return {"security_log": sl.name}


# ─────────────────────────────────────────────────────────
# CHECK-OUT (via Security Log)
# ─────────────────────────────────────────────────────────
@frappe.whitelist()
def visitor_checkout(docname):
    """Create a Security Log for check-out."""

    doc = frappe.get_doc("Visitor Pass", docname)

    if doc.status != "Checked-In":
        frappe.throw(_("Visitor is not currently checked in."))

    sl = frappe.new_doc("Security Log")
    sl.visitor_pass = docname
    sl.event_type = "Check-Out"
    sl.gate_name = "Main Gate"
    sl.insert(ignore_permissions=True)

    frappe.msgprint(
        _("Security Log {0} created for check-out.").format(
            '<a href="/app/security-log/{0}">{0}</a>'.format(sl.name)
        ),
        alert=True,
        indicator="green",
    )
    return {"security_log": sl.name}


# ─────────────────────────────────────────────────────────
# QR SCAN LOGIC
# ─────────────────────────────────────────────────────────
@frappe.whitelist()
def scan_qr_checkin(qr_data):
    """
    Gatekeeper QR Scan Logic.
    Routes to check-in or check-out via Security Log.

    Example QR:
    PASS:VMS-VP-2026-00001|VISITOR:John|VISIT_DATE:2026-06-01
    """

    if not qr_data:
        frappe.throw(_("Invalid QR Data."))

    # Parse QR string into dictionary
    parts = {}
    for p in qr_data.split("|"):
        if ":" in p:
            key, value = p.split(":", 1)
            parts[key.strip()] = value.strip()

    pass_id = parts.get("PASS")
    visitor_name = parts.get("VISITOR")
    visit_date = parts.get("VISIT_DATE")

    if not pass_id and (not visitor_name or not visit_date):
        frappe.throw(_("QR Code missing required information (PASS or VISITOR+DATE)."))

    # Find matching Visitor Pass
    if pass_id:
        doc_name = frappe.db.exists("Visitor Pass", pass_id)
    else:
        doc_name = frappe.db.get_value(
            "Visitor Pass",
            {
                "visitor_full_name": visitor_name,
                "visit_date": visit_date,
                "status": ["in", ["Approved", "Items Verified"]],
            },
            "name",
        )

    if not doc_name:
        frappe.throw(_("No valid Visitor Pass found for this QR code."))

    # Route based on current status
    doc_status = frappe.db.get_value("Visitor Pass", doc_name, "status")

    if doc_status in ("Approved", "Items Verified"):
        return visitor_checkin(doc_name)
    elif doc_status == "Checked-In":
        return visitor_checkout(doc_name)
    elif doc_status == "Checked-Out":
        frappe.throw(
            _("Visitor Pass {0} has already been used (Checked-Out). This QR code is now inactive.").format(doc_name)
        )
    else:
        frappe.throw(
            _("Visitor Pass {0} is in '{1}' state. Expected 'Approved', 'Items Verified', or 'Checked-In'.").format(
                doc_name, doc_status
            )
        )
