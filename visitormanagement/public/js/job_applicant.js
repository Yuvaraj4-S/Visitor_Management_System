frappe.ui.form.on("Job Applicant", {
	after_save(frm) {
		// The app's Custom Field is `custom_interview_mode`; the legacy
		// `interview_mode` is carried over and removed on migrate.
		if (frm.doc.custom_interview_mode !== "Offline") {
			return;
		}
		// An HRMS user without access to this app's invitations gets nothing from
		// this app here — no lookup, and so no "No permission" popup.
		if (!frappe.model.can_read("Visitor Invitation")) {
			return;
		}

		frappe.db
			.get_value("Visitor Invitation", { reference_job_applicant: frm.doc.name }, "name")
			.then((r) => {
				const inv = r && r.message && r.message.name;
				if (!inv) {
					return;
				}

				frappe.show_alert({
					message: __("Opening Visitor Invitation {0}", [inv]),
					indicator: "green",
				});
				frappe.set_route("Form", "Visitor Invitation", inv);
			});
	},
});
