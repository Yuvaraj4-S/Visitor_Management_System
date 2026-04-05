// Copyright (c) 2026, Harthesh and contributors
// For license information, please see license.txt

frappe.ui.form.on("Pre-Registration Request", {
	refresh(frm) {
		if (frm.doc.status === "Approved" && !frm.doc.visitor_pass && !frm.is_new()) {
			frm.add_custom_button(__("Create Visitor Pass"), () => {
				frappe.call({
					method: "create_visitor_pass",
					doc: frm.doc,
					callback: (r) => {
						if (r.message) {
							frappe.set_route("Form", "Visitor Pass", r.message);
						}
					},
				});
			});
		}
	},
});
