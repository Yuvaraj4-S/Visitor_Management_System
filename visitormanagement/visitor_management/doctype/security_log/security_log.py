# Copyright (c) 2026, Harthesh and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


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
        if vp:
            current_status = vp.status
            if self.event_type == 'Check-In':
                if current_status == 'Checked-In':
                    frappe.throw(f"Visitor {self.visitor_name} is already Checked-In.")
                if current_status == 'Checked-Out':
                    frappe.throw(f"Visitor {self.visitor_name} has already Checked-Out and the pass is now inactive.")
                
                if not self.check_in_date_time:
                    self.check_in_date_time = now
                    
            elif self.event_type == 'Check-Out':
                if current_status != 'Checked-In':
                    frappe.throw(f"Visitor {self.visitor_name} must be 'Checked-In' before they can 'Check-Out'. (Current Status: {current_status})")
                
                if not self.check_out_date_time:
                    self.check_out_date_time = now

        # 4. Auto-set security officer
        if not self.security_officer:
            emp = frappe.db.get_value(
                'Employee',
                {'user_id': frappe.session.user},
                'name'
            )
            if emp:
                self.security_officer = emp

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
            self._sync_item_verification()
            # Also update the Pass status to Checked-In
            frappe.db.set_value(
                'Visitor Pass',
                self.visitor_pass,
                'status',
                'Checked-In'
            )

        elif self.event_type == 'Check-Out':
            frappe.db.set_value(
                'Visitor Pass',
                self.visitor_pass,
                'status',
                'Checked-Out'
            )

    # --------------------------------------------------

    def on_update(self):
        if self.event_type == 'Check-In' and self.visitor_pass:
            self._sync_item_verification()

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
                frappe.db.set_value(
                    'Visitor Item',
                    row.visitor_item_row_name,
                    {
                        'verified_by_security': row.item_verified,
                        'verification_remarks': row.security_remarks,
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