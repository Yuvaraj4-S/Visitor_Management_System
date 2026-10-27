// For license information, please see license.txt

// Roles that may raise an invitation with somebody else as the host. The rule
// itself is enforced on save (visitor_invitation.py HOST_ON_BEHALF_ROLES); this
// copy only decides what the host picker offers.
const HOST_ON_BEHALF_ROLES = ["System Manager", "Front Office Executive"];

// What the server said this user may do with this invitation (set in the
// controller's onload). The link is a bearer credential, so the browser never
// decides this from role permissions.
function invitationAccess(frm) {
	return frm.doc.__onload || {};
}

// The link actions always run on the saved invitation, by name: nothing typed
// into the form and not yet saved can reach the server through them.
function callInvitationMethod(frm, method, options = {}) {
	return frappe.call({
		method: "run_doc_method",
		args: { dt: frm.doctype, dn: frm.docname, method },
		...options,
	});
}

function refuseIfUnsaved(frm) {
	if (!frm.is_dirty()) {
		return false;
	}
	frappe.msgprint({
		title: __("Unsaved Changes"),
		indicator: "orange",
		message: __("Save your changes first, then try again."),
	});
	return true;
}

// Show the link with a Copy button. Copying happens on that button's own click
// (browsers only allow a copy from a user's click), and the form is reloaded
// once the dialog is gone — reloading under it would tear it down.
function showInvitationLink(frm, { title, indicator, intro, link, advice, detail }) {
	const safeLink = frappe.utils.escape_html(link);
	const dialog = new frappe.ui.Dialog({
		title,
		indicator,
		fields: [{ fieldtype: "HTML", fieldname: "body" }],
		primary_action_label: __("Copy Link"),
		primary_action() {
			frappe.utils.copy_to_clipboard(link);
			dialog.hide();
		},
		on_hide: () => frm.reload_doc(),
	});
	dialog.fields_dict.body.$wrapper.html(`
		<p>${intro}</p>
		<p><b>${__("Link:")}</b><br>
			<a class="vm-invite-link" href="${safeLink}" target="_blank" rel="noopener"
				style="word-break: break-all;">${safeLink}</a></p>
		${advice ? `<p>${advice}</p>` : ""}
		${detail ? `<p class="text-muted small">${frappe.utils.escape_html(detail)}</p>` : ""}
	`);
	dialog.show();
}

frappe.ui.form.on("Visitor Invitation", {
	onload(frm) {
		// A host invites their own visitors, so the picker offers them only their
		// own Employee record. Reception and administrators invite on behalf of
		// anyone. Searched through this app's query: no role needs a permission on
		// the HRMS/ERPNext DocType (see link_queries.py).
		frm.set_query("host_employee", () => {
			const filters = { status: "Active" };
			if (!frappe.user.has_role(HOST_ON_BEHALF_ROLES)) {
				filters.user_id = frappe.session.user;
			}
			return {
				query: "visitormanagement.visitor_management.link_queries.search",
				filters,
			};
		});
		frm.set_query("reference_job_applicant", () => ({
			query: "visitormanagement.visitor_management.link_queries.search",
		}));

		// On new forms, auto-fill Host Employee with the logged-in user's Employee
		if (
			frm.is_new() &&
			!frm.doc.host_employee &&
			!["Administrator", "Guest"].includes(frappe.session.user)
		) {
			frappe.call({
				method: "visitormanagement.visitor_management.link_details.get_own_employee",
				callback: (r) => {
					const emp = r && r.message;
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

		const access = invitationAccess(frm);
		const status = frm.doc.invitation_status;
		const finished = ["Submitted", "Cancelled"].includes(status);

		// --- Send Invitation button ---
		// Only for someone who may change this invitation, and never for one whose
		// link is finished with: used, cancelled, or past its expiry.
		if (access.can_manage && frm.doc.visitor_email && !finished && !access.is_expired) {
			const btnLabel = status === "Draft" ? __("Send Invitation") : __("Resend Invitation");

			frm.add_custom_button(
				btnLabel,
				() => {
					if (refuseIfUnsaved(frm)) return;

					const action = frm.doc.invitation_status === "Draft" ? "send" : "resend";
					const confirmMsg =
						action === "resend"
							? __("Invitation was already sent on {0}. Send again?", [
									frappe.datetime.str_to_user(frm.doc.invitation_sent_on),
							  ])
							: __("Send invitation email to {0}?", [
									frappe.utils.escape_html(frm.doc.visitor_email),
							  ]);

					frappe.confirm(confirmMsg, () => {
						callInvitationMethod(frm, "send_invitation", {
							freeze: true,
							freeze_message: __("Sending visitor invitation..."),
							callback: ({ message }) => {
								if (!message) return;

								if (message.delivered) {
									frappe.show_alert({
										message: __("Invitation sent to {0}", [
											frm.doc.visitor_email,
										]),
										indicator: "green",
									});
									frm.reload_doc();
									return;
								}

								// The link is always minted, so a site without outgoing
								// email can still get the visitor registered — show it
								// and let the host pass it on by hand.
								showInvitationLink(frm, {
									title: __("Email Not Sent"),
									indicator: "orange",
									intro: __(
										"The invitation link was created, but the email could not be delivered."
									),
									link: message.link,
									advice: __(
										"Share it with the visitor directly, or configure an outgoing Email Account and resend."
									),
									detail: message.error,
								});
							},
						});
					});
				},
				__("Actions")
			);
		}

		// --- Copy Link button ---
		// The link is not on the document: it is fetched, on request, by someone
		// the server allows to hand it out.
		if (access.has_link) {
			frm.add_custom_button(
				__("Copy Invitation Link"),
				() => {
					callInvitationMethod(frm, "get_invitation_link", {
						callback: ({ message }) => {
							if (!message) {
								frappe.msgprint(
									__("This invitation no longer has a working link.")
								);
								frm.reload_doc();
								return;
							}
							showInvitationLink(frm, {
								title: __("Invitation Link"),
								indicator: "blue",
								intro: __(
									"Whoever opens this link can register as the visitor. Send it only to {0}.",
									[frappe.utils.escape_html(frm.doc.visitor_email || "")]
								),
								link: message,
							});
						},
					});
				},
				__("Actions")
			);
		}

		// --- Cancel Invitation button ---
		if (access.can_manage && !finished) {
			frm.add_custom_button(
				__("Cancel Invitation"),
				() => {
					frappe.confirm(
						__(
							"Cancel this invitation? The link sent to {0} stops working immediately and cannot be restored.",
							[frappe.utils.escape_html(frm.doc.visitor_email || "")]
						),
						() => {
							callInvitationMethod(frm, "cancel_invitation", {
								freeze: true,
								callback: () => {
									frappe.show_alert({
										message: __("Invitation cancelled"),
										indicator: "orange",
									});
									frm.reload_doc();
								},
							});
						}
					);
				},
				__("Actions")
			);
		}

		// --- Status Banner ---
		showStatusBanner(frm);
	},
});

// The status line above the form. Shown through the form's own intro message
// (frm.set_intro), which the form clears on every refresh and themes itself.
// It was previously attached next to an element the desk only creates while a
// message is showing, so it never appeared.
function showStatusBanner(frm) {
	// Past its expiry is expired, whatever the stored status still says: that
	// only turns "Expired" once somebody opens the dead link.
	const status = invitationAccess(frm).is_expired ? "Expired" : frm.doc.invitation_status;

	let title = "";
	let text = "";
	let color = "blue";

	if (status === "Draft") {
		title = __("Not Sent");
		text = __("Save the form and click <b>Send Invitation</b> to email the visitor.");
		color = "yellow";
	} else if (status === "Sent") {
		title = __("Sent");
		text = __(
			"Invitation emailed to <b>{0}</b> on {1}. Waiting for visitor to open the link.",
			[
				frappe.utils.escape_html(frm.doc.visitor_email || ""),
				frappe.utils.escape_html(
					frappe.datetime.str_to_user(frm.doc.invitation_sent_on) || ""
				),
			]
		);
	} else if (status === "Opened") {
		title = __("Link Opened");
		text = __("Visitor opened the link on {0}. Waiting for form submission.", [
			frappe.datetime.str_to_user(frm.doc.link_opened_on),
		]);
		color = "green";
	} else if (status === "Saved" || status === "Submitted") {
		title = __("Form {0}", [__(status)]);
		text = __("Visitor completed the pre-registration form on {0}.", [
			frappe.datetime.str_to_user(frm.doc.form_submitted_on || frm.doc.form_saved_on),
		]);
		if (frm.doc.visitor_pass) {
			const pass = frappe.utils.get_form_link("Visitor Pass", frm.doc.visitor_pass, true);
			text += ` ${__("Visitor Pass:")} ${pass}`;
		}
		color = "green";
	} else if (status === "Expired") {
		title = __("Expired");
		text =
			__("This invitation expired on {0}. The visitor can no longer use this link.", [
				frappe.datetime.str_to_user(frm.doc.invitation_expires_on),
			]) +
			" " +
			__("To use it again, set a later <b>Invitation Expires On</b> and save.");
		color = "red";
	} else if (status === "Cancelled") {
		title = __("Cancelled");
		text =
			__("This invitation was cancelled. Its link no longer works.") +
			" " +
			__("Raise a new invitation if the visit is still on.");
		color = "orange";
	}

	frm.set_intro(
		title
			? `<span class="vm-invite-banner"><strong>${title}</strong> &mdash; ${text}</span>`
			: "",
		color
	);
}
