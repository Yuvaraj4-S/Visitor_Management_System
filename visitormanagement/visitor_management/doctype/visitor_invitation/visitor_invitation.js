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

	onload(frm) {
		// Restrict Host Employee picker to the currently logged-in user's Employee
		frm.set_query("host_employee", () => ({
			filters: {
				user_id: frappe.session.user,
				status: "Active",
			},
		}));

		// On new forms, auto-fill Host Employee with the logged-in user's Employee
		if (frm.is_new() && !frm.doc.host_employee && !["Administrator", "Guest"].includes(frappe.session.user)) {
			frappe.call({
				method: "frappe.client.get_value",
				args: {
					doctype: "Employee",
					filters: { user_id: frappe.session.user, status: "Active" },
					fieldname: "name",
				},
				callback: (r) => {
					const emp = r && r.message && r.message.name;
					if (emp && !frm.doc.host_employee) {
						frm.set_value("host_employee", emp);
					}
				},
			});
		}
	},

	visit_date(frm) {
		if (frm.doc.visit_date && !frm.doc.invitation_expires_on) {
			frm.set_value("invitation_expires_on", `${frm.doc.visit_date} 23:59:59`);
		}
	},

	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		// --- Send Invitation button ---
		if (frm.doc.visitor_email && !["Submitted", "Expired"].includes(frm.doc.invitation_status)) {
			const btnLabel = frm.doc.invitation_status === "Draft"
				? __("Send Invitation")
				: __("Resend Invitation");

			frm.add_custom_button(btnLabel, () => {
				const action = frm.doc.invitation_status === "Draft" ? "send" : "resend";
				const confirmMsg = action === "resend"
					? __("Invitation was already sent on {0}. Send again?", [frm.doc.invitation_sent_on])
					: __("Send invitation email to {0}?", [frm.doc.visitor_email]);

				frappe.confirm(confirmMsg, () => {
					frappe.call({
						method: "send_invitation",
						doc: frm.doc,
						freeze: true,
						freeze_message: __("Sending visitor invitation..."),
						callback: ({ message }) => {
							if (!message) return;
							frappe.show_alert({
								message: __("Invitation sent to {0}", [frm.doc.visitor_email]),
								indicator: "green",
							});
							frm.reload_doc();
						},
					});
				});
			}, __("Actions"));
		}

		// --- Open Link button ---
		if (frm.doc.portal_submission_url || frm.doc.invitation_token) {
			frm.add_custom_button(__("Copy Invitation Link"), () => {
				const link = getInvitationLink(frm);
				frappe.utils.copy_to_clipboard(link);
				frappe.show_alert({ message: __("Link copied to clipboard"), indicator: "green" });
			}, __("Actions"));
		}

		// --- Status Banner ---
		showStatusBanner(frm);
	},
});

function showStatusBanner(frm) {
	const status = frm.doc.invitation_status;

	// Remove old banner
	$(frm.fields_dict.visitor_type.wrapper).closest(".form-page").find(".vm-invite-banner").remove();

	let html = "";

	if (status === "Draft") {
		html = `
			<div class="vm-invite-banner" style="
				margin: 12px 0; padding: 14px 18px; border-radius: 8px;
				background: #fff3cd; border: 1px solid #ffc107; color: #856404;
			">
				<strong>${__("Not Sent")}</strong> &mdash;
				${__("Save the form and click <b>Send Invitation</b> to email the visitor.")}
			</div>
		`;
	} else if (status === "Sent") {
		html = `
			<div class="vm-invite-banner" style="
				margin: 12px 0; padding: 14px 18px; border-radius: 8px;
				background: #d1ecf1; border: 1px solid #17a2b8; color: #0c5460;
			">
				<strong>${__("Sent")}</strong> &mdash;
				${__("Invitation emailed to <b>{0}</b> on {1}. Waiting for visitor to open the link.", [
					frm.doc.visitor_email,
					frappe.datetime.str_to_user(frm.doc.invitation_sent_on),
				])}
			</div>
		`;
	} else if (status === "Opened") {
		html = `
			<div class="vm-invite-banner" style="
				margin: 12px 0; padding: 14px 18px; border-radius: 8px;
				background: #e8f5e9; border: 1px solid #4caf50; color: #2e7d32;
			">
				<strong>${__("Link Opened")}</strong> &mdash;
				${__("Visitor opened the link on {0}. Waiting for form submission.", [
					frappe.datetime.str_to_user(frm.doc.link_opened_on),
				])}
			</div>
		`;
	} else if (status === "Saved" || status === "Submitted") {
		const vpLink = frm.doc.visitor_pass
			? ` <a href="/app/visitor-pass/${frm.doc.visitor_pass}">${frm.doc.visitor_pass}</a>`
			: "";
		html = `
			<div class="vm-invite-banner" style="
				margin: 12px 0; padding: 14px 18px; border-radius: 8px;
				background: #e8f5e9; border: 1px solid #28a745; color: #155724;
			">
				<strong>${__("Form {0}", [status])}</strong> &mdash;
				${__("Visitor completed the pre-registration form on {0}.", [
					frappe.datetime.str_to_user(frm.doc.form_submitted_on || frm.doc.form_saved_on),
				])}
				${vpLink ? __(" Visitor Pass: ") + vpLink : ""}
			</div>
		`;
	} else if (status === "Expired") {
		html = `
			<div class="vm-invite-banner" style="
				margin: 12px 0; padding: 14px 18px; border-radius: 8px;
				background: #f8d7da; border: 1px solid #dc3545; color: #721c24;
			">
				<strong>${__("Expired")}</strong> &mdash;
				${__("This invitation expired on {0}. The visitor can no longer use this link.", [
					frappe.datetime.str_to_user(frm.doc.invitation_expires_on),
				])}
			</div>
		`;
	}

	if (html) {
		$(frm.fields_dict.visitor_type.wrapper).closest(".form-page").find(".form-message").after(html);
	}
}
