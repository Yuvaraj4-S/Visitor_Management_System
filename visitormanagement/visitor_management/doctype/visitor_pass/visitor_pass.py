# Copyright (c) 2026, Harthesh
# For license information, please see license.txt

import frappe
import qrcode
from frappe.desk.doctype.notification_log.notification_log import make_notification_logs
from frappe.model.document import Document
from frappe.utils import get_url, get_url_to_form, now_datetime, today
from io import BytesIO

from visitormanagement.visitor_management.lifecycle import (
	ensure_hospitality_request,
	log_visitor_event,
	normalize_visitor_pass,
	sync_compliance_check,
)


def _get_employee_for_user(user):
    if not user:
        return None

    return frappe.db.get_value("Employee", {"user_id": user, "status": "Active"}, "name") or frappe.db.get_value(
        "Employee", {"user_id": user}, "name"
    )


def _get_lead_visit_details(reference_name):
    lead = frappe.db.get_value(
        "Lead",
        reference_name,
        ["lead_name", "mobile_no", "email_id", "company_name", "lead_owner"],
        as_dict=True,
    )

    if not lead:
        frappe.throw(f"Lead {reference_name} was not found.")

    return {
        "visitor_full_name": lead.lead_name or "",
        "mobile_number": lead.mobile_no or "",
        "email_id": lead.email_id or "",
        "company__organisation": lead.company_name or "",
        "sales_executive": _get_employee_for_user(lead.lead_owner) or "",
        "owner_user": lead.lead_owner or "",
    }


def _get_opportunity_visit_details(reference_name):
    opportunity = frappe.db.get_value(
        "Opportunity",
        reference_name,
        [
            "customer_name",
            "contact_person",
            "contact_email",
            "contact_mobile",
            "opportunity_owner",
            "opportunity_from",
            "party_name",
        ],
        as_dict=True,
    )

    if not opportunity:
        frappe.throw(f"Opportunity {reference_name} was not found.")

    visitor_name = opportunity.customer_name or ""
    company_name = opportunity.customer_name or ""
    mobile_number = opportunity.contact_mobile or ""
    email_id = opportunity.contact_email or ""

    if opportunity.contact_person:
        contact = frappe.db.get_value(
            "Contact",
            opportunity.contact_person,
            ["full_name", "mobile_no", "email_id", "company_name"],
            as_dict=True,
        )
        if contact:
            visitor_name = contact.full_name or visitor_name
            company_name = contact.company_name or company_name
            mobile_number = mobile_number or contact.mobile_no or ""
            email_id = email_id or contact.email_id or ""

    if opportunity.opportunity_from == "Lead" and opportunity.party_name:
        lead = frappe.db.get_value("Lead", opportunity.party_name, ["lead_name", "company_name"], as_dict=True)
        if lead:
            if not opportunity.contact_person:
                visitor_name = lead.lead_name or visitor_name or ""
            company_name = lead.company_name or company_name or ""
    elif opportunity.opportunity_from == "Customer" and opportunity.party_name:
        customer_name = frappe.db.get_value("Customer", opportunity.party_name, "customer_name")
        company_name = company_name or customer_name or ""
        visitor_name = visitor_name or customer_name or ""
    elif opportunity.opportunity_from == "Prospect" and opportunity.party_name:
        company_name = company_name or opportunity.party_name
        visitor_name = visitor_name or opportunity.party_name

    return {
        "visitor_full_name": visitor_name,
        "mobile_number": mobile_number,
        "email_id": email_id,
        "company__organisation": company_name,
        "sales_executive": _get_employee_for_user(opportunity.opportunity_owner) or "",
        "owner_user": opportunity.opportunity_owner or "",
    }


def _get_users_with_role(role):
    return frappe.db.sql(
        """
        select distinct u.name, u.email
        from `tabHas Role` hr
        join `tabUser` u on u.name = hr.parent
        where hr.role = %s and ifnull(u.enabled, 0) = 1 and ifnull(u.user_type, '') = 'System User'
        order by u.name
        """,
        (role,),
        as_dict=True,
    )


def _get_workflow_notification_roles(doc, stage):
    stage_map = {
        "Pending Visitor Manager": ["Visitor Manager"],
        "Pending Sales Manager": ["Sales Manager"],
        "Pending HR Manager": ["HR Manager"],
        "Pending HOD": ["HOD"],
        "Pending CEO": ["CEO"],
    }

    if doc.visitor_type == "VIP" and stage == "Approved":
        return ["HOD", "CEO"]

    return stage_map.get(stage, [])


def _render_vip_notification(doc):
    try:
        notification = frappe.get_doc("Notification", "VMS VIP Alert")
        subject = frappe.render_template(notification.subject or "", {"doc": doc})
        message = frappe.render_template(notification.message or "", {"doc": doc})
        return subject, message
    except frappe.DoesNotExistError:
        stage = doc.workflow_state or doc.status or "VIP Update"
        return (
            f"VIP {stage} | {doc.visitor_full_name} | {doc.visit_date}",
            f"<p>VIP visitor <b>{doc.visitor_full_name}</b> is now at stage <b>{stage}</b>.</p>",
        )


def _render_workflow_notification(doc, stage):
    if doc.visitor_type == "VIP":
        return _render_vip_notification(doc)

    role_label = stage.replace("Pending ", "")
    subject = f"{doc.visitor_type} Approval Required | {doc.visitor_full_name} | {doc.visit_date}"
    message = (
        "<div style='font-family: Arial, sans-serif; font-size: 13px; color: #1f2933; line-height: 1.5;'>"
        f"<h3 style='margin: 0 0 12px; color: #102a43;'>{doc.visitor_type} Approval Pending</h3>"
        f"<p style='margin: 0 0 12px;'>Approval is pending with <b>{role_label}</b>.</p>"
        "<table style='width: 100%; border-collapse: collapse; margin-bottom: 16px;'>"
        f"<tr><td style='padding: 6px; border: 1px solid #d9e2ec;'><strong>Visitor Name</strong></td><td style='padding: 6px; border: 1px solid #d9e2ec;'>{doc.visitor_full_name}</td></tr>"
        f"<tr><td style='padding: 6px; border: 1px solid #d9e2ec;'><strong>Visitor Type</strong></td><td style='padding: 6px; border: 1px solid #d9e2ec;'>{doc.visitor_type}</td></tr>"
        f"<tr><td style='padding: 6px; border: 1px solid #d9e2ec;'><strong>Company / Organisation</strong></td><td style='padding: 6px; border: 1px solid #d9e2ec;'>{doc.company__organisation or 'N/A'}</td></tr>"
        f"<tr><td style='padding: 6px; border: 1px solid #d9e2ec;'><strong>Purpose of Visit</strong></td><td style='padding: 6px; border: 1px solid #d9e2ec;'>{doc.purpose_of_visit or 'N/A'}</td></tr>"
        f"<tr><td style='padding: 6px; border: 1px solid #d9e2ec;'><strong>Host Person</strong></td><td style='padding: 6px; border: 1px solid #d9e2ec;'>{doc.person_to_visit or 'N/A'}</td></tr>"
        f"<tr><td style='padding: 6px; border: 1px solid #d9e2ec;'><strong>Visit Date</strong></td><td style='padding: 6px; border: 1px solid #d9e2ec;'>{doc.visit_date} | {doc.expected_checkin or 'N/A'} - {doc.expected_checkout or 'N/A'}</td></tr>"
        f"<tr><td style='padding: 6px; border: 1px solid #d9e2ec;'><strong>Risk / SLA</strong></td><td style='padding: 6px; border: 1px solid #d9e2ec;'>{doc.risk_level or 'N/A'} / {doc.approval_sla_minutes or 'N/A'} mins</td></tr>"
        "</table>"
        f"<p style='margin: 12px 0 0;'>Open the Visitor Pass to approve or reject this request.</p>"
        "</div>"
    )
    return subject, message


def _send_email_immediately(**kwargs):
    email_queue = frappe.sendmail(delayed=False, now=True, **kwargs)
    if email_queue:
        email_queue.send(force_send=True)
    return email_queue


def _send_workflow_stage_notification(doc):
    stage = doc.workflow_state or doc.status
    target_roles = _get_workflow_notification_roles(doc, stage)
    if not target_roles or getattr(doc, "last_workflow_notification_stage", None) == stage:
        return

    recipients = []
    for role in target_roles:
        recipients.extend(_get_users_with_role(role))

    recipient_map = {}
    for recipient in recipients:
        if recipient.email:
            recipient_map[recipient.email] = recipient

    if not recipient_map:
        return

    subject, message = _render_workflow_notification(doc, stage)
    existing_users = set(
        frappe.db.get_all(
            "Notification Log",
            filters={
                "document_type": doc.doctype,
                "document_name": doc.name,
                "subject": subject,
            },
            pluck="for_user",
        )
    )

    fresh_recipients = {
        email: recipient
        for email, recipient in recipient_map.items()
        if recipient.name not in existing_users
    }

    if not fresh_recipients:
        doc.db_set("last_workflow_notification_stage", stage, update_modified=False)
        return

    notification_doc = frappe._dict({
        "type": "Alert",
        "subject": subject,
        "email_content": message,
        "document_type": doc.doctype,
        "document_name": doc.name,
        "from_user": frappe.session.user,
        "link": get_url_to_form(doc.doctype, doc.name),
    })

    make_notification_logs(notification_doc, list(fresh_recipients.keys()))

    _send_email_immediately(
        recipients=list(fresh_recipients.keys()),
        subject=subject,
        message=message,
        reference_doctype=doc.doctype,
        reference_name=doc.name,
    )

    doc.db_set("last_workflow_notification_stage", stage, update_modified=False)


@frappe.whitelist()
def get_customer_visit_details(reference_type, reference_name):
    if reference_type not in {"Lead", "Opportunity"}:
        frappe.throw("CRM Reference Type must be Lead or Opportunity.")

    if not reference_name:
        return {}

    if reference_type == "Lead":
        return _get_lead_visit_details(reference_name)

    return _get_opportunity_visit_details(reference_name)


class VisitorPass(Document):

    @property
    def email(self):
        return self.email_id

    @property
    def company(self):
        return self.company__organisation

    @property
    def visitor_name(self):
        return self.visitor_full_name

    def after_insert(self):
        log_visitor_event(
            self.name,
            "Pass Created",
            event_status=self.status or "Draft",
            source_doctype=self.doctype,
            source_name=self.name,
            details={
                "visitor_type": self.visitor_type,
                "request_channel": self.request_channel,
            },
        )

    def on_update(self):
        _send_workflow_stage_notification(self)

    # ─────────────────────────────────────────────────────────
    # BEFORE SAVE
    # ─────────────────────────────────────────────────────────
    def before_save(self):
        normalize_visitor_pass(self)

        # Auto-fetch host department from Employee record
        if self.person_to_visit and not self.host_department:
            self.host_department = frappe.db.get_value(
                "Employee", self.person_to_visit, "department"
            )

        # Auto-set badge colour based on visitor type
        colour_map = {
            "Contractor": "Orange",
            "Candidate": "Purple",
            "Customer": "Green",
            "Supplier": "Teal",
            "VIP": "Gold",
        }

        if self.visitor_type and not self.badge_colour:
            self.badge_colour = colour_map.get(self.visitor_type, "Blue")

        if self.visitor_type == "VIP":
            self.priority_lane = 1
            if self.interpreter_required and not self.interpreter_language:
                self.interpreter_language = "English"

        # Sync item verification status from child table
        if self.visitor_items:
            total = len(self.visitor_items)
            # Match fieldname from Visitor Item DocType
            verified = sum(1 for i in self.visitor_items if getattr(i, 'verified_by_security', 0))

            if verified == 0:
                self.item_verification_status = "Pending"
            elif verified < total:
                self.item_verification_status = "Partial"
            else:
                self.item_verification_status = "All Verified"
                self.all_items_verified = 1

    # ─────────────────────────────────────────────────────────
    # BEFORE SUBMIT
    # ─────────────────────────────────────────────────────────
    def before_submit(self):
        # 1️⃣ BLACKLIST CHECK
        if self.id_proof_number:
            blacklist_name = frappe.db.exists(
                "Visitor Blacklist",
                {"id_proof_number": self.id_proof_number, "is_active": 1},
            )

            if blacklist_name:
                bl = frappe.get_doc("Visitor Blacklist", blacklist_name)
                frappe.throw(
                    f"<b>ACCESS DENIED</b><br>"
                    f"Visitor: {self.visitor_full_name}<br>"
                    f"Reason: {bl.reason}",
                    title="BLACKLISTED VISITOR",
                )

        # 2️⃣ Contractor Safety Check
        if self.visitor_type == "Contractor":
            if not getattr(self, "safety_induction_done", 0):
                frappe.msgprint(
                    "<b>Safety Warning:</b> Safety Induction is not yet completed for this contractor. "
                    "Please ensure it is checked in the Contractor Details section.",
                    indicator='orange',
                    alert=True
                )

        # 3️⃣ VIP Approval Check
        if self.visitor_type == "VIP":
            if not getattr(self, "mdceo_notified", 0):
                frappe.msgprint(
                    "<b>VIP Notification:</b> MD/CEO has not been notified. "
                    "Please check the MD/CEO Notified field in VIP Details section.",
                    indicator='orange',
                    alert=True
                )

    # ─────────────────────────────────────────────────────────
    # ON SUBMIT
    # ─────────────────────────────────────────────────────────
    def on_submit(self):
        # Record approval details
        self.db_set("approval_date", now_datetime())
        self.db_set("approved_by", frappe.session.user)
        self.db_set("status", "Approved")
        if self.visitor_type == "VIP":
            self.db_set("mdceo_notified", 1)

        # Generate QR Code for the badge (Skip for VIP)
        qr_file_url = None
        qr_content = None
        if self.visitor_type != "VIP":
            qr_file_url, qr_content = self._generate_qr_code()

        # Notify the visitor via email
        self._send_approval_email(qr_file_url, qr_content)

        # Notify Food Dept if a meal was requested
        if getattr(self, "meal_required", 0):
            self._notify_food_dept()

        ensure_hospitality_request(self)
        log_visitor_event(
            self.name,
            "Approved",
            event_status="Approved",
            source_doctype=self.doctype,
            source_name=self.name,
            details={
                "approved_by": frappe.session.user,
                "visitor_type": self.visitor_type,
            },
        )
        sync_compliance_check(self.name)
        _send_workflow_stage_notification(self)

    def on_update_after_submit(self):
        ensure_hospitality_request(self)
        sync_compliance_check(self.name)

    # ─────────────────────────────────────────────────────────
    # GENERATE BADGE NUMBER (Called by Security Log)
    # ─────────────────────────────────────────────────────────
    def generate_badge_number(self):
        if self.badge_number:
            return

        prefix_map = {
            "Contractor": "CON",
            "Candidate": "CAN",
            "Customer": "CUS",
            "Supplier": "SUP",
            "VIP": "VIP",
        }

        p = prefix_map.get(self.visitor_type, "VIS")
        count = frappe.db.count(
            "Visitor Pass",
            {"visitor_type": self.visitor_type, "visit_date": today()},
        )

        date_str = today().replace("-", "")
        badge_no = f"{p}-{date_str}-{str(count + 1).zfill(4)}"

        self.db_set("badge_number", badge_no)
        self.db_set("status", "Items Verified")

        frappe.msgprint(
            f"Badge Number Generated: {badge_no}",
            alert=True,
            indicator="green",
        )
        log_visitor_event(
            self.name,
            "Badge Issued",
            event_status="Ready for Gate",
            source_doctype=self.doctype,
            source_name=self.name,
            details={"badge_number": badge_no},
        )

    # ─────────────────────────────────────────────────────────
    # PRIVATE: GENERATE QR CODE
    # ─────────────────────────────────────────────────────────
    def _generate_qr_code(self):
        # Match keys used in visitor_gate.py (scan_qr_checkin)
        qr_data = (
            f"PASS:{self.name}"
            f"|VISITOR:{self.visitor_full_name}"
            f"|VISIT_DATE:{self.visit_date}"
            f"|ID_NO:{self.id_proof_number}"
            f"|HOST:{self.person_to_visit}"
        )

        qr_img = qrcode.make(qr_data)
        buffer = BytesIO()
        qr_img.save(buffer, format="PNG")
        qr_content = buffer.getvalue()

        # Cleanup existing QR files for this record
        frappe.db.delete("File", {
            "attached_to_doctype": "Visitor Pass",
            "attached_to_name": self.name,
            "attached_to_field": "qr_code_image"
        })

        file_doc = frappe.get_doc({
            "doctype": "File",
            "file_name": f"QR_{self.name}.png",
            "attached_to_doctype": "Visitor Pass",
            "attached_to_name": self.name,
            "attached_to_field": "qr_code_image",
            "content": qr_content,
            "is_private": 0,
        })

        file_doc.insert(ignore_permissions=True)
        self.db_set("qr_code_image", file_doc.file_url)
        return file_doc.file_url, qr_content

    # ─────────────────────────────────────────────────────────
    # PRIVATE: SEND EMAIL
    # ─────────────────────────────────────────────────────────
    def _send_approval_email(self, qr_file_url, qr_content=None):
        if not self.email_id:
            return

        items_text = ""
        if self.visitor_items:
            items_text = "<br><b>Items Declared:</b><ul>"
            for item in self.visitor_items:
                qty = getattr(item, 'quantity', 1)
                items_text += f"<li>{item.item_name} (Qty: {qty})</li>"
            items_text += "</ul>"

        attachments = []
        inline_images = []
        
        # Use a constant CID for the QR code
        qr_cid = "qr_pass_code"

        if qr_content:
            # Add as attachment fallback
            attachments.append({
                "fname": f"QR_{self.name}.png",
                "fcontent": qr_content
            })
            # Add as inline image for email clients supporting CID
            inline_images.append({
                "fname": f"QR_{self.name}.png",
                "fcontent": qr_content,
                "cid": qr_cid
            })

        qr_section = ""
        if qr_content:
            qr_section = (
                f"Please present the QR code below at the security gate:<br><br>"
                f"<img src='cid:{qr_cid}' width='200' style='border: 1px solid #ddd; padding: 10px;' alt='QR Code'><br><br>"
                f"<i>(If the image above is not visible, please use the attached QR code)</i><br><br>"
            )
        else:
            qr_section = "Please present your ID proof at the security gate for verification.<br><br>"

        _send_email_immediately(
            recipients=[self.email_id],
            subject=f"Visit APPROVED — {self.name}",
            message=(
                f"Dear {self.visitor_full_name},<br><br>"
                f"Your visit request has been approved.<br>"
                f"{qr_section}"
                f"<b>Visit Details:</b><br>"
                f"Pass ID: {self.name}<br>"
                f"Host: {self.person_to_visit}<br>"
                f"Date: {self.visit_date}<br>"
                f"{items_text}"
            ),
            attachments=attachments,
            inline_images=inline_images,
            reference_doctype=self.doctype,
            reference_name=self.name,
        )

    # ─────────────────────────────────────────────────────────
    # PRIVATE: NOTIFY FOOD DEPT
    # ─────────────────────────────────────────────────────────
    def _notify_food_dept(self):
        food_email = frappe.db.get_single_value("VMS Settings", "food_dept_email")
        if food_email:
            _send_email_immediately(
                recipients=[food_email],
                subject=f"Meal Required: {self.visitor_full_name}",
                message=f"Meal Type: {self.meal_type}<br>Visitor Pass: {self.name}",
                reference_doctype=self.doctype,
                reference_name=self.name,
            )


@frappe.whitelist()
def sync_badge_number(visitor_pass):
    doc = frappe.get_doc("Visitor Pass", visitor_pass)
    if not doc.badge_number:
        doc.generate_badge_number()
        doc.reload()
    return doc.badge_number
