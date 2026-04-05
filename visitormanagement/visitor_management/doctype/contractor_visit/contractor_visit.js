// Copyright (c) 2026, Harthesh and contributors
// For license information, please see license.txt

frappe.ui.form.on("Contractor Visit", {
    refresh(frm) {
        // Filter Visitor Pass to only show Contractors
        frm.set_query("visitor_pass", function () {
            return {
                filters: {
                    "visitor_type": "Contractor"
                }
            };
        });

        apply_contractor_visit_ui(frm);
    },

    nda_signed(frm) {
        apply_contractor_visit_ui(frm);
    },

    ppe_provided(frm) {
        apply_contractor_visit_ui(frm);
    }
});

function apply_contractor_visit_ui(frm) {
    frm.toggle_display("nda_document", !!frm.doc.nda_signed);
    frm.toggle_reqd("nda_document", !!frm.doc.nda_signed);
    frm.toggle_display("ppe_document", !!frm.doc.ppe_provided);
    frm.toggle_reqd("ppe_document", !!frm.doc.ppe_provided);
}
