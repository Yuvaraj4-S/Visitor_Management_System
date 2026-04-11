# Copyright (c) 2026, Harthesh
# For license information, please see license.txt

import frappe
import qrcode
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime, today, get_url
from io import BytesIO
from visitormanagement.visitor_management.lifecycle import (
    ensure_hospitality_request,
    normalize_visitor_pass,
)

PENDING_LANES_BY_VISITOR_TYPE = {
    "Contractor": ("Pending System Manager",),
    "Supplier": ("Pending System Manager",),
    "Customer": ("Pending Sales Manager",),
    "Candidate": ("Pending HR Manager",),
    "VIP": ("Pending HOD", "Pending CEO"),
}

ALL_PENDING_LANES = {
    "Pending System Manager",
    "Pending Visitor Manager",
    "Pending Sales Manager",
    "Pending HR Manager",
    "Pending HOD",
    "Pending CEO",
}

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

    def validate(self):
        normalize_visitor_pass(self)
        self._align_workflow_lane_with_visitor_type()

    def _align_workflow_lane_with_visitor_type(self):
        if not self.visitor_type or not self.workflow_state:
            return

        if self.workflow_state not in ALL_PENDING_LANES:
            return

        allowed_lanes = PENDING_LANES_BY_VISITOR_TYPE.get(self.visitor_type)
        if not allowed_lanes:
            return

        if self.workflow_state in allowed_lanes:
            return

        self.workflow_state = allowed_lanes[0]


    # ─────────────────────────────────────────────────────────
    # BEFORE SAVE
    # ─────────────────────────────────────────────────────────
    def before_save(self):
        # Auto-fetch host department from Employee record
        if self.person_to_visit and not self.host_department:
            self.host_department = frappe.db.get_value(
                "Employee", self.person_to_visit, "department"
            )

        # Auto-set badge colour from VMS Settings (or defaults)
        if self.visitor_type:
            settings = frappe.get_cached_doc("VMS Settings")
            colour_field = f"badge_colour_{self.visitor_type.lower()}"
            colour = getattr(settings, colour_field, None)
            if not colour:
                colour = {"Contractor": "Orange", "Candidate": "Purple", "Customer": "Green",
                           "Supplier": "Teal", "VIP": "Gold"}.get(self.visitor_type, "Orange")
            self.badge_colour = colour

        # Sync item verification status from child table
        if self.visitor_items:
            total = len(self.visitor_items)
            # Match fieldname from Visitor Item DocType
            verified = sum(1 for i in self.visitor_items if i.verified_by_security)

            if verified == 0:
                self.item_verification_status = "Pending"
            elif verified < total:
                self.item_verification_status = "Partial"
            else:
                self.item_verification_status = "All Verified"
                self.all_items_verified = 1

    def on_update(self):
        ensure_hospitality_request(self)

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
                frappe.throw(
                    _("Safety Induction must be completed before submitting a Contractor Visitor Pass. "
                      "Please check the Safety Induction field in the Contractor Details section."),
                    title=_("Safety Induction Required"),
                )

        # 3️⃣ VIP Approval Check
        if self.visitor_type == "VIP":
            if not getattr(self, "mdceo_notified", 0):
                frappe.throw(
                    _("MD/CEO must be notified before submitting a VIP Visitor Pass. "
                      "Please check the MD/CEO Notified field in the VIP Details section."),
                    title=_("VIP Notification Required"),
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
        qr_file_url, qr_content = self._generate_qr_code()

        # Notify the visitor via email
        self._send_approval_email(qr_file_url, qr_content)

        # Notify Food Dept if a meal was requested
        if getattr(self, "meal_required", 0):
            self._notify_food_dept()

    # ─────────────────────────────────────────────────────────
    # GENERATE BADGE NUMBER (Called by Security Log)
    # ─────────────────────────────────────────────────────────
    def generate_badge_number(self):
        if self.badge_number:
            return

        # Check VMS Settings — is badge enabled for this visitor type?
        settings = frappe.get_cached_doc("VMS Settings")
        if not getattr(settings, "enable_badge", 1):
            return

        badge_types = (getattr(settings, "badge_required_for", "") or "").strip()
        if badge_types and self.visitor_type not in badge_types:
            return

        # Get prefix from settings or use defaults
        prefix_field = f"badge_prefix_{self.visitor_type.lower()}"
        p = getattr(settings, prefix_field, None) or {
            "Contractor": "CON",
            "Candidate": "CAN",
            "Customer": "CUS",
            "Supplier": "SUP",
            "VIP": "VIP",
        }.get(self.visitor_type, "VIS")

        count = frappe.db.count(
            "Visitor Pass",
            {"visitor_type": self.visitor_type, "visit_date": today()},
        )

        date_str = today().replace("-", "")
        badge_no = f"{p}-{date_str}-{str(count + 1).zfill(4)}"

        self.db_set("badge_number", badge_no)
        self.db_set("status", "Items Verified")

        frappe.msgprint(
            _("Badge Number Generated: {0}").format(badge_no),
            alert=True,
            indicator="green",
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

        frappe.sendmail(
            recipients=[self.email_id],
            subject=f"Visit APPROVED — {self.name}",
            message=(
                f"Dear {self.visitor_full_name},<br><br>"
                f"Your visit request has been approved.<br>"
                f"Please present the QR code below at the security gate:<br><br>"
                f"<img src='cid:{qr_cid}' width='200' style='border: 1px solid #ddd; padding: 10px;' alt='QR Code'><br><br>"
                f"<i>(If the image above is not visible, please use the attached QR code)</i><br><br>"
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
def search_existing_by_phone(phone):
    if not phone:
        return []
    visitors = frappe.db.sql("""
        SELECT name, visitor_full_name, visitor_type
        FROM `tabVisitor Pass`
        WHERE mobile_number = %s
        ORDER BY creation DESC
        LIMIT 10
    """, (phone,), as_dict=True)
    return visitors

@frappe.whitelist()
def search_existing_by_id(id_number):
    if not id_number:
        return []
    visitors = frappe.db.sql("""
        SELECT name, visitor_full_name, visitor_type
        FROM `tabVisitor Pass`
        WHERE id_proof_number = %s
        ORDER BY creation DESC
        LIMIT 10
    """, (id_number,), as_dict=True)
    return visitors


def _normalized_digits(value):
    return "".join(ch for ch in (value or "") if ch.isdigit())


@frappe.whitelist()
def get_existing_visitor_matches(visitor_type=None, id_proof_number=None, mobile_number=None, exclude_name=None):
    id_proof_number = (id_proof_number or "").strip()
    mobile_number = (mobile_number or "").strip()
    exclude_name = (exclude_name or "").strip()
    visitor_type = (visitor_type or "").strip()

    if not id_proof_number and not mobile_number:
        return {"best_match": None, "matches": []}

    matches = []
    seen = set()

    def _push(rows):
        for row in rows:
            if row.name in seen:
                continue
            seen.add(row.name)
            matches.append(row)

    type_filter = "AND visitor_type = %(visitor_type)s" if visitor_type else ""
    exclude_filter = "AND name != %(exclude_name)s" if exclude_name else ""
    params = {
        "visitor_type": visitor_type,
        "exclude_name": exclude_name,
        "id_proof_number": id_proof_number,
    }

    if id_proof_number:
        by_id = frappe.db.sql(
            f"""
            SELECT name, visitor_full_name, visitor_type, mobile_number, id_proof_number
            FROM `tabVisitor Pass`
            WHERE id_proof_number = %(id_proof_number)s
              {type_filter}
              {exclude_filter}
            ORDER BY modified DESC
            LIMIT 10
            """,
            params,
            as_dict=True,
        )
        _push(by_id)

    if mobile_number and len(matches) < 10:
        phone_digits = _normalized_digits(mobile_number)
        by_phone = frappe.db.sql(
            f"""
            SELECT name, visitor_full_name, visitor_type, mobile_number, id_proof_number
            FROM `tabVisitor Pass`
            WHERE ifnull(mobile_number, '') != ''
              {type_filter}
              {exclude_filter}
            ORDER BY modified DESC
            LIMIT 100
            """,
            {
                "visitor_type": visitor_type,
                "exclude_name": exclude_name,
            },
            as_dict=True,
        )

        for row in by_phone:
            if row.name in seen:
                continue
            if not phone_digits:
                continue
            if _normalized_digits(row.mobile_number) == phone_digits:
                _push([row])
            if len(matches) >= 10:
                break

    return {"best_match": matches[0] if matches else None, "matches": matches}


@frappe.whitelist()
def get_existing_visitor_pass_details(visitor_pass, visitor_type=None):
    if not visitor_pass:
        frappe.throw("Visitor Pass is required.")

    doc = frappe.get_doc("Visitor Pass", visitor_pass)
    if visitor_type and doc.visitor_type != visitor_type:
        frappe.throw("Selected record type does not match current Visitor Type.")

    common_fields = [
        "name",
        "visitor_type",
        "visitor_full_name",
        "mobile_number",
        "email_id",
        "company__organisation",
        "id_proof_type",
        "id_proof_number",
        "id_proof_scan",
        "visitor_photo",
        "purpose_of_visit",
        "person_to_visit",
        "host_department",
        "visit_date",
        "expected_checkin",
        "expected_checkout",
    ]

    type_fields = {
        "Supplier": [
        "supplier_visit_mode",
        "supplier_link",
        "purchase_order",
        "delivery_note",
        "goods_description",
        "meeting_subject",
        "meeting_start_time",
        "meeting_end_time",
        "meeting_room",
        "attendees",
        "refreshments_required",
        "refreshment_notes",
        "presentation_material",
        "nda_required",
        "documents_shared",
        ],
        "Customer": [
        "crm_reference_type",
        "crm_lead_opportunity",
        "visit_category",
        "sales_executive",
        "products_discussed",
        "meeting_outcome",
        "followup_date",
        "meeting_minutes",
        ],
        "Contractor": [
        "contractor_link",
        "work_order_ref",
        "safety_induction_done",
        "contractor_nda_signed",
        "contractor_nda_document",
        "ppe_provided",
        "ppe_provided_document",
        "work_area_zone",
        "tools_list",
        "multi_day_pass",
        "pass_valid_until",
        ],
        "Candidate": [
        "job_applicant_link",
        "position_applied",
        "candidate_interview_type",
        "interview_panel",
        "interview_room",
        ],
    }

    fields = common_fields + type_fields.get(doc.visitor_type, [])
    return {field: doc.get(field) for field in fields}


@frappe.whitelist()
def sync_badge_number(visitor_pass):
    """Generate badge number for a visitor pass if not already set."""
    vp = frappe.get_doc("Visitor Pass", visitor_pass)
    if vp.badge_number:
        return vp.badge_number

    vp.generate_badge_number()
    vp.reload()
    return vp.badge_number
