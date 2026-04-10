frappe.listview_settings["Visitor Pass"] = {
	onload(listview) {
		listview.page.add_inner_button(__("Web Submissions"), () => {
			open_web_submissions_dialog();
		});
	},
};

function open_web_submissions_dialog() {
	frappe.call({
		method: "frappe.client.get_list",
		args: {
			doctype: "Pre-Registration Request",
			filters: [
				["request_channel", "=", "Portal"],
				["status", "!=", "Converted"],
			],
			fields: [
				"name",
				"visitor_name",
				"visitor_type",
				"mobile_number",
				"email_id",
				"visit_date",
				"person_to_visit",
				"status",
				"visitor_invitation",
				"modified",
			],
			limit_page_length: 50,
			order_by: "creation desc",
		},
		callback: ({ message }) => {
			const submissions = message || [];
			if (!submissions.length) {
				frappe.msgprint(__("No pending web submissions found."));
				return;
			}

			let html = `
				<style>
					.vms-submission-shell {
						display: grid;
						grid-template-columns: repeat(auto-fit, minmax(320px, 1fr));
						gap: 18px;
						padding: 6px 2px;
					}
					.vms-submission-card {
						border: 1px solid #d8e1ec;
						border-radius: 22px;
						padding: 22px;
						background:
							linear-gradient(180deg, rgba(255,255,255,0.98), rgba(247,250,252,0.98)),
							radial-gradient(circle at top right, rgba(14, 165, 233, 0.08), transparent 40%);
						box-shadow: 0 18px 40px rgba(15, 23, 42, 0.08);
					}
					.vms-submission-head {
						display: flex;
						align-items: flex-start;
						justify-content: space-between;
						gap: 12px;
						margin-bottom: 16px;
					}
					.vms-submission-title {
						font-size: 1.1rem;
						font-weight: 700;
						color: #0f172a;
						line-height: 1.3;
						margin: 0;
					}
					.vms-submission-subtitle {
						font-size: 0.86rem;
						color: #475569;
						margin-top: 4px;
					}
					.vms-status-pill, .vms-source-pill {
						display: inline-flex;
						align-items: center;
						border-radius: 999px;
						padding: 6px 10px;
						font-size: 0.75rem;
						font-weight: 700;
						letter-spacing: 0.02em;
						white-space: nowrap;
					}
					.vms-status-pill {
						background: #e0f2fe;
						color: #075985;
					}
					.vms-source-pill {
						background: #ecfccb;
						color: #3f6212;
					}
					.vms-meta-grid {
						display: grid;
						grid-template-columns: repeat(2, minmax(0, 1fr));
						gap: 12px;
						margin-bottom: 16px;
					}
					.vms-meta-item {
						background: #f8fafc;
						border: 1px solid #e2e8f0;
						border-radius: 14px;
						padding: 12px 14px;
					}
					.vms-meta-label {
						font-size: 0.72rem;
						font-weight: 700;
						letter-spacing: 0.08em;
						text-transform: uppercase;
						color: #64748b;
						margin-bottom: 6px;
					}
					.vms-meta-value {
						font-size: 0.95rem;
						font-weight: 600;
						color: #0f172a;
						word-break: break-word;
					}
					.vms-card-footer {
						display: flex;
						align-items: center;
						justify-content: space-between;
						gap: 12px;
						margin-top: 6px;
					}
					.vms-card-updated {
						font-size: 0.82rem;
						color: #64748b;
					}
					.vms-web-submission-pick {
						border-radius: 999px;
						padding-inline: 16px;
						font-weight: 700;
					}
					@media (max-width: 767px) {
						.vms-meta-grid {
							grid-template-columns: 1fr;
						}
						.vms-card-footer {
							flex-direction: column;
							align-items: stretch;
						}
					}
				</style>
				<div class="vms-submission-shell">
			`;
			submissions.forEach((sub) => {
				const sourceLabel = sub.visitor_invitation ? __("Invitation") : __("Portal");
				html += `
					<div class="vms-submission-card">
						<div class="vms-submission-head">
							<div>
								<h5 class="vms-submission-title">${frappe.utils.escape_html(sub.visitor_name || "Visitor")}</h5>
								<div class="vms-submission-subtitle">${frappe.utils.escape_html(sub.visitor_type || "")} • ${frappe.utils.escape_html(sub.name)}</div>
							</div>
							<div class="text-end">
								<div class="vms-status-pill">${frappe.utils.escape_html(sub.status || "Draft")}</div>
								<div class="mt-2 vms-source-pill">${frappe.utils.escape_html(sourceLabel)}</div>
							</div>
						</div>
						<div class="vms-meta-grid">
							<div class="vms-meta-item">
								<div class="vms-meta-label">${__("Phone")}</div>
								<div class="vms-meta-value">${frappe.utils.escape_html(sub.mobile_number || "Not provided")}</div>
							</div>
							<div class="vms-meta-item">
								<div class="vms-meta-label">${__("Email")}</div>
								<div class="vms-meta-value">${frappe.utils.escape_html(sub.email_id || "Not provided")}</div>
							</div>
							<div class="vms-meta-item">
								<div class="vms-meta-label">${__("Visit Date")}</div>
								<div class="vms-meta-value">${frappe.datetime.str_to_user(sub.visit_date || "") || frappe.utils.escape_html(sub.visit_date || "-")}</div>
							</div>
							<div class="vms-meta-item">
								<div class="vms-meta-label">${__("Host")}</div>
								<div class="vms-meta-value">${frappe.utils.escape_html(sub.person_to_visit || "Not assigned")}</div>
							</div>
						</div>
						<div class="vms-card-footer">
							<div class="vms-card-updated">${__("Updated")}: ${frappe.datetime.str_to_user(sub.modified || "") || frappe.utils.escape_html(sub.modified || "")}</div>
							<button class="btn btn-dark btn-sm vms-web-submission-pick" data-name="${frappe.utils.escape_html(sub.name)}">${__("Use Submission")}</button>
						</div>
					</div>
				`;
			});
			html += "</div>";

			const dialog = new frappe.ui.Dialog({
				title: __("Web Submissions"),
				fields: [{ fieldtype: "HTML", fieldname: "submissions", options: html }],
				size: "large",
			});
			dialog.show();

			$(dialog.$wrapper).on("click", ".vms-web-submission-pick", (event) => {
				const source_name = event.currentTarget.getAttribute("data-name");
				if (!source_name) return;
				dialog.hide();
				create_new_visitor_pass_from_web_submission(source_name);
			});
		},
	});
}

function create_new_visitor_pass_from_web_submission(source_name) {
	frappe.call({
		method: "frappe.client.get",
		args: { doctype: "Pre-Registration Request", name: source_name },
		callback: ({ message }) => {
			if (!message) return;

			frappe.new_doc("Visitor Pass");
			setTimeout(() => {
				const frm = cur_frm;
				if (!frm) return;

				frm.set_value("visitor_type", message.visitor_type);
				frm.set_value("entry_type", "New");
				frm.set_value("visitor_full_name", message.visitor_name);
				frm.set_value("mobile_number", message.mobile_number);
				frm.set_value("email_id", message.email_id);
				frm.set_value("company__organisation", message.company__organisation);
				frm.set_value("visit_date", message.visit_date);
				frm.set_value("expected_checkin", message.expected_checkin);
				frm.set_value("expected_checkout", message.expected_checkout);
				frm.set_value("purpose_of_visit", (message.purpose_of_visit || "").trim());
				frm.set_value("person_to_visit", message.person_to_visit);
				frm.set_value("id_proof_type", message.id_proof_type);
				frm.set_value("id_proof_number", message.id_proof_number);
				frm.set_value("id_proof_scan", message.id_proof_scan);
				frm.set_value("visitor_photo", message.visitor_photo);
				frm.set_value("pre_registration_request", message.name);
				frm.set_value("request_channel", "Portal");
			}, 300);
		},
	});
}
