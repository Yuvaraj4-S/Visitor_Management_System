// For license information, please see license.txt

function sync_reqd_flags(frm) {
	// UI-only sync of required-field markers. Must NOT call set_value() — doing
	// so on refresh dirties the form and shows "Not Saved" before the user has
	// touched anything.
	const t = frm.doc.cab_type;
	const needs_pickup = t === "Pickup" || t === "Both";
	const needs_drop = t === "Drop" || t === "Both";
	frm.toggle_reqd(["cab_type"], frm.doc.cab_required);
	frm.toggle_reqd(["pickup_location", "pickup_datetime"], frm.doc.cab_required && needs_pickup);
	frm.toggle_reqd(["drop_location", "drop_datetime"], frm.doc.cab_required && needs_drop);
	frm.toggle_reqd(["check_in", "check_out"], frm.doc.hotel_required);
	frm.toggle_reqd(["tour_guide"], frm.doc.factory_tour_required);
	frm.toggle_reqd(["buggy_pickup_point", "buggy_datetime"], frm.doc.buggy_required);
	frm.toggle_reqd(["greeting_type", "greeting_delivery_time"], frm.doc.greeting_required);
}

frappe.ui.form.on("Hospitality Request", {
	refresh(frm) {
		if (!frm.is_new()) {
			frm.set_query("visitor_pass", () => ({
				filters: {
					docstatus: ["!=", 2],
				},
			}));
		}
		if (frm.dashboard) frm.dashboard.clear_headline();
		sync_reqd_flags(frm);
	},

	cab_required(frm) {
		frm.toggle_reqd(["cab_type"], frm.doc.cab_required);
		if (frm.doc.cab_required && !frm.doc.cab_type) {
			frm.set_value("cab_type", "Both");
		}
		if (frm.doc.cab_required && !frm.doc.buggy_required) {
			frm.set_value("buggy_required", 1);
		}
	},

	cab_type(frm) {
		const t = frm.doc.cab_type;
		const needs_pickup = t === "Pickup" || t === "Both";
		const needs_drop = t === "Drop" || t === "Both";
		frm.toggle_reqd(["pickup_location", "pickup_datetime"], needs_pickup);
		frm.toggle_reqd(["drop_location", "drop_datetime"], needs_drop);
		if (!needs_pickup) {
			frm.set_value("pickup_location", null);
			frm.set_value("pickup_datetime", null);
		}
		if (!needs_drop) {
			frm.set_value("drop_location", null);
			frm.set_value("drop_datetime", null);
		}
	},

	hotel_required(frm) {
		frm.toggle_reqd(
			["check_in", "check_out"],
			frm.doc.hotel_required
		);
		if (frm.doc.hotel_required && !frm.doc.buggy_required) {
			frm.set_value("buggy_required", 1);
		}
	},

	check_in(frm) { frm.trigger("_recalc_nights"); },
	check_out(frm) { frm.trigger("_recalc_nights"); },
	_recalc_nights(frm) {
		if (frm.doc.check_in && frm.doc.check_out) {
			const nights = frappe.datetime.get_day_diff(
				frm.doc.check_out,
				frm.doc.check_in
			);
			frm.set_value("nights", nights > 0 ? nights : 0);
		}
	},

	factory_tour_required(frm) {
		frm.toggle_reqd(["tour_guide"], frm.doc.factory_tour_required);
		if (frm.doc.factory_tour_required && !frm.doc.buggy_required) {
			frm.set_value("buggy_required", 1);
		}
	},

	tour_date(frm) {
		if (
			frm.doc.tour_date
			&& frm.doc.buggy_required
			&& !frm.doc.buggy_datetime
		) {
			const time = frm.doc.tour_start_time || "09:00:00";
			frm.set_value("buggy_datetime", `${frm.doc.tour_date} ${time}`);
		}
	},

	tour_start_time(frm) {
		if (frm.doc.tour_date && frm.doc.tour_start_time && frm.doc.buggy_required) {
			frm.set_value(
				"buggy_datetime",
				`${frm.doc.tour_date} ${frm.doc.tour_start_time}`
			);
		}
	},

	buggy_required(frm) {
		frm.toggle_reqd(
			["buggy_pickup_point", "buggy_datetime"],
			frm.doc.buggy_required
		);
	},

	greeting_required(frm) {
		frm.toggle_reqd(
			["greeting_type", "greeting_delivery_time"],
			frm.doc.greeting_required
		);
	},
});

// ── Financial approval for cab / hotel (Phase 5) ──────────────────────────────
const VMS_HR_FIN = "visitormanagement.visitor_management.hospitality_extensions";

frappe.ui.form.on("Hospitality Request", {
	refresh(frm) {
		if (frm.is_new() || !frm.doc.requires_financial_approval) return;
		const status = frm.doc.financial_approval_status;
		const has = (roles) => roles.some((r) => frappe.user.has_role(r));

		// bookings can be confirmed only after Finance approval
		const locked = status !== "Finance Approved";
		["cab_booking_confirmed", "hotel_booking_confirmed"].forEach((f) =>
			frm.set_df_property(f, "read_only", locked ? 1 : 0)
		);
		if (locked) {
			frm.dashboard.set_headline(
				__("Cab / Hotel financial approval: {0}", [`<b>${__(status)}</b>`]),
				status === "Rejected" ? "red" : "orange"
			);
		}

		if (["Pending Estimate", "Rejected"].includes(status) && has(["Transport Coordinator", "Hospitality Manager", "System Manager"])) {
			frm.add_custom_button(__("Submit Estimate"), () => {
				frappe.prompt(
					[
						{ fieldname: "estimated_cab_cost", label: __("Estimated Cab Cost"), fieldtype: "Currency", default: frm.doc.estimated_cab_cost },
						{ fieldname: "estimated_hotel_cost", label: __("Estimated Hotel Cost"), fieldtype: "Currency", default: frm.doc.estimated_hotel_cost },
						{ fieldname: "cost_estimate_notes", label: __("Notes"), fieldtype: "Small Text", default: frm.doc.cost_estimate_notes },
					],
					(v) => vms_hr_call(frm, "submit_estimate", v),
					__("Submit Cost Estimate"),
					__("Submit")
				);
			}, __("Financial Approval"));
		}
		if (status === "Estimate Submitted" && has(["HOD", "System Manager"])) {
			vms_hr_decision_buttons(frm, "dept_head_decision", __("Dept Head"));
		}
		if (status === "Dept Head Approved" && has(["VMS Finance Approver", "System Manager"])) {
			vms_hr_decision_buttons(frm, "finance_decision", __("Finance"));
		}
	},
});

function vms_hr_decision_buttons(frm, method, stage) {
	frm.add_custom_button(__("Approve ({0})", [stage]), () => {
		frappe.prompt({ fieldname: "remarks", label: __("Remarks"), fieldtype: "Small Text" },
			(v) => vms_hr_call(frm, method, { approve: 1, remarks: v.remarks }), __("Approve"), __("Approve"));
	}, __("Financial Approval"));
	frm.add_custom_button(__("Reject ({0})", [stage]), () => {
		frappe.prompt({ fieldname: "remarks", label: __("Reason"), fieldtype: "Small Text", reqd: 1 },
			(v) => vms_hr_call(frm, method, { approve: 0, remarks: v.remarks }), __("Reject"), __("Reject"));
	}, __("Financial Approval"));
}

function vms_hr_call(frm, method, args) {
	frappe.call({
		method: `${VMS_HR_FIN}.${method}`,
		args: Object.assign({ hospitality_request: frm.doc.name }, args),
		freeze: true,
		callback: () => frm.reload_doc(),
	});
}
