# Copyright (c) 2026, Harthesh
# For license information, please see license.txt

import frappe
import qrcode
from frappe.model.document import Document
from frappe.utils import now_datetime, today, get_url
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

        frappe.sendmail(
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
            inline_images=inline_images
        )

    # ─────────────────────────────────────────────────────────
    # PRIVATE: NOTIFY FOOD DEPT
    # ─────────────────────────────────────────────────────────
    def _notify_food_dept(self):
        food_email = frappe.db.get_single_value("VMS Settings", "food_dept_email")
        if food_email:
            frappe.sendmail(
                recipients=[food_email],
                subject=f"Meal Required: {self.visitor_full_name}",
                message=f"Meal Type: {self.meal_type}<br>Visitor Pass: {self.name}",
            )


@frappe.whitelist()
def sync_badge_number(visitor_pass):
    doc = frappe.get_doc("Visitor Pass", visitor_pass)
    if not doc.badge_number:
        doc.generate_badge_number()
        doc.reload()
    return doc.badge_number
