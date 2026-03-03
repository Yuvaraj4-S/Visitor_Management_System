// Copyright (c) 2026, Harthesh and contributors
// For license information, please see license.txt

frappe.ui.form.on('HR Interview', {
    refresh: function (frm) {
        if (frm.doc.interview_type === 'Offline' && !frm.doc.visitor_pass) {
            frm.add_custom_button(__('Create Visitor Pass'), function () {
                frappe.model.with_doctype('Visitor Pass', function () {
                    let vp = frappe.model.get_new_doc('Visitor Pass');
                    vp.visitor_type = 'Candidate';
                    vp.visitor_full_name = frm.doc.candidate_name;
                    vp.email_id = frm.doc.email_id;
                    vp.mobile_number = frm.doc.mobile_number;
                    vp.visit_date = frm.doc.interview_date;
                    vp.hr_interview = frm.doc.name;
                    vp.purpose_of_visit = 'Interview: ' + frm.doc.name;

                    // Set the route to the new Visitor Pass
                    frappe.set_route('Form', 'Visitor Pass', vp.name);

                    // Small delay to ensure the form is loaded, then link it back if possible
                    // However, in Frappe it's better to save and then link.
                    // For now, just pre-filling is what the user asked ("fetch the data").
                });
            }, __('Actions'));
        }

        if (frm.doc.visitor_pass) {
            frm.add_custom_button(__('View Visitor Pass'), function () {
                frappe.set_route('Form', 'Visitor Pass', frm.doc.visitor_pass);
            }, __('Actions'));
        }
    },

    interview_type: function (frm) {
        frm.trigger('refresh');
    }
});
