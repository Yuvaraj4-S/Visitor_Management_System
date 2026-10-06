# For license information, please see license.txt

import re
import frappe
import qrcode
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime, today, get_url, getdate, get_time, date_diff, cint
from io import BytesIO
from visitormanagement.visitor_management.time_utils import (
    format_time_hhmm,
    strip_expected_time_seconds,
    strip_seconds,
)
from visitormanagement.visitor_management.multi_day import get_visit_end_date, validate_multi_day_range
from visitormanagement.visitor_management.lifecycle import (
    ensure_hospitality_request,
    log_visitor_event,
    normalize_visitor_pass,
)
from visitormanagement.visitor_management.validators import (
    id_proof_error_message,
    mask_id_number,
    validate_id,
)

# Per-visitor hospitality the host sets on Visitor Invitation → Visitors table.
INVITATION_GUEST_PASS_FIELDS = ("meal_required", "cab_required", "factory_tour_required")

# Approver role → the workflow pending lane it owns. Lanes are derived from each
# Visitor Type's approver_role (and secondary_approver_role) so custom types work
# without touching this map — it only translates a role name to its lane.
ROLE_TO_PENDING_LANE = {
    "System Manager": "Pending System Manager",
    "Sales Manager": "Pending Sales Manager",
    "HR Manager": "Pending HR Manager",
    "HOD": "Pending HOD",
    "CEO": "Pending CEO",
}

ALL_PENDING_LANES = set(ROLE_TO_PENDING_LANE.values())


def _get_visitor_type_doc(visitor_type_name):
    """Fetch the linked Visitor Type master record (cached — this is a small,
    frequently-read doc, so `get_cached_doc` avoids a DB round-trip on every
    Visitor Pass save)."""
    if not visitor_type_name:
        return None
    try:
        return frappe.get_cached_doc("Visitor Type", visitor_type_name)
    except frappe.DoesNotExistError:
        return None


def _get_home_country():
    """Return the home country for nationality checks.
    VMS Settings does not have a home_country field yet, so default to India."""
    try:
        return frappe.db.get_single_value("VMS Settings", "home_country") or "India"
    except Exception:
        return "India"


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
        strip_expected_time_seconds(self)

        # Nationality is mandatory. It defaults to the configured home country so
        # that any creation path (portal, API, automation, import) that doesn't
        # supply it still saves, instead of failing mandatory validation.
        if not self.custom_nationality:
            self.custom_nationality = _get_home_country()

        self._apply_invitation_visitor_choices()
        normalize_visitor_pass(self)
        self._align_workflow_lane_with_visitor_type()
        validate_multi_day_range(self)
        self._validate_schedule()
        self._validate_formats()
        self._validate_host_active()
        self._validate_duplicate_pass()
        self._validate_visit_duration()

    # ─────────────────────────────────────────────────────────
    # VISITOR INVITATION → PASS (per-visitor hospitality)
    # ─────────────────────────────────────────────────────────
    def _invitation_visitor_row(self):
        """This visitor's row in the linked invitation's Visitors table: the row already
        linked to this pass, else the one with the same email, else the same name."""
        if not self.visitor_invitation:
            return None
        rows = frappe.get_all(
            "Visitor Invitation Guest",
            filters={"parent": self.visitor_invitation, "parenttype": "Visitor Invitation"},
            fields=["name", "visitor_full_name", "visitor_email", "visitor_pass", *INVITATION_GUEST_PASS_FIELDS],
            order_by="idx asc",
        )
        mine = [r for r in rows if self.name and r.visitor_pass == self.name]
        if mine:
            return mine[0]
        free = [r for r in rows if not r.visitor_pass]
        email = (self.email_id or "").strip().lower()
        name = (self.visitor_full_name or "").strip().lower()
        for r in free:
            if email and (r.visitor_email or "").strip().lower() == email:
                return r
        for r in free:
            if name and (r.visitor_full_name or "").strip().lower() == name:
                return r
        return None

    def _apply_invitation_visitor_choices(self):
        """Meal / Cab / Factory Tour are chosen by the host on the invitation — however the
        pass is created (visitor's link or desk), it carries that visitor's choices."""
        row = self._invitation_visitor_row()
        self.flags.invitation_visitor_row = row.name if row else None
        if not row:
            return
        for fieldname in INVITATION_GUEST_PASS_FIELDS:
            self.set(fieldname, cint(row.get(fieldname)))

    def _link_invitation_visitor_row(self):
        row_name = self.flags.get("invitation_visitor_row")
        if not row_name or frappe.db.get_value("Visitor Invitation Guest", row_name, "visitor_pass") == self.name:
            return
        from visitormanagement.visitor_management.doctype.visitor_invitation.visitor_invitation import (
            refresh_invitation_status,
        )

        frappe.db.set_value(
            "Visitor Invitation Guest",
            row_name,
            {
                "visitor_pass": self.name,
                "invitation_status": "Saved" if self.docstatus == 0 and self.request_channel == "Portal" else "Submitted",
                "form_submitted_on": now_datetime(),
            },
            update_modified=False,
        )
        refresh_invitation_status(frappe.get_doc("Visitor Invitation", self.visitor_invitation))

    # ─────────────────────────────────────────────────────────
    # BUSINESS VALIDATIONS
    # ─────────────────────────────────────────────────────────
    def _validate_schedule(self):
        """Block past dates, enforce check-in < check-out, enforce future-date ceiling."""
        if not self.visit_date:
            return

        today_date = getdate(today())
        visit_date = getdate(self.visit_date)

        # Past date blocked — allow only if the pass is already checked-in/out (historical edits OK).
        # A multi-day visit stays valid until its end date, so judge it by that.
        if get_visit_end_date(self) < today_date and self.status in (None, "", "Draft", "Approved"):
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

        if self.id_proof_type and self.custom_nationality and self.custom_nationality != _get_home_country():
            if self.id_proof_type != "Passport":
                frappe.throw(
                    _("Foreign national visitors must use Passport as the ID Proof Type."),
                    title=_("Invalid ID Proof Type"),
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
        """Same visitor (by ID proof) cannot have multiple active passes on the same date.
        Multi-day passes cover every day of their range, so ranges must not overlap."""
        if not self.id_proof_number or not self.visit_date:
            return
        existing = frappe.db.sql(
            """
            SELECT name FROM `tabVisitor Pass`
            WHERE id_proof_number = %(id)s
              AND visit_date <= %(end)s
              AND (CASE WHEN multi_day_pass = 1 AND pass_valid_until IS NOT NULL
                        THEN pass_valid_until ELSE visit_date END) >= %(start)s
              AND name != %(self_name)s
              AND docstatus < 2
              AND status NOT IN ('Cancelled', 'Rejected')
            LIMIT 1
            """,
            {
                "id": self.id_proof_number,
                "start": self.visit_date,
                "end": get_visit_end_date(self),
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

    def _pending_lanes_for_type(self):
        """The pending lane(s) a pass of this visitor type may occupy, derived
        from the type's approver_role (+ secondary_approver_role). Data-driven,
        so custom Visitor Types route correctly without a hardcoded map."""
        visitor_type_doc = _get_visitor_type_doc(self.visitor_type)
        if not visitor_type_doc:
            return ()
        lanes = []
        primary = ROLE_TO_PENDING_LANE.get(getattr(visitor_type_doc, "approver_role", None))
        if primary:
            lanes.append(primary)
        secondary = ROLE_TO_PENDING_LANE.get(getattr(visitor_type_doc, "secondary_approver_role", None))
        if secondary and secondary not in lanes:
            lanes.append(secondary)
        return tuple(lanes)

    def _align_workflow_lane_with_visitor_type(self):
        if not self.visitor_type or not self.workflow_state:
            return

        if self.workflow_state not in ALL_PENDING_LANES:
            return

        allowed_lanes = self._pending_lanes_for_type()
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

        # Auto-set badge colour from the linked Visitor Type (or defaults)
        if self.visitor_type:
            visitor_type_doc = _get_visitor_type_doc(self.visitor_type)
            colour = getattr(visitor_type_doc, "badge_colour", None)
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
                    f"<li><b>ID Proof:</b> {self.id_proof_type} — {mask_id_number(self.id_proof_number)}</li>"
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
        user_names = frappe.get_all(
            "Has Role",
            filters={"role": ["in", ["Security", "System Manager"]], "parenttype": "User"},
            pluck="parent",
            distinct=True,
        )
        if not user_names:
            return []
        # Single bulk query for all emails instead of one get_value() per user
        # (avoids an N+1 round-trip per security/admin user on every alert).
        emails = frappe.get_all(
            "User",
            filters={"name": ["in", user_names]},
            pluck="email",
        )
        return list({e for e in emails if e and e != "Administrator" and "@" in e})

    def _normalize_mobile_number(self):
        if not self.mobile_number:
            return
        raw = str(self.mobile_number).strip()

        if self.custom_nationality and self.custom_nationality != _get_home_country():
            # Foreign national — the number already carries its own country's
            # ISD prefix from the Phone widget; don't force Indian formatting.
            return

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
        self._link_invitation_visitor_row()
        if self.docstatus == 0 and self.status == "Draft":
            return
        # Walk-in visitors don't get hospitality — pass is auto-generated
        # from Walk In Visitor Request, no meal/cab/hotel arrangements.
        if self.request_channel == "Walk-In":
            return
        ensure_hospitality_request(self)

    # ─────────────────────────────────────────────────────────
    # BEFORE SUBMIT
    # ─────────────────────────────────────────────────────────
    def before_submit(self):
        # 0️⃣.5 OPTIONAL ITEM DECLARATION (enforced via VMS Settings)
        settings = frappe.get_cached_doc("VMS Settings")
        if settings.get("require_item_declaration") and not self.visitor_items:
            frappe.throw(
                _("Item declaration is required — add at least one item row before submitting."),
                title=_("Items Not Declared"),
            )

        # 1️⃣ BLACKLIST CHECK
        # Match by ID proof number first; fall back to visitor_name + id_proof_type so
        # name-only blacklist entries (created when admin didn't have the number) also block.
        from visitormanagement.visitor_management.doctype.visitor_blacklist.visitor_blacklist import VisitorBlacklist
        blacklist_name = VisitorBlacklist.find_active_match(
            id_proof_number=self.id_proof_number,
            visitor_name=self.visitor_full_name,
            id_proof_type=self.id_proof_type,
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

        # 4️⃣ Foreign National Document Check
        home_country = _get_home_country()
        if self.custom_nationality and self.custom_nationality != home_country:
            if not self.custom_visa_copy:
                frappe.throw(
                    _("Visa Copy is required for foreign national visitors."),
                    title=_("Missing Travel Documents"),
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

        # Get prefix from the linked Visitor Type or use defaults
        visitor_type_doc = _get_visitor_type_doc(self.visitor_type)
        p = getattr(visitor_type_doc, "badge_prefix", None) or {
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
        # Match keys used in visitor_gate.py (scan_qr_checkin), which identifies
        # the pass by PASS / VISITOR+VISIT_DATE only. We deliberately do NOT embed
        # the ID proof number or host in the QR: the QR image is emailed and could
        # be intercepted/leaked, and the gate re-derives those values from the DB.
        qr_data = (
            f"PASS:{self.name}"
            f"|VISITOR:{self.visitor_full_name}"
            f"|VISIT_DATE:{self.visit_date}"
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
            # Private: the QR is attached to the pass (staff with read access can
            # still view it) but is no longer world-readable in /files. It is
            # emailed to the visitor as an attachment, so this does not break delivery.
            "is_private": 1,
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
            time_value = f"{format_time_hhmm(self.expected_checkin)} &ndash; {format_time_hhmm(self.expected_checkout)}"

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
    # frappe.get_list (unlike raw SQL / get_all) enforces the Visitor Pass
    # row-level permission model (visitormanagement/permissions.py), so a caller
    # can only find passes they are authorised to read — no cross-visitor PII scan.
    return frappe.get_list(
        "Visitor Pass",
        filters={"mobile_number": phone},
        fields=["name", "visitor_full_name", "visitor_type"],
        order_by="creation desc",
        limit=10,
    )

@frappe.whitelist()
def search_existing_by_id(id_number):
    if not id_number:
        return []
    return frappe.get_list(
        "Visitor Pass",
        filters={"id_proof_number": id_number},
        fields=["name", "visitor_full_name", "visitor_type"],
        order_by="creation desc",
        limit=10,
    )


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

    # Authorisation: require doctype read, then constrain rows to the caller's
    # Visitor Pass permission scope so this cannot enumerate other visitors' PII.
    frappe.has_permission("Visitor Pass", "read", throw=True)
    from visitormanagement.permissions import get_visitor_pass_permission_query_conditions

    _perm = get_visitor_pass_permission_query_conditions()
    perm_filter = f" AND ({_perm})" if _perm else ""

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
            """
            SELECT name, visitor_full_name, visitor_type, mobile_number, id_proof_number
            FROM `tabVisitor Pass`
            WHERE id_proof_number = %(id_proof_number)s
            """
            + type_filter
            + exclude_filter
            + perm_filter
            + """
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
            """
            SELECT name, visitor_full_name, visitor_type, mobile_number, id_proof_number
            FROM `tabVisitor Pass`
            WHERE ifnull(mobile_number, '') != ''
            """
            + type_filter
            + exclude_filter
            + perm_filter
            + """
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

    # Mask ID proof numbers before returning to client
    for m in matches:
        if m.get("id_proof_number"):
            m["id_proof_number"] = mask_id_number(m["id_proof_number"])

    return {"best_match": matches[0] if matches else None, "matches": matches}


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def search_visitor_passes(doctype, txt, searchfield, start, page_len, filters):
    frappe.has_permission("Visitor Pass", "read", throw=True)
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
    # Constrain rows to the caller's Visitor Pass permission scope.
    from visitormanagement.permissions import get_visitor_pass_permission_query_conditions

    _perm = get_visitor_pass_permission_query_conditions()
    if _perm:
        where += f" AND ({_perm})"
    # Compute the dropdown description live (visitor_full_name · mobile_number) so
    # reception can spot + search by phone, regardless of how stale the cached
    # `visitor_summary` is on legacy/demo records.
    # Compute the dropdown description live (visitor_full_name · mobile_number) so reception
    # can spot + search by phone, regardless of how stale the cached visitor_summary is.

    return frappe.db.sql(
        """SELECT
                name,
                CONCAT_WS(' · ', NULLIF(visitor_full_name, ''), NULLIF(mobile_number, '')) AS description
            FROM `tabVisitor Pass`
            WHERE """
        + where
        + """
            ORDER BY modified DESC
            LIMIT %s OFFSET %s""",
        params + [int(page_len), int(start)],
    )


@frappe.whitelist()
def get_existing_visitor_pass_details(visitor_pass, visitor_type=None):
    if not visitor_pass:
        frappe.throw("Visitor Pass is required.")

    doc = frappe.get_doc("Visitor Pass", visitor_pass)
    # IDOR guard: this returns ID proof number/scan + photo. Enforce the app's
    # own row-level access model (the same model used by the search endpoints and
    # list views) so a user cannot fetch a pass outside their scope. We use the
    # app's hook rather than doc.check_permission() so this stays consistent with
    # the search results and is not skewed by deployment-specific User Permissions.
    from visitormanagement.permissions import has_visitor_pass_permission

    if frappe.session.user != "Administrator" and not has_visitor_pass_permission(
        doc, frappe.session.user, "read"
    ):
        raise frappe.PermissionError("You are not permitted to access this Visitor Pass.")
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


# ─────────────────────────────────────────────────────────
# VISIT TIME EXTENSION (host only → Security notified)
# ─────────────────────────────────────────────────────────
EXTENDABLE_STATUSES = ("Approved", "Items Verified", "Checked-In")


def _is_pass_host(doc, user=None):
    """True when ``user`` (default: session user) is the pass's Person to Visit."""
    if not doc.person_to_visit:
        return False
    host_user = frappe.db.get_value("Employee", doc.person_to_visit, "user_id")
    return bool(host_user) and host_user == (user or frappe.session.user)


@frappe.whitelist()
def can_extend_visit_time(visitor_pass):
    doc = frappe.get_doc("Visitor Pass", visitor_pass)
    return doc.docstatus == 1 and doc.status in EXTENDABLE_STATUSES and _is_pass_host(doc)


@frappe.whitelist()
def extend_visit_time(visitor_pass, new_checkout, reason):
    """Push the pass's Expected Check-Out later. Only the host (Person to Visit) may do
    this; every extension is kept in Time Extensions and Security is notified."""
    doc = frappe.get_doc("Visitor Pass", visitor_pass)

    if not _is_pass_host(doc):
        frappe.throw(
            _("Only the host ({0}) can extend this visit.").format(doc.person_to_visit or "-"),
            frappe.PermissionError,
        )
    if doc.docstatus != 1 or doc.status not in EXTENDABLE_STATUSES:
        frappe.throw(
            _("Only an approved pass that has not checked out can be extended. Current status: {0}").format(
                doc.status
            ),
            title=_("Cannot Extend"),
        )

    reason = (reason or "").strip()
    if not reason:
        frappe.throw(_("Enter the reason for extending the visit."), title=_("Reason Required"))
    if not new_checkout:
        frappe.throw(_("Enter the new Expected Check-Out time."), title=_("Time Required"))

    new_checkout = strip_seconds(new_checkout)
    previous_checkout = doc.expected_checkout
    if previous_checkout and get_time(new_checkout) <= get_time(previous_checkout):
        frappe.throw(
            _("New Check-Out ({0}) must be later than the current Expected Check-Out ({1}).").format(
                format_time_hhmm(new_checkout), format_time_hhmm(previous_checkout)
            ),
            title=_("Invalid Time"),
        )

    row = doc.append(
        "time_extensions",
        {
            "previous_checkout": previous_checkout,
            "new_checkout": new_checkout,
            "reason": reason,
            "extended_by": frappe.session.user,
            "extended_on": now_datetime(),
        },
    )
    row.db_insert()
    doc.db_set("expected_checkout", new_checkout)
    doc.add_comment(
        "Info",
        _("Visit extended: Expected Check-Out {0} → {1}. Reason: {2}").format(
            format_time_hhmm(previous_checkout, "-"), format_time_hhmm(new_checkout), frappe.utils.escape_html(reason)
        ),
    )

    log_visitor_event(
        doc.name,
        "Visit Extended",
        event_status="Recorded",
        source_doctype="Visit Time Extension",
        source_name=row.name,
        details={
            "previous_checkout": str(previous_checkout or ""),
            "new_checkout": new_checkout,
            "reason": reason,
            "extended_by": frappe.session.user,
        },
    )
    _notify_security_of_extension(doc, previous_checkout, new_checkout, reason)

    return {"expected_checkout": new_checkout}


def _notify_security_of_extension(doc, previous_checkout, new_checkout, reason):
    """Bell notification + email to every enabled Security user."""
    from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification

    security_users = frappe.get_all(
        "Has Role",
        filters={"role": "Security", "parenttype": "User"},
        pluck="parent",
        distinct=True,
    )
    users = frappe.get_all(
        "User",
        filters={"name": ["in", security_users or [""]], "enabled": 1, "user_type": "System User"},
        pluck="name",
    )
    users = [u for u in users if u not in ("Administrator", "Guest")]
    if not users:
        return

    esc = frappe.utils.escape_html
    host_name = frappe.db.get_value("Employee", doc.person_to_visit, "employee_name") or doc.person_to_visit
    subject = _("Visit extended: {0} ({1}) until {2}").format(
        esc(doc.visitor_full_name or ""), doc.name, format_time_hhmm(new_checkout)
    )
    message = (
        f"<p>The host has extended this visit. Allow the visitor to stay until the new time.</p>"
        f"<ul>"
        f"<li><b>Visitor:</b> {esc(doc.visitor_full_name or '-')} ({doc.name})</li>"
        f"<li><b>Host:</b> {esc(host_name or '-')}</li>"
        f"<li><b>Expected Check-Out:</b> {format_time_hhmm(previous_checkout, '-')} → "
        f"<b>{format_time_hhmm(new_checkout)}</b></li>"
        f"<li><b>Reason:</b> {esc(reason)}</li>"
        f"</ul>"
    )
    enqueue_create_notification(
        users,
        {
            "type": "Alert",
            "document_type": "Visitor Pass",
            "document_name": doc.name,
            "subject": subject,
            "email_content": message,
            "from_user": frappe.session.user,
        },
    )


# ─────────────────────────────────────────────────────────
# GROUP (INVITATION) APPROVAL
# ─────────────────────────────────────────────────────────
# Every visitor on a Visitor Invitation gets their own pass. An approver can move
# all of the group's passes that sit in the same workflow stage in one click — each
# pass still goes through the normal workflow (roles + conditions are re-checked).
GROUP_WORKFLOW_ACTIONS = ("Submit", "Approve")


def _group_siblings(doc):
    if not doc.visitor_invitation:
        return []
    return frappe.get_all(
        "Visitor Pass",
        filters={
            "visitor_invitation": doc.visitor_invitation,
            "workflow_state": doc.workflow_state,
            "docstatus": doc.docstatus,
        },
        pluck="name",
        order_by="creation asc",
    )


def _allowed_group_action(doc, action):
    from frappe.model.workflow import get_transitions

    try:
        return any(t.action == action for t in get_transitions(doc))
    except Exception:
        return False


@frappe.whitelist()
def get_group_workflow_actions(visitor_pass):
    """Actions the current user may apply to every pass of this pass's invitation."""
    doc = frappe.get_doc("Visitor Pass", visitor_pass)
    siblings = _group_siblings(doc)
    if len(siblings) < 2:
        return {"count": len(siblings), "actions": []}
    actions = [a for a in GROUP_WORKFLOW_ACTIONS if _allowed_group_action(doc, a)]
    return {"count": len(siblings), "actions": actions}


@frappe.whitelist()
def apply_group_workflow_action(visitor_pass, action):
    from frappe.model.workflow import apply_workflow

    if action not in GROUP_WORKFLOW_ACTIONS:
        frappe.throw(_("Action {0} cannot be applied to a group.").format(action))

    doc = frappe.get_doc("Visitor Pass", visitor_pass)
    done, skipped = [], []
    for name in _group_siblings(doc):
        sibling = frappe.get_doc("Visitor Pass", name)
        if not _allowed_group_action(sibling, action):
            skipped.append({"name": name, "reason": _("Action not available")})
            continue
        # Savepoint per pass so one failure doesn't leave a half-saved pass behind.
        frappe.db.savepoint("group_workflow")
        try:
            apply_workflow(sibling, action)
            done.append(name)
        except Exception as e:
            frappe.db.rollback(save_point="group_workflow")
            frappe.clear_messages()
            skipped.append({"name": name, "reason": frappe.utils.strip_html(str(e))[:200]})
    return {"done": done, "skipped": skipped}


@frappe.whitelist()
def get_invitation_visitors(visitor_invitation, visitor_pass=None):
    """Shared visit details of an invitation + its visitors who don't have a pass yet,
    so a pass created in the desk can be filled from the host's invitation."""
    inv = frappe.get_doc("Visitor Invitation", visitor_invitation)
    inv.check_permission("read")
    shared = {
        f: inv.get(f)
        for f in (
            "visitor_type", "host_employee", "visit_date", "multi_day_pass", "pass_valid_until",
            "expected_checkin", "expected_checkout", "purpose_of_visit",
        )
    }
    visitors = [
        {
            "row": r.name,
            "visitor_full_name": r.visitor_full_name,
            "visitor_email": r.visitor_email,
            "visitor_mobile": r.visitor_mobile,
            **{f: cint(r.get(f)) for f in INVITATION_GUEST_PASS_FIELDS},
        }
        for r in inv.visitors
        if not r.visitor_pass or r.visitor_pass == visitor_pass
    ]
    return {"shared": shared, "visitors": visitors}
