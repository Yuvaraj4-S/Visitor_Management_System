// Copyright (c) 2026, Harthesh and contributors
// For license information, please see license.txt

function getInvitationLink(frm) {
	if (frm.doc.invitation_token) {
		return frappe.urllib.get_full_url(
			`/visitor-pre-registration-form/new?token=${encodeURIComponent(frm.doc.invitation_token)}`
		);
	}

	return frm.doc.portal_submission_url;
}

frappe.ui.form.on("Visitor Invitation", {

	visit_date(frm) {
		if (frm.doc.visit_date && !frm.doc.invitation_expires_on) {
			frm.set_value("invitation_expires_on", `${frm.doc.visit_date} 23:59:59`);
		}
	},

	refresh(frm) {
		if (!frm.is_new() && frm.doc.visitor_email && frm.doc.invitation_status !== "Submitted") {
			frm.add_custom_button(__("Send Invitation"), () => {
				frappe.call({
					method: "send_invitation",
					doc: frm.doc,
					freeze: true,
					freeze_message: __("Sending visitor invitation..."),
					callback: ({ message }) => {
						if (!message) return;

						frappe.show_alert({ message: __("Invitation sent"), indicator: "green" });
						frm.reload_doc();
					},
				});
			});
		}

		if (frm.doc.portal_submission_url) {
			frm.add_custom_button(__("Open Invitation Link"), () => {
				window.open(getInvitationLink(frm), "_blank");
			});
		}
	},
});
