# For license information, please see license.txt

import re
import frappe
import qrcode
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime, today, get_url, getdate, get_time, date_diff, cint
from io import BytesIO
from visitormanagement.visitor_management.lifecycle import (
    ensure_hospitality_request,
    normalize_visitor_pass,
)
from visitormanagement.visitor_management.validators import (
    id_proof_error_message,
    validate_id,
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
    "Pending Sales Manager",
    "Pending HR Manager",
    "Pending HOD",
    "Pending CEO",
}

class VisitorPass(Document):

    # Aliases used by notification templates and external references.
    # `visitor_name` is referenced by gate_security_alert.html and vms_prr_submitted notification.
    # `company` is referenced by gate_security_alert.html as {{ doc.company }}.
    @property
    def visitor_name(self):
        return self.visitor_full_name

    @property
    def company(self):
        return self.company__organisation

    def validate(self):
        normalize_visitor_pass(self)
        self._align_workflow_lane_with_visitor_type()
        self._validate_schedule()
        self._validate_formats()
        self._validate_host_active()
        self._validate_duplicate_pass()
        self._validate_visit_duration()

    # ─────────────────────────────────────────────────────────
    # BUSINESS VALIDATIONS
    # ─────────────────────────────────────────────────────────
    def _validate_schedule(self):
        """Block past dates, enforce check-in < check-out, enforce future-date ceiling."""
        if not self.visit_date:
            return

        today_date = getdate(today())
        visit_date = getdate(self.visit_date)

        # Past date blocked — allow only if the pass is already checked-in/out (historical edits OK)
        if visit_date < today_date and self.status in (None, "", "Draft", "Approved"):
            if not (self.docstatus == 1 and self.status in ("Checked-In", "Checked-Out", "Cancelled")):
                frappe.throw(
                    _("Visit date {0} is in the past. Pick today or a future date.").format(self.visit_date),
                    title=_("Invalid Visit Date"),
                )

        # Future date ceiling — 90 days ahead max
        if date_diff(visit_date, today_date) > 90:
            frappe.throw(
                _("Visit date cannot be more than 90 days in the future."),
                title=_("Invalid Visit Date"),
            )

        # Check-in before check-out
        if self.expected_checkin and self.expected_checkout:
            if get_time(self.expected_checkin) >= get_time(self.expected_checkout):
                frappe.throw(
                    _("Expected Check-In ({0}) must be before Expected Check-Out ({1}).").format(
                        self.expected_checkin, self.expected_checkout
                    ),
                    title=_("Invalid Time Range"),
                )

    def _validate_formats(self):
        """Validate email and ID proof number format per type."""
        if self.email_id:
            if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", self.email_id):
                frappe.throw(
                    _("Email ID '{0}' is not a valid email address.").format(self.email_id),
                    title=_("Invalid Email"),
                )

        if self.id_proof_type and self.id_proof_number:
            if not validate_id(self.id_proof_type, self.id_proof_number):
                frappe.throw(
                    _(id_proof_error_message(self.id_proof_type)),
                    title=_("Invalid ID Proof"),
                )

    def _validate_host_active(self):
        """Host must be an Active employee."""
        if not self.person_to_visit:
            return
        status = frappe.db.get_value("Employee", self.person_to_visit, "status")
        if status != "Active":
            frappe.throw(
                _("Host {0} is not an Active employee (status: {1}). Cannot assign pass.").format(
                    self.person_to_visit, status or "Unknown"
                ),
                title=_("Invalid Host"),
            )

    def _validate_duplicate_pass(self):
        """Same visitor (by ID proof) cannot have multiple active passes on the same date."""
        if not self.id_proof_number or not self.visit_date:
            return
        existing = frappe.db.sql(
            """
            SELECT name FROM `tabVisitor Pass`
            WHERE id_proof_number = %(id)s
              AND visit_date = %(date)s
              AND name != %(self_name)s
              AND docstatus < 2
              AND status NOT IN ('Cancelled', 'Rejected')
            LIMIT 1
            """,
            {
                "id": self.id_proof_number,
                "date": self.visit_date,
                "self_name": self.name or "NEW",
            },
        )
        if existing:
            frappe.throw(
                _("A visitor pass ({0}) already exists for this ID Proof on {1}. Duplicate passes are not allowed.").format(
                    existing[0][0], self.visit_date
                ),
                title=_("Duplicate Pass"),
            )

    def _validate_visit_duration(self):
        """Enforce max visit duration from VMS Settings."""
        if not (self.expected_checkin and self.expected_checkout):
            return

        if self._is_host_approved_invitation_schedule():
            return

        settings = frappe.get_cached_doc("VMS Settings")
        max_hours = cint(getattr(settings, "max_visit_duration_hrs", 0))
        if not max_hours:
            return

        ci = get_time(self.expected_checkin)
        co = get_time(self.expected_checkout)
        duration_hours = (co.hour * 60 + co.minute - ci.hour * 60 - ci.minute) / 60
        if duration_hours > max_hours:
            frappe.throw(
                _("Visit duration ({0:.1f} hrs) exceeds the maximum allowed ({1} hrs). "
                  "Adjust the expected check-in/out times.").format(duration_hours, max_hours),
                title=_("Visit Too Long"),
            )

    def _is_host_approved_invitation_schedule(self):
        """Allow invitation-backed passes to retain the host-approved visit window."""
        if not self.visitor_invitation or not frappe.db.exists("Visitor Invitation", self.visitor_invitation):
            return False

        invitation = frappe.get_cached_doc("Visitor Invitation", self.visitor_invitation)
        return (
            str(self.visit_date or "") == str(invitation.visit_date or "")
            and str(self.expected_checkin or "") == str(invitation.expected_checkin or "")
            and str(self.expected_checkout or "") == str(invitation.expected_checkout or "")
            and (self.person_to_visit or "") == (invitation.host_employee or "")
        )

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
        self._sync_items_carried()
        self._normalize_mobile_number()
        self._set_visitor_summary()
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

    def _alert_blacklist_match(self, blacklist_doc):
        recipients = self._security_alert_recipients()
        if not recipients:
            return
        try:
            frappe.sendmail(
                recipients=recipients,
                subject=f"🚨 Blacklist match attempt: {self.visitor_full_name}",
                message=(
                    f"<p><b>A blacklisted visitor attempted entry.</b></p>"
                    f"<ul>"
                    f"<li><b>Visitor:</b> {self.visitor_full_name}</li>"
                    f"<li><b>ID Proof:</b> {self.id_proof_type} — {self.id_proof_number}</li>"
                    f"<li><b>Mobile:</b> {self.mobile_number or '-'}</li>"
                    f"<li><b>Reason on file:</b> {blacklist_doc.reason}</li>"
                    f"<li><b>Attempted host:</b> {self.person_to_visit or '-'}</li>"
                    f"<li><b>Time:</b> {frappe.utils.now()}</li>"
                    f"</ul>"
                    f"<p>Entry was blocked. No Visitor Pass created.</p>"
                ),
                reference_doctype="Visitor Blacklist",
                reference_name=blacklist_doc.name,
                now=True,
            )
        except Exception as exc:
            frappe.log_error(f"Blacklist alert email failed: {exc}", "VMS Blacklist Alert")

    def _security_alert_recipients(self):
        users = frappe.get_all(
            "Has Role",
            filters={"role": ["in", ["Security", "System Manager"]], "parenttype": "User"},
            fields=["parent"],
            distinct=True,
        )
        emails = []
        for u in users:
            email = frappe.db.get_value("User", u.parent, "email")
            if email and email != "Administrator" and "@" in email:
                emails.append(email)
        return list(set(emails))

    def _normalize_mobile_number(self):
        if not self.mobile_number:
            return
        raw = str(self.mobile_number).strip()
        digits = "".join(c for c in raw if c.isdigit())
        if not digits:
            return
        if digits.startswith("91") and len(digits) > 10:
            digits = digits[2:]
        # Frappe Phone widget expects "+{isd}-{number}" format (hyphen, NOT space)
        # See apps/frappe/frappe/public/js/frappe/form/controls/phone.js:167
        self.mobile_number = f"+91-{digits[-10:]}" if len(digits) >= 10 else raw

    def _set_visitor_summary(self):
        mobile_display = (self.mobile_number or "").replace("-", " ")
        parts = [self.visitor_full_name or "", mobile_display]
        self.visitor_summary = " | ".join(p for p in parts if p)

    def _sync_items_carried(self):
        """Bi-directional sync between `items_carried` (Small Text shown on the
        desk form) and the structured `visitor_items` child table.

        Rules — in priority order:
          1. If `visitor_items` already has rows AND items_carried matches the
             auto-summary of those rows → data is already consistent, leave
             everything alone. This is the path the portal uses when it sets
             both fields atomically with structured rows + a derived summary.
          2. If items_carried is set and visitor_items is empty → create a
             single mirror row (keeps gate verification working for
             desk-form users who only type free-text).
          3. If items_carried is set but visitor_items rows DON'T match the
             summary → the desk-form user just edited items_carried; rebuild
             the structured rows from the new text (single row mirror,
             preserving prior verification state).
          4. If items_carried is empty and rows exist → derive a summary into
             items_carried so prints / dropdowns have a single label.
        """
        text = (self.items_carried or "").strip()
        rows = list(self.visitor_items or [])
        auto_summary = self._summarise_items(rows)

        if rows and text and text == auto_summary:
            # Already consistent; don't rebuild anything.
            return

        if text:
            current_first = rows[0] if rows else None
            current_first_name = (current_first.item_name or "").strip() if current_first else ""
            if current_first_name == text and len(rows) == 1:
                return
            preserved_verified = current_first.verified_by_security if current_first else 0
            preserved_remarks = current_first.verification_remarks if current_first else None
            self.set("visitor_items", [])
            row = self.append("visitor_items", {
                "item_name": text,
                "quantity": 1,
            })
            if preserved_verified:
                row.verified_by_security = 1
            if preserved_remarks:
                row.verification_remarks = preserved_remarks
        elif rows:
            # items_carried empty but rows exist (portal flow) → derive summary
            self.items_carried = auto_summary

    @staticmethod
    def _summarise_items(rows):
        """Build the same comma-separated summary string the portal helper
        creates, so the controller can detect 'already-in-sync' state."""
        if not rows:
            return None
        parts = []
        for r in rows:
            name = (r.item_name or "").strip()
            if not name:
                continue
            qty = r.quantity
            if qty and int(qty or 0) > 1:
                name = f"{name} (x{int(qty)})"
            parts.append(name)
        return ", ".join(parts) if parts else None

    def on_update(self):
        if self.docstatus == 0 and self.status == "Draft":
            return
        ensure_hospitality_request(self)

    # ─────────────────────────────────────────────────────────
    # BEFORE SUBMIT
    # ─────────────────────────────────────────────────────────
    def before_submit(self):
        # 0️⃣ REQUIRED DOCUMENTS
        if not self.visitor_photo:
            frappe.throw(
                _("Visitor Photo is required before submitting the pass."),
                title=_("Missing Visitor Photo"),
            )
        if not self.id_proof_scan:
            frappe.throw(
                _("ID Proof Scan is required before submitting the pass."),
                title=_("Missing ID Proof Scan"),
            )

        # 0️⃣.5 OPTIONAL ITEM DECLARATION (enforced via VMS Settings)
        settings = frappe.get_cached_doc("VMS Settings")
        if settings.get("require_item_declaration") and not self.visitor_items:
            frappe.throw(
                _("Item declaration is required — add at least one item row before submitting."),
                title=_("Items Not Declared"),
            )

        # 1️⃣ BLACKLIST CHECK
        if self.id_proof_number:
            blacklist_name = frappe.db.exists(
                "Visitor Blacklist",
                {"id_proof_number": self.id_proof_number, "is_active": 1},
            )

            if blacklist_name:
                bl = frappe.get_doc("Visitor Blacklist", blacklist_name)
                self._alert_blacklist_match(bl)
                frappe.throw(
                    msg=_(
                        "Visitor: {0}\n"
                        "Reason: {1}\n\n"
                        "This person is on the active blacklist. The pass cannot be submitted."
                    ).format(self.visitor_full_name, bl.reason or _("Not specified")),
                    title=_("Access Denied — Blacklisted Visitor"),
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

        # Generate badge number for all approved visitors (non-VIP).
        # VIPs get badge at gate check-in (handled in security_log.py).
        # Only skip if badge disabled in settings or visitor_type not in badge_required_for.
        if self.visitor_type != "VIP":
            self.generate_badge_number(update_status=False)

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
    def generate_badge_number(self, update_status=True):
        """Generate a badge number if enabled in VMS Settings.

        `update_status=True` means this was called from the items-verification flow
        (Security Log) — move the pass to "Items Verified".
        `update_status=False` means called from on_submit — keep status as "Approved".
        """
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
        if update_status:
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
        items_section = ""
        if self.visitor_items:
            items_section = (
                "<h3 style='margin: 16px 0 6px; font-size: 14px;'>Items Declared</h3>"
                "<ul style='margin: 0 0 12px 20px; padding: 0;'>"
            )
            for item in self.visitor_items:
                qty = getattr(item, 'quantity', 1)
                items_section += f"<li>{item.item_name} (Qty: {qty})</li>"
            items_section += "</ul>"

        time_value = ""
        if self.expected_checkin and self.expected_checkout:
            time_value = f"{self.expected_checkin} &ndash; {self.expected_checkout}"

        td_label = "padding: 8px; border: 1px solid #ddd; width: 30%;"
        td_value = "padding: 8px; border: 1px solid #ddd;"

        details_rows = [
            ("Date", self.visit_date or ""),
            ("Time", time_value),
            ("Host", self.person_to_visit or ""),
            ("Purpose", self.purpose_of_visit or ""),
            ("Pass ID", self.name or ""),
        ]
        details_html = (
            "<table style='border-collapse: collapse; width: 100%; margin: 0 0 12px 0;'>"
        )
        for i, (label, value) in enumerate(details_rows):
            bg = "background: #f4f5f7;" if i % 2 == 0 else ""
            details_html += (
                f"<tr style='{bg}'>"
                f"<td style='{td_label}'><b>{label}</b></td>"
                f"<td style='{td_value}'>{value}</td>"
                f"</tr>"
            )
        details_html += "</table>"

        contractor_li = ""
        if self.visitor_type == "Contractor":
            contractor_li = (
                "<li>Ensure you have completed the required safety induction and are "
                "wearing provided PPE.</li>"
            )

        attachments = []
        if qr_content:
            attachments.append({
                "fname": f"QR_{self.name}.png",
                "fcontent": qr_content
            })

        frappe.sendmail(
            recipients=[self.email_id],
            subject=f"Visit Approved: {self.visit_date} — Pass {self.name}",
            message=(
                f"<div style='font-family: Arial, sans-serif; font-size: 14px; color: #1f2933; line-height: 1.5;'>"
                f"<p>Dear <b>{self.visitor_full_name}</b>,</p>"
                f"<p>Your visit has been <b style='color: #28a745;'>APPROVED</b>.</p>"
                f"<h3 style='margin: 16px 0 6px; font-size: 14px;'>Visit Details</h3>"
                f"{details_html}"
                f"{items_section}"
                f"<h3 style='margin: 16px 0 6px; font-size: 14px;'>On Arrival</h3>"
                f"<ul style='margin: 0 0 12px 20px; padding: 0;'>"
                f"<li>Please carry a valid photo ID matching the one you registered with.</li>"
                f"<li>Scan the <b>QR code attached to this email</b> at the security gate.</li>"
                f"<li>Your physical badge will be issued at the security desk after item verification.</li>"
                f"{contractor_li}"
                f"</ul>"
                f"<p>We look forward to welcoming you.</p>"
                f"<p style='margin-top: 20px; color: #64748b; font-size: 12px;'>"
                f"This is an automated email. Please contact your host for any changes or queries."
                f"</p>"
                f"</div>"
            ),
            attachments=attachments,
        )

    # ─────────────────────────────────────────────────────────
    # PRIVATE: NOTIFY FOOD DEPT
    # ─────────────────────────────────────────────────────────
    def _notify_food_dept(self):
        food_email = frappe.db.get_single_value("VMS Settings", "food_dept_email")
        if not food_email:
            return
        try:
            frappe.sendmail(
                recipients=[food_email],
                subject=f"Meal Required: {self.visitor_full_name}",
                message=f"Meal Type: {self.meal_type}<br>Visitor Pass: {self.name}",
            )
        except Exception as exc:
            frappe.log_error(f"Food dept notification failed for {self.name}: {exc}", "VMS Food Dept Notification")

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
@frappe.validate_and_sanitize_search_inputs
def search_visitor_passes(doctype, txt, searchfield, start, page_len, filters):
    filters = filters or {}
    visitor_type = filters.get("visitor_type")

    conditions = []
    params = []
    if visitor_type:
        conditions.append("visitor_type = %s")
        params.append(visitor_type)
    if txt:
        like = f"%{txt}%"
        conditions.append(
            "(name LIKE %s OR visitor_full_name LIKE %s OR mobile_number LIKE %s OR email_id LIKE %s OR visitor_summary LIKE %s)"
        )
        params.extend([like] * 5)

    where = " AND ".join(conditions) if conditions else "1=1"
    # Compute the dropdown description live (visitor_full_name · mobile_number) so
    # reception can spot + search by phone, regardless of how stale the cached
    # `visitor_summary` is on legacy/demo records.
    # Compute the dropdown description live (visitor_full_name · mobile_number) so reception
    # can spot + search by phone, regardless of how stale the cached visitor_summary is.

    return frappe.db.sql(
        f"""SELECT
                name,
                CONCAT_WS(' · ', NULLIF(visitor_full_name, ''), NULLIF(mobile_number, '')) AS description
            FROM `tabVisitor Pass`
            WHERE {where}
            ORDER BY modified DESC
            LIMIT %s OFFSET %s""",
        params + [int(page_len), int(start)],
    )


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
            "tools_list",
            "multi_day_pass",
            "pass_valid_until",
        ],
        "Candidate": [
            "job_applicant_link",
            "position_applied",
            "candidate_interview_type",
            "interview_panel",
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
