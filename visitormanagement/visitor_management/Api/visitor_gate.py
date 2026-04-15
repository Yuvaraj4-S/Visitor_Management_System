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

    # Fetch status + visit_date for early validation
    vp_info = frappe.db.get_value(
        "Visitor Pass", doc_name,
        ["status", "visit_date", "id_proof_number"],
        as_dict=True,
    )
    doc_status = vp_info.status

    # Visit date must match today (or checkout allowed for already Checked-In)
    from frappe.utils import getdate, today as _today
    if vp_info.visit_date and doc_status != "Checked-In":
        if getdate(vp_info.visit_date) != getdate(_today()):
            frappe.throw(
                _("Visitor Pass {0} is for {1}, not today. QR code not valid for this date.").format(
                    doc_name, vp_info.visit_date
                )
            )

    # Re-check blacklist at the gate — person may have been blacklisted since pass approval
    if vp_info.id_proof_number:
        blocked = frappe.db.exists(
            "Visitor Blacklist",
            {"id_proof_number": vp_info.id_proof_number, "is_active": 1},
        )
        if blocked:
            frappe.throw(
                _("ACCESS DENIED: Visitor Pass {0} matches an active blacklist entry.").format(doc_name),
                title=_("Blacklisted"),
            )

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
