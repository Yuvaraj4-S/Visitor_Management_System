import frappe
from frappe import _


def _assert_gate_permission():
    """Only staff allowed to create Security Logs (Security / System Manager)
    may operate the gate.

    These endpoints are @frappe.whitelist() (login required, NOT guest), but
    that alone only proves the caller is authenticated — it does not enforce a
    role. Without this check, any logged-in user (e.g. a plain Employee, who has
    no `create` permission on Security Log) could forge entry/exit logs and
    poison the audit trail.
    """
    if not frappe.has_permission("Security Log", "create"):
        frappe.throw(
            _("You are not permitted to record gate entry/exit. Security role required."),
            frappe.PermissionError,
        )


# ─────────────────────────────────────────────────────────
# CHECK-IN (via Security Log)
# ─────────────────────────────────────────────────────────
@frappe.whitelist()
def visitor_checkin(docname):
    """Create a Security Log for check-in. Gate officer must complete
    verification (photo, ID match, items) on the Security Log form."""

    _assert_gate_permission()

    doc = frappe.get_doc("Visitor Pass", docname)

    from visitormanagement.visitor_management.utils import checked_in_on, in_recurring_window

    recurring_reentry = doc.status == "Checked-Out" and in_recurring_window(doc) and not checked_in_on(doc.name)
    if doc.status not in ("Approved", "Items Verified") and not recurring_reentry:
        frappe.throw(
            _("Pass must be 'Approved' or 'Items Verified' to Check-In. Current status: {0}").format(doc.status)
        )

    sl = frappe.new_doc("Security Log")
    sl.visitor_pass = docname
    sl.event_type = "Check-In"
    sl.gate_name = "Main Gate"
    sl.insert()

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

    _assert_gate_permission()

    doc = frappe.get_doc("Visitor Pass", docname)

    if doc.status != "Checked-In":
        frappe.throw(_("Visitor is not currently checked in."))

    sl = frappe.new_doc("Security Log")
    sl.visitor_pass = docname
    sl.event_type = "Check-Out"
    sl.gate_name = "Main Gate"
    sl.insert()

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

    _assert_gate_permission()

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
        ["name", "status", "visit_date", "id_proof_number", "visitor_full_name", "id_proof_type",
         "is_recurring", "recurring_start_date", "recurring_end_date"],
        as_dict=True,
    )
    doc_status = vp_info.status

    # Recurring pass: valid on any day of its window; a checked-out pass re-enters once per day
    from visitormanagement.visitor_management.utils import checked_in_on, in_recurring_window, recurring_window
    if recurring_window(vp_info):
        if doc_status != "Checked-In" and not in_recurring_window(vp_info):
            start, end = recurring_window(vp_info)
            frappe.throw(
                _("Visitor Pass {0} is valid from {1} to {2}, not today.").format(doc_name, start, end)
            )
        if doc_status == "Checked-Out":
            if checked_in_on(doc_name):
                frappe.throw(_("Visitor Pass {0} has already been used today.").format(doc_name))
            doc_status = "Items Verified"  # route to check-in; Security Log re-validates

    # Visit date must match today (or checkout allowed for already Checked-In)
    from frappe.utils import getdate, today as _today
    if vp_info.visit_date and doc_status != "Checked-In" and not recurring_window(vp_info):
        if getdate(vp_info.visit_date) != getdate(_today()):
            frappe.throw(
                _("Visitor Pass {0} is for {1}, not today. QR code not valid for this date.").format(
                    doc_name, vp_info.visit_date
                )
            )

    if doc_status in ("Approved", "Items Verified"):
        # Re-check blacklist only at entry — a Checked-In blacklisted visitor must still
        # be allowed to check out. Match by ID number first, fall back to name + ID type.
        from visitormanagement.visitor_management.doctype.visitor_blacklist.visitor_blacklist import VisitorBlacklist
        blocked = VisitorBlacklist.find_active_match(
            id_proof_number=vp_info.id_proof_number,
            visitor_name=vp_info.visitor_full_name,
            id_proof_type=vp_info.id_proof_type,
        )
        if blocked:
            frappe.throw(
                _("ACCESS DENIED: Visitor Pass {0} matches an active blacklist entry.").format(doc_name),
                title=_("Blacklisted"),
            )
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
