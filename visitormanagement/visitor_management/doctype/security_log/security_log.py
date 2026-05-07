# Copyright (c) 2026, Harthesh and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime, getdate, now_datetime, time_diff_in_seconds

import re

from visitormanagement.visitor_management.lifecycle import (
    log_visitor_event,
    sync_contact_trace,
    sync_compliance_check,
)


def mask_id_number(raw):
    """Mask ID proof number, preserving separators and showing only last 4 characters.

    Aadhaar  5001-5002-5003  →  XXXX-XXXX-5003
    PAN      AABPR2345T     →  XXXXXX345T
    Passport P1234567       →  XXXX4567
    DL       DL-TN-05210099 →  XX-XX-XXXX0099
    """
    # Extract only alphanumeric characters and their positions
    chars = []
    for i, ch in enumerate(raw):
        if ch.isalnum():
            chars.append((i, ch))

    if len(chars) <= 4:
        return raw

    # Positions of characters to keep visible (last 4 alphanumeric)
    visible_positions = {pos for pos, _ in chars[-4:]}

    # Rebuild string: mask alphanumeric chars except last 4, keep separators
    masked = []
    for i, ch in enumerate(raw):
        if ch.isalnum():
            masked.append(ch if i in visible_positions else "X")
        else:
            masked.append(ch)  # keep hyphens, spaces, slashes as-is
    return "".join(masked)


def _get_employee_email(employee_name):
    if not employee_name:
        return None

    employee = frappe.db.get_value(
        "Employee",
        employee_name,
        ["company_email", "personal_email", "user_id"],
        as_dict=True,
    )
    if not employee:
        return None

    return employee.company_email or employee.personal_email or employee.user_id


def _send_host_checkin_email(visitor_pass, security_log):
    host_email = _get_employee_email(visitor_pass.person_to_visit)
    if not host_email:
        return

    items_summary = "No items declared."
    if visitor_pass.visitor_items:
        item_lines = []
        for item in visitor_pass.visitor_items:
            line = item.item_name
            if item.quantity:
                line = f"{line} | Qty: {item.quantity}"
            if item.serial_number:
                line = f"{line} | S/N: {item.serial_number}"
            item_lines.append(line)
        items_summary = "<br>".join(item_lines)

    try:
        frappe.sendmail(
            recipients=[host_email],
            subject=f"Visitor Arrived: {visitor_pass.visitor_full_name}",
            message=(
                f"<p>Visitor <b>{visitor_pass.visitor_full_name}</b> has checked in.</p>"
                "<table style='border-collapse: collapse;'>"
                f"<tr><td style='padding:4px 8px;'><b>Pass ID</b></td><td style='padding:4px 8px;'>{visitor_pass.name}</td></tr>"
                f"<tr><td style='padding:4px 8px;'><b>Visitor Type</b></td><td style='padding:4px 8px;'>{visitor_pass.visitor_type or '-'}</td></tr>"
                f"<tr><td style='padding:4px 8px;'><b>Purpose</b></td><td style='padding:4px 8px;'>{visitor_pass.purpose_of_visit or '-'}</td></tr>"
                f"<tr><td style='padding:4px 8px;'><b>Check-In Time</b></td><td style='padding:4px 8px;'>{security_log.check_in_date_time or now_datetime()}</td></tr>"
                f"<tr><td style='padding:4px 8px;'><b>Gate</b></td><td style='padding:4px 8px;'>{security_log.gate_name or '-'}</td></tr>"
                f"<tr><td style='padding:4px 8px;'><b>Items Declared</b></td><td style='padding:4px 8px;'>{items_summary}</td></tr>"
                "</table>"
            ),
            now=True,
        )
    except Exception as exc:
        # Don't let a missing/misconfigured Email Account block check-in.
        frappe.log_error(f"Host check-in email failed for {security_log.name}: {exc}", "VMS Host Check-in Email")


class SecurityLog(Document):

    def before_save(self):

        # Fetch Visitor Pass once
        vp = None
        if self.visitor_pass:
            vp = frappe.get_doc('Visitor Pass', self.visitor_pass)

        # 0. Re-check blacklist at gate — blacklisting could have happened AFTER pass approval
        if vp and self.event_type == 'Check-In' and vp.id_proof_number:
            blacklist_name = frappe.db.exists(
                'Visitor Blacklist',
                {'id_proof_number': vp.id_proof_number, 'is_active': 1},
            )
            if blacklist_name:
                bl = frappe.get_doc('Visitor Blacklist', blacklist_name)
                frappe.throw(
                    f"<b>ACCESS DENIED AT GATE</b><br>"
                    f"Visitor: {vp.visitor_full_name}<br>"
                    f"ID Proof matches active blacklist entry.<br>"
                    f"Reason: {bl.reason or 'Not specified'}<br>"
                    f"Blocked by: {bl.blocked_by or 'System'}",
                    title='BLACKLISTED VISITOR',
                )

        # 1. Auto-fetch visitor info and ID details
        if vp:
            # VIP badges are issued only during the gate check-in flow.
            if not vp.badge_number and self.event_type == 'Check-In' and vp.visitor_type == 'VIP':
                vp.generate_badge_number()
                vp.reload()
            
            if not self.badge_number:
                self.badge_number = vp.badge_number
            if not self.visitor_name:
                self.visitor_name = vp.visitor_full_name
            if not self.visitor_photo:
                self.visitor_photo = vp.visitor_photo
            if not self.id_proof_scan:
                self.id_proof_scan = vp.id_proof_scan

            # Mask ID proof number — gate security only needs last 4 digits
            if vp.id_proof_number:
                self.id_proof_number = mask_id_number(str(vp.id_proof_number).strip())

        # 2. Auto-assign gate
        if vp and not self.gate_name:
            gate_rules = {
                'VIP': 'VIP Entrance',
                'Supplier': 'Loading Dock',
                'Contractor': 'Back Gate',
                'Candidate': 'Main Gate',
                'Customer': 'Main Gate',
            }
            self.gate_name = gate_rules.get(vp.visitor_type, 'Main Gate')
            self.gate_auto_assigned = 1

        # 3. Auto-stamp datetime and validate status sequence
        now = now_datetime()
        if not self.verification_started_on:
            self.verification_started_on = now

        if vp:
            current_status = vp.status
            if self.event_type == 'Check-In':
                if current_status not in {'Approved', 'Items Verified'}:
                    frappe.throw(
                        f"Visitor {self.visitor_name or vp.visitor_full_name} must be approved before check-in. (Current Status: {current_status})"
                    )
                if current_status == 'Checked-In':
                    frappe.throw(f"Visitor {self.visitor_name} is already Checked-In.")
                if current_status == 'Checked-Out':
                    frappe.throw(f"Visitor {self.visitor_name} has already Checked-Out and the pass is now inactive.")
                
                if not self.check_in_date_time:
                    self.check_in_date_time = now

                # Validate check-in is within reasonable window of expected visit
                if vp.visit_date and self.check_in_date_time:
                    from frappe.utils import getdate, get_datetime as _get_dt
                    checkin_date = getdate(self.check_in_date_time)
                    expected_date = getdate(vp.visit_date)
                    if checkin_date < expected_date:
                        frappe.throw(
                            f"Check-in date ({checkin_date}) is before the scheduled visit date ({expected_date}). Cannot check in early."
                        )
                    if checkin_date > expected_date:
                        frappe.throw(
                            f"Check-in date ({checkin_date}) is after the scheduled visit date ({expected_date}). Pass is no longer valid for this date."
                        )

                if self.verification_started_on and self.check_in_date_time:
                    self.verification_duration = time_diff_in_seconds(
                        self.check_in_date_time,
                        self.verification_started_on,
                    )

            elif self.event_type == 'Check-Out':
                if current_status != 'Checked-In':
                    frappe.throw(f"Visitor {self.visitor_name} must be 'Checked-In' before they can 'Check-Out'. (Current Status: {current_status})")

                if not self.check_out_date_time:
                    self.check_out_date_time = now

                # Check-out must be after check-in
                prior_checkin = frappe.db.get_value(
                    "Security Log",
                    {"visitor_pass": self.visitor_pass, "event_type": "Check-In", "docstatus": ["<", 2]},
                    "check_in_date_time",
                )
                if prior_checkin and get_datetime(self.check_out_date_time) <= get_datetime(prior_checkin):
                    frappe.throw(
                        f"Check-out time ({self.check_out_date_time}) must be after check-in time ({prior_checkin})."
                    )

            elif self.event_type == 'Gate Transfer' and current_status != 'Checked-In':
                frappe.throw(
                    f"Visitor {self.visitor_name} must be 'Checked-In' before a gate transfer can be logged."
                )

        # 4. Auto-set security officer
        if not self.security_officer:
            emp = frappe.db.get_value(
                'Employee',
                {'user_id': frappe.session.user},
                'name'
            )
            if emp:
                self.security_officer = emp

        if self.event_type == 'Check-In':
            if not self.photo_at_gate:
                frappe.throw("Capture a live gate photo before saving the visitor check-in.")

            if not self.id_proof_match:
                frappe.throw("Confirm that the visitor matches the ID proof before saving the visitor check-in.")

            if not self.pass_photo_match:
                frappe.throw("Confirm that the visitor matches the pass creation photo before saving the visitor check-in.")

        if self.event_type == 'Gate Transfer' and not self.visited_area:
            frappe.throw("Visited Area is required for gate transfer tracking.")

        if (
            self.is_new()
            and self.event_type == 'Check-In'
            and vp
            and not self.items_verification
        ):
            # Try to fetch from visitor_items if it exists
            items = vp.get('visitor_items') or []
            for vi in items:
                self.append('items_verification', {
                    'visitor_item_row_name': vi.name,
                    'item_name': vi.item_name,
                    'item_category': vi.item_category,
                    'quantity_declared': vi.quantity,
                    'uom': vi.unit_of_measure,
                    'serial__asset_number': vi.serial_number,
                    'quantity_found': vi.quantity,
                    'item_verified': 0,
                    # item_image is captured by the gate officer at scan time, not copied from Visitor Item.
                })

        for row in (self.items_verification or []):
            if row.quantity_found is not None and row.quantity_declared is not None:
                row.discrepancy = 1 if row.quantity_found != row.quantity_declared else 0

        # 7. Check if all items confirmed
        if self.items_verification:
            all_ok = all(r.item_verified for r in self.items_verification)
            self.all_items_confirmed = 1 if all_ok else 0
        else:
            self.all_items_confirmed = 1

    # --------------------------------------------------

    def after_insert(self):
        if not self.visitor_pass:
            return

        if self.event_type == 'Check-In':
            self._sync_gate_verification()
            self._sync_item_verification()
            # Also update the Pass status to Checked-In
            frappe.db.set_value(
                'Visitor Pass',
                self.visitor_pass,
                {
                    'status': 'Checked-In',
                    'actual_checkin': self.check_in_date_time or now_datetime(),
                    'no_show': 0,
                },
            )
            self._notify_host_arrival()

        elif self.event_type == 'Check-Out':
            frappe.db.set_value(
                'Visitor Pass',
                self.visitor_pass,
                {
                    'status': 'Checked-Out',
                    'actual_checkout': self.check_out_date_time or now_datetime(),
                },
            )

            # Auto-close any pending Evacuation Muster records for this visitor —
            # they've left the premises, so they're no longer at risk.
            pending_musters = frappe.get_all(
                'Evacuation Muster',
                filters={'visitor_pass': self.visitor_pass, 'accounted_status': ['in', ['Pending', 'Missing']]},
                pluck='name',
            )
            for muster_name in pending_musters:
                frappe.db.set_value(
                    'Evacuation Muster', muster_name,
                    {
                        'accounted_status': 'Excused',
                        'accounted_time': now_datetime(),
                        'notes': (frappe.db.get_value('Evacuation Muster', muster_name, 'notes') or '') +
                                 f"\nAuto-closed: visitor checked out at {now_datetime()}.",
                    },
                )

        self._record_lifecycle_event()
        sync_contact_trace(self.visitor_pass, self)
        sync_compliance_check(self.visitor_pass, self)

    # --------------------------------------------------

    def on_update(self):
        if self.event_type == 'Check-In' and self.visitor_pass:
            if not self.photo_at_gate:
                vp_photo = frappe.db.get_value('Visitor Pass', self.visitor_pass, 'visitor_photo')
                if vp_photo:
                    self.photo_at_gate = vp_photo
            self._sync_gate_verification()
            self._sync_item_verification()

        if self.visitor_pass:
            self._record_lifecycle_event()
            sync_contact_trace(self.visitor_pass, self)
            sync_compliance_check(self.visitor_pass, self)

    # --------------------------------------------------

    def _sync_gate_verification(self):
        if not self.visitor_pass or not self.photo_at_gate:
            return

        values = {
            'gate_verified_photo': self.photo_at_gate,
            'gate_verified_on': self.check_in_date_time or now_datetime(),
        }

        if self.security_officer:
            values['gate_verified_by'] = self.security_officer

        visitor_type = frappe.db.get_value('Visitor Pass', self.visitor_pass, 'visitor_type')
        if visitor_type == 'VIP':
            values['visitor_photo'] = self.photo_at_gate

        frappe.db.set_value('Visitor Pass', self.visitor_pass, values)

    # --------------------------------------------------

    def _sync_item_verification(self):

        vp = frappe.get_doc('Visitor Pass', self.visitor_pass)

        total_items = len(self.items_verification or [])
        verified_count = sum(
            1 for r in (self.items_verification or [])
            if r.item_verified
        )

        if total_items == 0 or verified_count == total_items:
            new_status = 'All Verified'
            items_verified_flag = 1
        elif verified_count > 0:
            new_status = 'Partial'
            items_verified_flag = 0
        else:
            new_status = 'Pending'
            items_verified_flag = 0

        # Update Visitor Item rows
        for row in (self.items_verification or []):
            if row.visitor_item_row_name:
                verification_remarks = row.security_remarks
                if not verification_remarks:
                    verification_remarks = (
                        'Verified at gate'
                        if row.item_verified
                        else 'Pending security verification'
                    )

                frappe.db.set_value(
                    'Visitor Item',
                    row.visitor_item_row_name,
                    {
                        'verified_by_security': row.item_verified,
                        'verification_remarks': verification_remarks,
                    }
                )

        # Update Visitor Pass
        frappe.db.set_value(
            'Visitor Pass',
            self.visitor_pass,
            {
                'item_verification_status': new_status,
                'items_verified': items_verified_flag,
            }
        )

        # Generate badge only if fully verified
        if items_verified_flag and not vp.badge_number:
            vp.reload()
            vp.generate_badge_number()
            frappe.msgprint(
                f'All items verified! Badge issued: {vp.badge_number}',
                alert=True,
                indicator='green'
            )

    def _notify_host_arrival(self):
        if self.event_type != 'Check-In' or not self.visitor_pass:
            return

        visitor_pass = frappe.get_doc('Visitor Pass', self.visitor_pass)
        _send_host_checkin_email(visitor_pass, self)

    def _record_lifecycle_event(self):
        if not self.visitor_pass:
            return

        log_visitor_event(
            self.visitor_pass,
            self.event_type,
            event_status="Recorded",
            source_doctype=self.doctype,
            source_name=self.name,
            details={
                "gate_name": self.gate_name,
                "visited_area": self.visited_area,
                "security_officer": self.security_officer,
                "exception_reason": self.exception_reason,
            },
        )


@frappe.whitelist()
def get_approved_vip_queue(visit_date=None):
	target_date = getdate(visit_date) if visit_date else getdate()
	return frappe.get_all(
		"Visitor Pass",
		filters={
			"visitor_type": "VIP",
			"visit_date": target_date,
			"status": ["in", ["Approved", "Items Verified", "Checked-In"]],
		},
		fields=[
			"name",
			"visitor_full_name",
			"company__organisation",
			"visit_date",
			"expected_checkin",
			"expected_checkout",
			"person_to_visit",
			"purpose_of_visit",
			"status",
			"workflow_state",
			"mdceo_notified",
			"conference_room",
			"meal_type",
			"number_of_people",
			"protocol_notes",
		],
		order_by="expected_checkin asc, modified asc",
	)
