frappe.ui.form.on("Walk In Visitor Request", {
	refresh(frm) {
		// Mask ID proof number on saved forms
		if (!frm.is_new() && frm.doc.id_proof_number && !frm.doc.id_proof_number.includes("X")) {
			const val = frm.doc.id_proof_number;
			const chars = [];
			for (let i = 0; i < val.length; i++) {
				if (/[A-Za-z0-9]/.test(val[i])) chars.push(i);
			}
			if (chars.length > 4) {
				const visible = new Set(chars.slice(-4));
				let masked = "";
				for (let i = 0; i < val.length; i++) {
					if (/[A-Za-z0-9]/.test(val[i])) {
						masked += visible.has(i) ? val[i] : "X";
					} else {
						masked += val[i];
					}
				}
				frm.doc.id_proof_number = masked;
				frm.refresh_field("id_proof_number");
			}
		}

		// Visitor photos are captured per visitor in the Visitors table (header photo is hidden).

		// Show Approve / Reject buttons for the host or System Manager
		if (frm.doc.status === "Pending Approval" && !frm.is_new()) {
			// Ask the server — same rule it enforces on approve (host or System Manager).
			frappe.call({
				method: "visitormanagement.visitor_management.doctype.walk_in_visitor_request.walk_in_visitor_request.can_current_user_approve",
				args: { request_name: frm.doc.name },
				callback: ({ message }) => {
					if (!message) return;
					frm.add_custom_button(__("Approve"), () => _approve_request(frm), __("Action"));
					frm.add_custom_button(__("Reject"), () => _reject_request(frm), __("Action"));
				},
			});
		}

		// Show link to the generated Visitor Pass
		if (frm.doc.visitor_pass) {
			frm.set_intro(
				__("Visitor Pass {0} has been created.", [
					`<a href="/app/visitor-pass/${frm.doc.visitor_pass}">${frm.doc.visitor_pass}</a>`,
				]),
				"green"
			);
		}

		// Status indicators
		if (frm.doc.status === "Pending Approval") {
			frm.set_intro(
				__("Waiting for approval from the host ({0}).", [frm.doc.person_to_visit]),
				"orange"
			);
		} else if (frm.doc.status === "Rejected") {
			frm.set_intro(
				__("This request was rejected. Reason: {0}", [frm.doc.rejection_reason || "Not specified"]),
				"red"
			);
		}
	},

	person_to_visit(frm) {
		if (frm.doc.person_to_visit) {
			frappe.db.get_value("Employee", frm.doc.person_to_visit, "department", (r) => {
				if (r) frm.set_value("host_department", r.department);
			});
		}
	},
});


// ─────────────────────────────────────────────────────────
// APPROVAL / REJECTION
// ─────────────────────────────────────────────────────────

function _approve_request(frm) {
	frappe.confirm(
		__("Are you sure you want to approve this walk-in visitor request?"),
		() => {
			frappe.call({
				method:
					"visitormanagement.visitor_management.doctype.walk_in_visitor_request.walk_in_visitor_request.approve_request",
				args: { request_name: frm.doc.name },
				freeze: true,
				freeze_message: __("Approving and generating Visitor Pass..."),
				callback(r) {
					if (r.message) {
						frm.reload_doc();
					}
				},
			});
		}
	);
}

function _reject_request(frm) {
	frappe.prompt(
		{
			fieldname: "rejection_reason",
			fieldtype: "Small Text",
			label: __("Rejection Reason"),
			reqd: 1,
		},
		(values) => {
			frappe.call({
				method:
					"visitormanagement.visitor_management.doctype.walk_in_visitor_request.walk_in_visitor_request.reject_request",
				args: {
					request_name: frm.doc.name,
					rejection_reason: values.rejection_reason,
				},
				freeze: true,
				freeze_message: __("Rejecting request..."),
				callback(r) {
					if (r.message) {
						frm.reload_doc();
					}
				},
			});
		},
		__("Reject Walk-In Request"),
		__("Reject")
	);
}

// New visitors start with row 1's times (Frappe would otherwise stamp "now").
frappe.ui.form.on("Walk In Additional Visitor", {
	additional_visitors_add(frm, cdt, cdn) {
		const first = (frm.doc.additional_visitors || [])[0];
		if (!first || first.name === cdn) return;
		frappe.model.set_value(cdt, cdn, "expected_checkin", first.expected_checkin || null);
		frappe.model.set_value(cdt, cdn, "expected_checkout", first.expected_checkout || null);
	},
});
