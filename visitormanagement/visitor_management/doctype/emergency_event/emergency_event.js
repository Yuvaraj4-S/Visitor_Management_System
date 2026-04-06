// Copyright (c) 2026, Harthesh and contributors
// For license information, please see license.txt

frappe.ui.form.on("Emergency Event", {
	refresh(frm) {
		if (!frm.is_new() && frm.doc.status === "Active") {
			frm.add_custom_button(__("Regenerate Muster"), () => {
				frappe.call({
					method: "regenerate_muster",
					doc: frm.doc,
					callback: (r) => {
						if (typeof r.message !== "undefined") {
							frappe.show_alert({
								message: __("Muster refreshed for {0} active visitors.", [r.message]),
								indicator: "green",
							});
							frm.reload_doc();
						}
					},
				});
			});
		}
	},
});
