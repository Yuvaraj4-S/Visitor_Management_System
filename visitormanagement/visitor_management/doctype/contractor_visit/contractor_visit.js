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
    }
});
