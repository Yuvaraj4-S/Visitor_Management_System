// Copyright (c) 2026, Harthesh and contributors
// For license information, please see license.txt

frappe.ui.form.on("Customer Visit", {
    refresh(frm) {
        frm.set_query("visitor_pass", function () {
            return {
                filters: {
                    "visitor_type": "Customer"
                }
            };
        });
    },
});
