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
			doctype: "Visitor Pass",
			filters: [
				["request_channel", "=", "Portal"],
				["workflow_state", "in", ["Draft", "Pending System Manager", "Pending Visitor Manager", "Pending Sales Manager", "Pending HR Manager", "Pending HOD", "Pending CEO"]],
			],
			fields: ["name", "visitor_full_name", "visitor_type", "mobile_number", "email_id", "visit_date"],
			limit_page_length: 50,
			order_by: "creation desc",
		},
		callback: ({ message }) => {
			const submissions = message || [];
			if (!submissions.length) {
				frappe.msgprint(__("No pending web submissions found."));
				return;
			}

			let html = '<div class="row">';
			submissions.forEach((sub) => {
				html += `
					<div class="col-md-6 mb-3">
						<div class="card">
							<div class="card-body">
								<h5 class="card-title">${frappe.utils.escape_html(sub.visitor_full_name || "Visitor")} (${frappe.utils.escape_html(sub.visitor_type || "")})</h5>
								<p class="card-text">
									Phone: ${frappe.utils.escape_html(sub.mobile_number || "")}<br>
									Email: ${frappe.utils.escape_html(sub.email_id || "")}<br>
									Date: ${frappe.utils.escape_html(sub.visit_date || "")}
								</p>
								<button class="btn btn-primary btn-sm vms-web-submission-pick" data-name="${frappe.utils.escape_html(sub.name)}">Use This Data</button>
							</div>
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
		args: { doctype: "Visitor Pass", name: source_name },
		callback: ({ message }) => {
			if (!message) return;

			frappe.new_doc("Visitor Pass");
			setTimeout(() => {
				const frm = cur_frm;
				if (!frm) return;

				frm.set_value("visitor_type", message.visitor_type);
				frm.set_value("entry_type", "New");
				frm.set_value("visitor_full_name", message.visitor_full_name);
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
				frm.set_value("request_channel", "Portal");
			}, 300);
		},
	});
}
