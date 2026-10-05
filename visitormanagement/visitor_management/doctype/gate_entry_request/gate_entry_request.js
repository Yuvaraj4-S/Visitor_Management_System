// For license information, please see license.txt

const GER_METHOD = "visitormanagement.visitor_management.doctype.gate_entry_request.gate_entry_request";

frappe.ui.form.on("Gate Entry Request", {
	refresh(frm) {
		toggle_mapping(frm);

		// Items are only declared here; Security ticks "Verified" on the Visitor Pass at check-in.
		const items_grid = frm.fields_dict.visitor_items.grid;
		items_grid.update_docfield_property("verified_by_security", "hidden", 1);
		items_grid.update_docfield_property("verification_remarks", "hidden", 1);

		// Lock only once the request has left Draft. (frm.set_read_only() ignores its
		// argument and frm.disable_save()'s argument is "set dirty", so neither can be
		// called with a boolean — calling them on a Draft made the form read-only.)
		if (!frm.is_new() && frm.doc.status !== "Draft") {
			frm.disable_form();
		}

		if (frm.doc.__onload && frm.doc.__onload.id_masked) {
			frm.set_df_property("id_proof_number", "read_only", 1);
			frm.set_df_property("id_proof_number", "description", __("Masked for privacy."));
		}

		if (frm.doc.status === "Draft" && !frm.is_new()) {
			frm.add_custom_button(__("Submit for Approval"), () => {
				frappe.call({
					method: `${GER_METHOD}.submit_gate_entry_request`,
					args: { gate_entry_request: frm.doc.name },
					freeze: true,
					callback() {
						frm.reload_doc();
						frappe.show_alert({ message: __("Request sent for approval"), indicator: "green" });
					},
				});
			}).addClass("btn-primary");
		}

		if (frm.doc.status === "Pending Approval" && frm.doc.__onload && frm.doc.__onload.can_act) {
			frm.add_custom_button(__("Approve"), () => {
				frappe.confirm(__("Approve entry for {0}?", [frm.doc.visitor_full_name]), () => {
					frappe.call({
						method: `${GER_METHOD}.approve_gate_entry`,
						args: { gate_entry_request: frm.doc.name },
						freeze: true,
						freeze_message: __("Creating Visitor Pass..."),
						callback(r) {
							frm.reload_doc();
							frappe.show_alert({
								message: __("Visitor approved. Pass: {0}", [r.message.visitor_pass]),
								indicator: "green",
							});
						},
					});
				});
			}, __("Actions"));

			frm.add_custom_button(__("Reject"), () => {
				frappe.prompt(
					{ fieldname: "reason", label: __("Rejection Reason"), fieldtype: "Small Text", reqd: 1 },
					(values) => {
						frappe.call({
							method: `${GER_METHOD}.reject_gate_entry`,
							args: { gate_entry_request: frm.doc.name, reason: values.reason },
							freeze: true,
							callback() {
								frm.reload_doc();
							},
						});
					},
					__("Reject Gate Entry"),
					__("Reject")
				);
			}, __("Actions"));
		}

		if (frm.doc.visitor_pass) {
			frm.set_intro(
				__("Visitor Pass created: {0}", [
					`<a href="/app/visitor-pass/${encodeURIComponent(frm.doc.visitor_pass)}">${frappe.utils.escape_html(frm.doc.visitor_pass)}</a>`,
				]),
				"green"
			);
		} else if (["Rejected", "Expired"].includes(frm.doc.status)) {
			frm.set_intro(__("Entry not allowed — request {0}.", [frm.doc.status.toLowerCase()]), "red");
		}
	},

	mapping_type(frm) {
		toggle_mapping(frm);
		if (frm.doc.mapping_type === "Single Person") {
			frm.set_value("visitor_group", "");
		} else {
			frm.set_value("person_to_visit", "");
		}
	},
});

function toggle_mapping(frm) {
	const single = frm.doc.mapping_type !== "Group";
	frm.toggle_reqd("person_to_visit", single);
	frm.toggle_reqd("visitor_group", !single);
	frm.toggle_display("person_to_visit", single);
	frm.toggle_display("person_to_visit_name", single);
	frm.toggle_display("visitor_group", !single);
}
