from contextlib import contextmanager

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime, today, getdate, get_time
from visitormanagement.visitor_management.time_utils import (
    format_time_hhmm,
    strip_expected_time_seconds,
    strip_seconds,
)
from visitormanagement.visitor_management.validators import (
    id_proof_error_message,
    validate_id,
)


class WalkInVisitorRequest(Document):

    def validate(self):
        strip_expected_time_seconds(self)
        self._validate_schedule()
        self._validate_id_proof()
        self._validate_additional_visitors()
        self._validate_host_active()
        self._validate_mobile()
        self._set_host_department()

    def after_insert(self):
        self._notify_host_for_approval()

    def _validate_schedule(self):
        if not self.visit_date:
            return
        visit_date = getdate(self.visit_date)
        if visit_date < getdate(today()):
            frappe.throw(
                _("Visit date {0} is in the past.").format(self.visit_date),
                title=_("Invalid Visit Date"),
            )
        if self.expected_checkin and self.expected_checkout:
            if get_time(self.expected_checkin) >= get_time(self.expected_checkout):
                frappe.throw(
                    _("Expected Check-In must be before Expected Check-Out."),
                    title=_("Invalid Time Range"),
                )

    def _validate_id_proof(self):
        if self.id_proof_type and self.id_proof_number:
            if not validate_id(self.id_proof_type, self.id_proof_number):
                frappe.throw(
                    _(id_proof_error_message(self.id_proof_type)),
                    title=_("Invalid ID Proof"),
                )

    def _validate_additional_visitors(self):
        """Each extra person gets their own pass; ID proof stays optional."""
        for row in self.additional_visitors or []:
            row.visitor_full_name = (row.visitor_full_name or "").strip()
            # Each person may have their own times; empty means "same as the request".
            row.expected_checkin = strip_seconds(row.expected_checkin) if row.expected_checkin else None
            row.expected_checkout = strip_seconds(row.expected_checkout) if row.expected_checkout else None
            checkin = row.expected_checkin or self.expected_checkin
            checkout = row.expected_checkout or self.expected_checkout
            if checkin and checkout and get_time(checkin) >= get_time(checkout):
                frappe.throw(
                    _("Additional visitor row {0} ({1}): Expected Check-In must be before Expected Check-Out.").format(
                        row.idx, row.visitor_full_name
                    ),
                    title=_("Invalid Time Range"),
                )
            row.id_proof_number = (row.id_proof_number or "").strip()
            if row.id_proof_type and row.id_proof_number and not validate_id(row.id_proof_type, row.id_proof_number):
                frappe.throw(
                    _("Additional visitor row {0} ({1}): {2}").format(
                        row.idx, row.visitor_full_name, _(id_proof_error_message(row.id_proof_type))
                    ),
                    title=_("Invalid ID Proof"),
                )
        # The head count can never be lower than the named visitors.
        named = 1 + len(self.additional_visitors or [])
        if (self.number_of_visitors or 0) < named:
            self.number_of_visitors = named

    def _validate_host_active(self):
        if not self.person_to_visit:
            return
        status = frappe.db.get_value("Employee", self.person_to_visit, "status")
        if status != "Active":
            frappe.throw(
                _("Host {0} is not an Active employee.").format(self.person_to_visit),
                title=_("Invalid Host"),
            )

    def _validate_mobile(self):
        if not self.mobile_number:
            return
        raw = str(self.mobile_number).strip()
        digits = "".join(c for c in raw if c.isdigit())
        if not digits:
            return
        if digits.startswith("91") and len(digits) > 10:
            digits = digits[2:]
        if len(digits) >= 10:
            self.mobile_number = f"+91-{digits[-10:]}"

    def _set_host_department(self):
        if self.person_to_visit and not self.host_department:
            self.host_department = frappe.db.get_value(
                "Employee", self.person_to_visit, "department"
            )

    def _notify_host_for_approval(self):
        """Send approval notification to the host employee (person_to_visit)."""
        if not self.person_to_visit:
            return

        host_user = frappe.db.get_value("Employee", self.person_to_visit, "user_id")
        if not host_user:
            frappe.log_error(
                f"No user linked to Employee {self.person_to_visit} — cannot send walk-in approval notification.",
                "WVR Notification",
            )
            return

        host_name = frappe.db.get_value("Employee", self.person_to_visit, "employee_name")
        link = frappe.utils.get_url_to_form("Walk In Visitor Request", self.name)

        frappe.sendmail(
            recipients=[host_user],
            subject=_("Walk-In Visitor Approval Required: {0}").format(self.visitor_full_name),
            message=(
                f"<p>Dear {host_name},</p>"
                f"<p>A walk-in visitor is at the gate and requires your approval:</p>"
                f"<ul>"
                f"<li><b>Visitor:</b> {self.visitor_full_name}</li>"
                + (
                    f"<li><b>Also visiting:</b> {', '.join(_guest_label(self, r) for r in self.additional_visitors)}"
                    f" — each gets their own pass when you approve</li>"
                    if self.additional_visitors
                    else ""
                )
                + f"<li><b>Total visitors:</b> {self.number_of_visitors or 1}</li>"
                f"<li><b>Type:</b> {self.visitor_type}</li>"
                f"<li><b>Purpose:</b> {self.purpose_of_visit or '-'}</li>"
                f"<li><b>Mobile:</b> {self.mobile_number or '-'}</li>"
                f"<li><b>Date:</b> {self.visit_date}</li>"
                f"<li><b>Time:</b> {format_time_hhmm(self.expected_checkin)} - {format_time_hhmm(self.expected_checkout)}</li>"
                f"</ul>"
                f"<p><a href='{link}'>Click here to Approve or Reject</a></p>"
            ),
        )

        # Also create a ToDo for the host so it appears in their desk
        frappe.get_doc({
            "doctype": "ToDo",
            "allocated_to": host_user,
            "reference_type": "Walk In Visitor Request",
            "reference_name": self.name,
            "description": _("Approve or reject walk-in visitor request from {0}").format(
                self.visitor_full_name
            ),
            "priority": "High",
        }).insert(ignore_permissions=True)

        # System notification (bell icon)
        from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification
        enqueue_create_notification(
            [host_user],
            {
                "type": "Alert",
                "document_type": "Walk In Visitor Request",
                "document_name": self.name,
                "subject": _("Walk-in visitor {0} is at the gate and needs your approval").format(
                    frappe.bold(self.visitor_full_name)
                ),
                "from_user": frappe.session.user,
            },
        )


def _guest_label(request_doc, row):
    """Name, plus their own time window when it differs from the request's."""
    checkin = row.expected_checkin or request_doc.expected_checkin
    checkout = row.expected_checkout or request_doc.expected_checkout
    if (row.expected_checkin or row.expected_checkout) and (checkin, checkout) != (
        request_doc.expected_checkin,
        request_doc.expected_checkout,
    ):
        return f"{row.visitor_full_name} ({format_time_hhmm(checkin)} - {format_time_hhmm(checkout)})"
    return row.visitor_full_name


@frappe.whitelist()
def approve_request(request_name):
    """Host approves the walk-in request. Auto-creates a Visitor Pass."""
    doc = frappe.get_doc("Walk In Visitor Request", request_name)

    if doc.status != "Pending Approval":
        frappe.throw(_("This request is already {0}.").format(doc.status))

    # Verify the current user is the host or has System Manager role
    _check_approval_permission(doc)

    # Check blacklist before approval — for the lead visitor and every additional one
    from visitormanagement.visitor_management.doctype.visitor_blacklist.visitor_blacklist import VisitorBlacklist
    people = [doc] + list(doc.additional_visitors or [])
    for person in people:
        blacklist_match = VisitorBlacklist.find_active_match(
            id_proof_number=person.id_proof_number,
            visitor_name=person.visitor_full_name,
            id_proof_type=person.id_proof_type,
        )
        if blacklist_match:
            bl = frappe.get_doc("Visitor Blacklist", blacklist_match)
            frappe.throw(
                _("Visitor {0} is on the active blacklist. Reason: {1}").format(
                    person.visitor_full_name, bl.reason or "Not specified"
                ),
                title=_("Access Denied - Blacklisted Visitor"),
            )

    # Update the request status
    doc.status = "Approved"
    doc.approved_by = frappe.session.user
    doc.approval_date = now_datetime()
    doc.save(ignore_permissions=True)

    # Auto-create one Visitor Pass per person — lead visitor first
    extra_rows = list(doc.additional_visitors or [])
    with _visitor_pass_workflow_paused():
        visitor_pass = _create_visitor_pass_from_request(doc)
        extra_passes = []
        for row in extra_rows:
            vp = _create_visitor_pass_from_request(doc, visitor=row)
            frappe.db.set_value("Walk In Additional Visitor", row.name, "visitor_pass", vp.name, update_modified=False)
            extra_passes.append(vp.name)

    # Link the lead pass back to the request
    doc.db_set("visitor_pass", visitor_pass.name, update_modified=False)

    # Close the ToDo
    _close_todo(doc.name)

    all_passes = [visitor_pass.name] + extra_passes
    frappe.msgprint(
        _("Request approved. Visitor Pass {0} has been created.").format(
            ", ".join(frappe.bold(p) for p in all_passes)
        ),
        title=_("Approved"),
        indicator="green",
        alert=True,
    )

    return {"visitor_pass": visitor_pass.name, "visitor_passes": all_passes}


@frappe.whitelist()
def reject_request(request_name, rejection_reason=None):
    """Host rejects the walk-in request."""
    doc = frappe.get_doc("Walk In Visitor Request", request_name)

    if doc.status != "Pending Approval":
        frappe.throw(_("This request is already {0}.").format(doc.status))

    _check_approval_permission(doc)

    doc.status = "Rejected"
    doc.rejection_reason = rejection_reason
    doc.save(ignore_permissions=True)

    _close_todo(doc.name)

    # Notify security about the rejection
    _notify_security_rejection(doc)

    frappe.msgprint(
        _("Request rejected."),
        title=_("Rejected"),
        indicator="red",
        alert=True,
    )

    return {"status": "Rejected"}


def _check_approval_permission(doc):
    """Only the host (person_to_visit) or System Manager can approve/reject."""
    user = frappe.session.user
    if user == "Administrator":
        return

    roles = set(frappe.get_roles(user))
    if "System Manager" in roles:
        return

    employee = frappe.db.get_value("Employee", {"user_id": user}, "name")
    if employee and employee == doc.person_to_visit:
        return

    frappe.throw(
        _("Only the host ({0}) or a System Manager can approve/reject this request.").format(
            doc.person_to_visit
        ),
        title=_("Permission Denied"),
    )


@contextmanager
def _visitor_pass_workflow_paused():
    """Temporarily disable the Visitor Pass workflow so insert + submit don't go
    through Draft → Pending → Approved. The Walk In Visitor Request IS the
    approval — no second approval needed on the Visitor Pass."""
    workflow_name = frappe.db.get_value(
        "Workflow", {"document_type": "Visitor Pass", "is_active": 1}, "name"
    )
    if workflow_name:
        frappe.db.set_value("Workflow", workflow_name, "is_active", 0)
        frappe.clear_cache(doctype="Visitor Pass")
    try:
        yield
    finally:
        # Re-enable the workflow immediately
        if workflow_name:
            frappe.db.set_value("Workflow", workflow_name, "is_active", 1)
            frappe.clear_cache(doctype="Visitor Pass")


def _create_visitor_pass_from_request(request_doc, visitor=None):
    """Create an already-approved Visitor Pass from the walk-in request.

    The Visitor Pass has an active workflow (Visitor Pass Approval) that normally
    controls submission: Draft → Pending {Role} → Approved. Since the host already
    approved via the Walk In Visitor Request, we bypass the workflow entirely:
    insert the pass, then directly set docstatus=1 + workflow_state=Approved via
    db_set, and manually trigger on_submit for badge/QR/email generation.

    ``visitor`` is an Additional Visitor row: their own name/ID/photo on a pass
    that shares the request's host, date, time and purpose. Call inside
    ``_visitor_pass_workflow_paused()``.
    """
    visitor_type_name = request_doc.visitor_type

    vp = frappe.new_doc("Visitor Pass")
    vp.visitor_type = visitor_type_name
    person = visitor or request_doc
    vp.visitor_full_name = person.visitor_full_name
    # Pass needs a mobile + email; an additional visitor falls back to the lead's.
    vp.mobile_number = person.mobile_number or request_doc.mobile_number
    vp.email_id = (visitor and visitor.email_id) or request_doc.email_id
    vp.company__organisation = (visitor and visitor.company__organisation) or request_doc.company__organisation
    vp.id_proof_type = person.id_proof_type
    vp.id_proof_number = person.id_proof_number
    vp.id_proof_scan = person.id_proof_scan
    vp.visitor_photo = person.visitor_photo
    vp.person_to_visit = request_doc.person_to_visit
    vp.purpose_of_visit = request_doc.purpose_of_visit
    vp.visit_date = request_doc.visit_date
    vp.expected_checkin = (visitor and visitor.expected_checkin) or request_doc.expected_checkin
    vp.expected_checkout = (visitor and visitor.expected_checkout) or request_doc.expected_checkout
    vp.vehicle_number = visitor.vehicle_number if visitor else request_doc.vehicle_number
    # Each person's declared items go on their own pass.
    vp.items_carried = visitor.items_carried if visitor else request_doc.items_carried
    if visitor:
        vp.number_of_people = 1
    else:
        # Lead pass covers everyone not given their own pass.
        named_extra = len(request_doc.additional_visitors or [])
        vp.number_of_people = max(1, (request_doc.number_of_visitors or 1) - named_extra)
    vp.request_channel = "Walk-In"
    vp.entry_type = "New"

    vp.insert(ignore_permissions=True)
    vp.submit()

    # Set workflow_state to Approved so the pass looks correct in list views
    vp.db_set("workflow_state", "Approved", update_modified=False)

    return vp


def _close_todo(request_name):
    """Close ToDo entries linked to this request."""
    todos = frappe.get_all(
        "ToDo",
        filters={
            "reference_type": "Walk In Visitor Request",
            "reference_name": request_name,
            "status": "Open",
        },
        pluck="name",
    )
    for todo_name in todos:
        frappe.db.set_value("ToDo", todo_name, "status", "Closed")


def _notify_security_rejection(doc):
    """Notify security that the walk-in request was rejected."""
    security_users = frappe.get_all(
        "Has Role",
        filters={"role": "Security", "parenttype": "User"},
        pluck="parent",
        distinct=True,
    )
    if not security_users:
        return

    emails = frappe.get_all(
        "User",
        filters={"name": ["in", security_users], "enabled": 1},
        pluck="email",
    )
    emails = [e for e in emails if e and "@" in e]
    if not emails:
        return

    frappe.sendmail(
        recipients=emails,
        subject=_("Walk-In Request Rejected: {0}").format(doc.visitor_full_name),
        message=(
            f"<p>The walk-in visitor request for <b>{doc.visitor_full_name}</b> "
            f"has been <b style='color:red;'>REJECTED</b> by the host.</p>"
            f"<p><b>Reason:</b> {doc.rejection_reason or 'Not specified'}</p>"
            f"<p>Please inform the visitor at the gate.</p>"
        ),
    )
