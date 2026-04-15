frappe.ui.form.on("Job Applicant", {
	after_save(frm) {
		if (frm.doc.interview_mode !== "Offline") {
			return;
		}

		frappe.db
			.get_value(
				"Visitor Invitation",
				{ reference_job_applicant: frm.doc.name },
				"name"
			)
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
