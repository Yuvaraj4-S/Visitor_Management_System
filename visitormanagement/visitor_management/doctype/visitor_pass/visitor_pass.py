# Copyright (c) 2026, Harthesh
# For license information, please see license.txt

import frappe
import qrcode
from frappe.model.document import Document
from frappe.utils import now_datetime, today, get_url
from io import BytesIO

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


    # ─────────────────────────────────────────────────────────
    # BEFORE SAVE
    # ─────────────────────────────────────────────────────────
    def before_save(self):
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
            # Ensure 'is_verified' is the correct fieldname in Visitor Items Table
            verified = sum(1 for i in self.visitor_items if getattr(i, 'is_verified', 0))

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
            # Fetches safety_induction status from linked Contractor Visit
            safety = frappe.db.get_value(
                "Contractor Visit",
                {"visitor_pass": self.name},
                "safety_induction_done",
            )
            # Handle case where record doesn't exist (None) or is 0
            if not safety:
                frappe.msgprint(
                    "<b>Safety Warning:</b> Safety Induction is not yet completed for this contractor. "
                    "Please ensure it is verified in the Contractor Visit form.",
                    indicator='orange',
                    alert=True
                )

        # 3️⃣ VIP Approval Check
        if self.visitor_type == "VIP":
            notified = frappe.db.get_value(
                "VIP Visit",
                {"visitor_pass": self.name},
                "mdceo_notified",
            )
            if not notified:
                frappe.msgprint(
                    "<b>VIP Notification:</b> MD/CEO has not been notified. "
                    "Please update the VIP Visit form record.",
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

        # Generate QR Code for the badge
        qr_file_url = self._generate_qr_code()

        # Notify the visitor via email
        self._send_approval_email(qr_file_url)

        # Notify Food Dept if a meal was requested
        if getattr(self, "meal_required", 0):
            self._notify_food_dept()

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

    # ─────────────────────────────────────────────────────────
    # PRIVATE: GENERATE QR CODE
    # ─────────────────────────────────────────────────────────
    def _generate_qr_code(self):
        qr_data = (
            f"PASS:{self.name}"
            f"|VISITOR:{self.visitor_full_name}"
            f"|ID_NO:{self.id_proof_number}"
            f"|DATE:{self.visit_date}"
            f"|HOST:{self.person_to_visit}"
        )

        qr_img = qrcode.make(qr_data)
        buffer = BytesIO()
        qr_img.save(buffer, format="PNG")
        buffer.seek(0)

        file_doc = frappe.get_doc({
            "doctype": "File",
            "file_name": f"QR_{self.name}.png",
            "attached_to_doctype": "Visitor Pass",
            "attached_to_name": self.name,
            "attached_to_field": "qr_code_image",
            "content": buffer.read(),
            "is_private": 0,
        })

        file_doc.insert(ignore_permissions=True)
        self.db_set("qr_code_image", file_doc.file_url)
        return file_doc.file_url

    # ─────────────────────────────────────────────────────────
    # PRIVATE: SEND EMAIL
    # ─────────────────────────────────────────────────────────
    def _send_approval_email(self, qr_file_url):
        if not self.email_id:
            return

        site_url = get_url()
        qr_full_url = site_url + qr_file_url if qr_file_url else ""

        items_text = ""
        if self.visitor_items:
            items_text = "<br><b>Items Declared:</b><ul>"
            for item in self.visitor_items:
                # Ensure 'quantity' matches your Child DocType fieldname
                qty = getattr(item, 'quantity', 1)
                items_text += f"<li>{item.item_name} — Qty: {qty}</li>"
            items_text += "</ul>"

        frappe.sendmail(
            recipients=[self.email_id],
            subject=f"Visit APPROVED — {self.name}",
            message=(
                f"Dear {self.visitor_full_name},<br><br>"
                f"Your visit request has been approved.<br>"
                f"Please present the QR code below at the security gate:<br><br>"
                f"<img src='{qr_full_url}' width='180'><br><br>"
                f"{items_text}"
            ),
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