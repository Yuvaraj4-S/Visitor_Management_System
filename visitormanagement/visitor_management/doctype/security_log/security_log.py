# Copyright (c) 2026, Harthesh and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime, time_diff_in_seconds

from visitormanagement.visitor_management.lifecycle import (
    derive_health_screening_status,
    log_visitor_event,
    sync_contact_trace,
    sync_compliance_check,
    sync_health_screening,
)


class SecurityLog(Document):

    def before_save(self):

        # Fetch Visitor Pass once
        vp = None
        if self.visitor_pass:
            vp = frappe.get_doc('Visitor Pass', self.visitor_pass)

        # 1. Auto-fetch visitor info and ID details
        if vp:
            # Generate badge number if missing
            if not vp.badge_number:
                vp.generate_badge_number()
                vp.reload()
            
            if not self.badge_number:
                self.badge_number = vp.badge_number
            if not self.visitor_name:
                self.visitor_name = vp.visitor_full_name
            if not self.id_proof_number:
                self.id_proof_number = vp.id_proof_number
            if not self.visitor_photo:
                self.visitor_photo = vp.visitor_photo
            if not self.id_proof_scan:
                self.id_proof_scan = vp.id_proof_scan
            
            # Auto-calculate last 4 digits if ID number is present
            if self.id_proof_number and not self.id_last_4_digits:
                id_str = str(self.id_proof_number).strip()
                if len(id_str) >= 4:
                    self.id_last_4_digits = id_str[-4:]
                else:
                    self.id_last_4_digits = id_str

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
                if current_status == 'Checked-In':
                    frappe.throw(f"Visitor {self.visitor_name} is already Checked-In.")
                if current_status == 'Checked-Out':
                    frappe.throw(f"Visitor {self.visitor_name} has already Checked-Out and the pass is now inactive.")
                
                if not self.check_in_date_time:
                    self.check_in_date_time = now

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
            if self.manual_override and not self.exception_reason:
                frappe.throw("Provide an exception reason when manual override is used.")

            if not self.photo_at_gate and not self.manual_override:
                frappe.throw("Capture a live gate photo before saving the visitor check-in.")

            if not self.id_proof_match and not self.manual_override:
                frappe.throw("Confirm that the visitor matches the ID proof before saving the visitor check-in.")

            if not self.pass_photo_match and not self.manual_override:
                frappe.throw("Confirm that the visitor matches the pass creation photo before saving the visitor check-in.")

            if self.manual_override and not self.alert_level:
                self.alert_level = "Medium"

            health_status = derive_health_screening_status(
                temperature=self.temperature,
                symptoms_flag=self.symptoms_flag,
                alert_level=self.alert_level,
            )
            if health_status == "Denied Entry" and not self.manual_override:
                frappe.throw(
                    "Health screening failed due to the recorded temperature. Use manual override with an exception reason if entry is still required."
                )

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
                    'item_image': vi.get('item_image')
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

        elif self.event_type == 'Check-Out':
            frappe.db.set_value(
                'Visitor Pass',
                self.visitor_pass,
                {
                    'status': 'Checked-Out',
                    'actual_checkout': self.check_out_date_time or now_datetime(),
                },
            )

        self._record_lifecycle_event()
        sync_health_screening(self.visitor_pass, self)
        sync_contact_trace(self.visitor_pass, self)
        sync_compliance_check(self.visitor_pass, self)

    # --------------------------------------------------

    def on_update(self):
        if self.event_type == 'Check-In' and self.visitor_pass:
            self._sync_gate_verification()
            self._sync_item_verification()

        if self.visitor_pass:
            self._record_lifecycle_event()
            sync_health_screening(self.visitor_pass, self)
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

    def _record_lifecycle_event(self):
        if not self.visitor_pass:
            return

        log_visitor_event(
            self.visitor_pass,
            self.event_type,
            event_status=self.alert_level or "Recorded",
            source_doctype=self.doctype,
            source_name=self.name,
            details={
                "gate_name": self.gate_name,
                "visited_area": self.visited_area,
                "security_officer": self.security_officer,
                "manual_override": self.manual_override,
                "exception_reason": self.exception_reason,
                "health_screening_status": self.health_screening_status,
            },
        )
