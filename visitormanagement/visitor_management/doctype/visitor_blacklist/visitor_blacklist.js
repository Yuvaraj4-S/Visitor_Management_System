// For license information, please see license.txt

// The full ID number never reaches this form: the server sends only
// `id_proof_number_masked`, and a number is typed into `id_proof_number_entry`,
// which the server stores out of sight and empties on save
// (visitor_management/id_masking.py). A System Manager can ask for the full
// number; the server checks the role again and records every view.
const VMS_REVEAL_ID = "visitormanagement.visitor_management.id_masking.reveal_id";

frappe.ui.form.on("Visitor Blacklist", {
	refresh(frm) {
		const has_number = !!frm.doc.id_proof_number_masked;
		frm.set_df_property(
			"id_proof_number_entry",
			"label",
			has_number ? __("Change ID Proof Number") : __("Enter ID Proof Number")
		);

		if (frm.is_new() || !has_number || !frappe.user.has_role("System Manager")) {
			return;
		}
		frm.add_custom_button(__("Show Full ID Number"), () => {
			frappe.confirm(
				__(
					"Viewing the full ID number is recorded with your name and the time. Continue?"
				),
				() => {
					frappe
						.call({
							method: VMS_REVEAL_ID,
							args: {
								doctype: frm.doctype,
								name: frm.docname,
								fieldname: "id_proof_number",
							},
						})
						.then((r) => {
							if (!r || !r.message) {
								return;
							}
							frappe.msgprint({
								title: __("Full ID Number"),
								message: frappe.utils.escape_html(r.message),
								indicator: "orange",
							});
							// The timeline now carries the note that the number was viewed.
							frm.reload_doc();
						});
				}
			);
		});
	},
});
