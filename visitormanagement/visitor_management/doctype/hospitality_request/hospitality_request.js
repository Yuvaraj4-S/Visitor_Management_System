// Copyright (c) 2026, Harthesh and contributors
// For license information, please see license.txt

frappe.ui.form.on("Hospitality Request", {
	refresh(frm) {
		if (!frm.is_new()) {
			frm.set_query("visitor_pass", () => ({
				filters: {
					docstatus: ["!=", 2],
				},
			}));
		}
		frm.trigger("cab_required");
		frm.trigger("hotel_required");
		frm.trigger("factory_tour_required");
		frm.trigger("buggy_required");
		frm.trigger("greeting_required");
	},

	cab_required(frm) {
		frm.toggle_reqd(
			["cab_type", "pickup_location"],
			frm.doc.cab_required
		);
	},

	hotel_required(frm) {
		frm.toggle_reqd(
			["hotel_name", "check_in", "check_out"],
			frm.doc.hotel_required
		);
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
		frm.toggle_reqd(
			["tour_date", "tour_guide"],
			frm.doc.factory_tour_required
		);
	},

	buggy_required(frm) {
		frm.toggle_reqd(
			["buggy_pickup_point", "buggy_datetime"],
			frm.doc.buggy_required
		);
	},

	greeting_required(frm) {
		frm.toggle_reqd(
			["greeting_type", "greeting_delivery_time", "greeting_assigned_to"],
			frm.doc.greeting_required
		);
	},
});
