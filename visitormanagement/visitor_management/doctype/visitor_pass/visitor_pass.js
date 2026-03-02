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
      }
    }
  },

  visitor_type: function (frm) {
    // Clear linked fields when type changes
    frm.set_value('linked_contractor', '');
    frm.set_value('linked_customer', '');
    frm.set_value('linked_candidate', '');
    frm.set_value('linked_supplier', '');
    frm.set_value('linked_vip', '');
  },

  linked_contractor: function (frm) {
    if (frm.doc.linked_contractor) {
      frappe.db.get_doc('Contractor Visit', frm.doc.linked_contractor).then(doc => {
        frm.set_value('visitor_full_name', doc.worker_name);
        frm.set_value('mobile_number', doc.mobile_number);
        frm.set_value('email_id', doc.email_id);
        frm.set_value('company__organisation', doc.contractor_company);
      });
    }
  },

  linked_vip: function (frm) {
    if (frm.doc.linked_vip) {
      frappe.db.get_doc('VIP Visit', frm.doc.linked_vip).then(doc => {
        frm.set_value('visitor_full_name', doc.vip_name);
        frm.set_value('mobile_number', doc.mobile_number);
        frm.set_value('email_id', doc.email_id);
      });
    }
  },

  linked_customer: function (frm) {
    if (frm.doc.linked_customer) {
      frappe.db.get_value('Customer Visit', frm.doc.linked_customer, ['customer', 'sales_executive'], (r) => {
        if (r.customer) {
          frappe.db.get_value('Customer', r.customer, ['customer_name', 'mobile_no', 'email_id'], (c) => {
            frm.set_value('visitor_full_name', c.customer_name);
            frm.set_value('mobile_number', c.mobile_no);
            frm.set_value('email_id', c.email_id);
            frm.set_value('company__organisation', r.customer);
          });
        }
        if (r.sales_executive) {
          frm.set_value('person_to_visit', r.sales_executive);
        }
      });
    }
  },

  linked_supplier: function (frm) {
    if (frm.doc.linked_supplier) {
      frappe.db.get_value('Supplier Visit', frm.doc.linked_supplier, ['supplier', 'driver_name'], (r) => {
        if (r.supplier) {
          frappe.db.get_value('Supplier', r.supplier, ['supplier_name', 'mobile_no', 'email_id'], (s) => {
            frm.set_value('visitor_full_name', r.driver_name || s.supplier_name);
            frm.set_value('mobile_number', s.mobile_no);
            frm.set_value('email_id', s.email_id);
            frm.set_value('company__organisation', s.supplier_name);
          });
        }
      });
    }
  },

  linked_candidate: function (frm) {
    if (frm.doc.linked_candidate) {
      frappe.db.get_value('Candidate Visit', frm.doc.linked_candidate, 'job_applicant_link', (r) => {
        if (r.job_applicant_link) {
          frappe.db.get_value('Job Applicant', r.job_applicant_link, ['applicant_name', 'email_id', 'phone_number'], (a) => {
            frm.set_value('visitor_full_name', a.applicant_name);
            frm.set_value('mobile_number', a.phone_number);
            frm.set_value('email_id', a.email_id);
          });
        }
      });
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
        }

        frappe.set_route('Form', doctype, new_doc.name);
      });
    }
  });
}