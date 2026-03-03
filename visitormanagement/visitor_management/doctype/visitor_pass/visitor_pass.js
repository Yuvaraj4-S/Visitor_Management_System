// Copyright (c) 2026, Harthesh and contributors
// For license information, please see license.txt

frappe.ui.form.on('Visitor Pass', {
  refresh: function (frm) {
    if (!frm.is_new()) {
      // Add buttons for related Visit forms based on type
      if (frm.doc.visitor_type === 'Contractor') {
        frm.add_custom_button(__('Manage Contractor Details'), function () {
          open_related_visit_form(frm, 'Contractor Visit');
        }, __('Actions'));
      } else if (frm.doc.visitor_type === 'VIP') {
        frm.add_custom_button(__('Manage VIP Details'), function () {
          open_related_visit_form(frm, 'VIP Visit');
        }, __('Actions'));
      } else if (frm.doc.visitor_type === 'Supplier') {
        frm.add_custom_button(__('Manage Supplier Details'), function () {
          open_related_visit_form(frm, 'Supplier Visit');
        }, __('Actions'));
      } else if (frm.doc.visitor_type === 'Candidate') {
        frm.add_custom_button(__('Manage Candidate Details'), function () {
          open_related_visit_form(frm, 'Candidate Visit');
        }, __('Actions'));
      }
    }
  }
});

function open_related_visit_form(frm, doctype) {
  frappe.db.get_value(doctype, { visitor_pass: frm.doc.name }, 'name', (r) => {
    if (r && r.name) {
      frappe.set_route('Form', doctype, r.name);
    } else {
      // Create new linked visit record
      frappe.model.with_doctype(doctype, function () {
        let new_doc = frappe.model.get_new_doc(doctype);
        new_doc.visitor_pass = frm.doc.name;

        // Pre-fill common fields
        if (doctype === 'Contractor Visit') {
          new_doc.contractor_company = frm.doc.company__organisation;
          new_doc.worker_name = frm.doc.visitor_full_name;
        } else if (doctype === 'VIP Visit') {
          new_doc.personal_escort = frm.doc.person_to_visit;
        } else if (doctype === 'Supplier Visit') {
          new_doc.supplier = frm.doc.company__organisation;
        } else if (doctype === 'Candidate Visit') {
          new_doc.candidate_name = frm.doc.visitor_full_name;
        }

        frappe.set_route('Form', doctype, new_doc.name);
      });
    }
  });
}