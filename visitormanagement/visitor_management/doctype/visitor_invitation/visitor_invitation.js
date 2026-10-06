// For license information, please see license.txt

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

	pass_valid_until(frm) {
		// A multi-day visitor may register any time until the visit's last day.
		if (frm.doc.multi_day_pass && frm.doc.pass_valid_until) {
			frm.set_value("invitation_expires_on", `${frm.doc.pass_valid_until} 23:59:59`);
		}
	},

	multi_day_pass(frm) {
		if (!frm.doc.multi_day_pass) {
			frm.set_value("pass_valid_until", null);
		}
	},

	refresh(frm) {
		if (frm.is_new()) {
			return;
		}

		// --- Send Invitation button (one email + link per visitor row) ---
		const pending_rows = (frm.doc.visitors || []).filter(
			(r) => !["Submitted", "Expired"].includes(r.invitation_status)
		);
		if (pending_rows.length && frm.doc.invitation_status !== "Expired") {
			const btnLabel = frm.doc.invitation_status === "Draft"
				? __("Send Invitation")
				: __("Resend Invitation");

			frm.add_custom_button(btnLabel, () => {
				const action = frm.doc.invitation_status === "Draft" ? "send" : "resend";
				const names = pending_rows.map((r) => frappe.utils.escape_html(r.visitor_email)).join(", ");
				const confirmMsg = action === "resend"
					? __("Invitation was already sent on {0}. Send again to visitors who have not submitted ({1})?", [frm.doc.invitation_sent_on, names])
					: __("Send an invitation email to each visitor ({0})?", [names]);

				frappe.confirm(confirmMsg, () => {
					frappe.call({
						method: "send_invitation",
						doc: frm.doc,
						freeze: true,
						freeze_message: __("Sending visitor invitation..."),
						callback: ({ message }) => {
							if (!message) return;
							frappe.show_alert({
								message: __("Invitation sent to {0} visitor(s)", [message.length]),
								indicator: "green",
							});
							frm.reload_doc();
						},
					});
				});
			}, __("Actions"));
		}

		// --- Copy a visitor's link ---
		const linked_rows = (frm.doc.visitors || []).filter((r) => r.portal_submission_url);
		if (linked_rows.length) {
			frm.add_custom_button(__("Copy Invitation Link"), () => {
				if (linked_rows.length === 1) {
					copyLink(linked_rows[0].portal_submission_url);
					return;
				}
				frappe.prompt(
					{
						fieldname: "row",
						fieldtype: "Select",
						label: __("Visitor"),
						reqd: 1,
						options: linked_rows.map((r) => ({ label: `${r.visitor_full_name} <${r.visitor_email}>`, value: r.name })),
					},
					({ row }) => copyLink(linked_rows.find((r) => r.name === row).portal_submission_url),
					__("Copy Invitation Link")
				);
			}, __("Actions"));
		}

		// --- Status Banner ---
		showStatusBanner(frm);
	},
});

function copyLink(link) {
	frappe.utils.copy_to_clipboard(link);
	frappe.show_alert({ message: __("Link copied to clipboard"), indicator: "green" });
}

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
				${__("Invitation emailed to <b>{0}</b> visitor(s) on {1}. Waiting for visitors to open their links.", [
					(frm.doc.visitors || []).length,
					frappe.utils.escape_html(frappe.datetime.str_to_user(frm.doc.invitation_sent_on) || ""),
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
	} else if (status === "Partially Submitted") {
		html = `
			<div class="vm-invite-banner" style="
				margin: 12px 0; padding: 14px 18px; border-radius: 8px;
				background: #fff8e1; border: 1px solid #ffb300; color: #7a5200;
			">
				<strong>${__("Partially Submitted")}</strong> &mdash;
				${__("{0}. Check the Visitors table for each visitor's status and pass.", [
					frappe.utils.escape_html(frm.doc.visitors_summary || ""),
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
