// Copyright (c) 2026, Harthesh and contributors
// For license information, please see license.txt

frappe.ui.form.on("Pre-Registration Request", {
	refresh(frm) {
		refresh_hospitality_plan(frm);

		if (frm.doc.status === "Approved" && !frm.doc.visitor_pass && !frm.is_new()) {
			frm.add_custom_button(__("Create Visitor Pass"), () => {
				frappe.call({
					method: "create_visitor_pass",
					doc: frm.doc,
					callback: (r) => {
						if (r.message) {
							frappe.set_route("Form", "Visitor Pass", r.message);
						}
					},
				});
			});
		}
	},

	visit_date(frm) {
		refresh_hospitality_plan(frm);
	},

	expected_checkin(frm) {
		refresh_hospitality_plan(frm);
	},

	expected_checkout(frm) {
		refresh_hospitality_plan(frm);
	},
});

function refresh_hospitality_plan(frm) {
	if (!frm.doc.visit_date || !frm.doc.expected_checkin || !frm.doc.expected_checkout) {
		frm.set_value({
			meal_required: 0,
			meal_type: "",
			assigned_meal_slots: "",
			hospitality_type: "",
		});
		return;
	}

	frappe.call({
		method: "visitormanagement.visitor_management.lifecycle.get_hospitality_meal_plan",
		args: {
			visit_date: frm.doc.visit_date,
			expected_checkin: frm.doc.expected_checkin,
			expected_checkout: frm.doc.expected_checkout,
		},
		callback: ({ message }) => {
			if (!message) {
				return;
			}

			frm.set_value({
				meal_required: message.meal_required || 0,
				meal_type: message.meal_type || "",
				assigned_meal_slots: message.assigned_meal_slots || "",
				hospitality_type: message.hospitality_type || "",
			});
		},
	});
}
