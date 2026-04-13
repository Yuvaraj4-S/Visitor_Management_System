// Copyright (c) 2026, Harthesh and contributors
// For license information, please see license.txt

async function render_visitor_headline(frm) {
	if (!frm.doc.visitor_pass) {
		frm.dashboard.clear_headline();
		return;
	}
	try {
		const vp = await frappe.db.get_doc("Visitor Pass", frm.doc.visitor_pass);
		const type_color = {
			"VIP": "#d4af37",
			"Contractor": "#e67e22",
			"Customer": "#27ae60",
			"Supplier": "#2980b9",
			"Candidate": "#8e44ad",
		}[vp.visitor_type] || "#34495e";
		frm.dashboard.set_headline(
			`<div style="display:flex;gap:16px;align-items:center;flex-wrap:wrap;font-size:13px;">`
			+ `<b style="font-size:14px;">${frappe.utils.escape_html(vp.visitor_full_name || "-")}</b>`
			+ `<span style="background:${type_color};color:#fff;padding:2px 10px;border-radius:10px;font-weight:600;">`
			+ `${frappe.utils.escape_html(vp.visitor_type || "-")}</span>`
			+ `<span>📞 ${frappe.utils.escape_html(vp.mobile_number || "-")}</span>`
			+ `<span>🏢 ${frappe.utils.escape_html(vp.company__organisation || "-")}</span>`
			+ `<span>👤 Host: ${frappe.utils.escape_html(vp.person_to_visit || "-")}</span>`
			+ `<span>📅 ${frappe.utils.escape_html(vp.visit_date || "-")}</span>`
			+ `</div>`
		);
	} catch (e) {
		frm.dashboard.clear_headline();
	}
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
		render_visitor_headline(frm);
		frm.trigger("cab_required");
		frm.trigger("hotel_required");
		frm.trigger("factory_tour_required");
		frm.trigger("buggy_required");
		frm.trigger("greeting_required");
	},

	visitor_pass(frm) {
		render_visitor_headline(frm);
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
			frm.set_value("flight_train_no", null);
		}
		if (!needs_drop) {
			frm.set_value("drop_location", null);
			frm.set_value("drop_datetime", null);
		}
	},

	hotel_required(frm) {
		frm.toggle_reqd(
			["hotel_name", "check_in", "check_out"],
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
		frm.toggle_reqd(
			["tour_date", "tour_guide"],
			frm.doc.factory_tour_required
		);
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
			["greeting_type", "greeting_delivery_time", "greeting_assigned_to"],
			frm.doc.greeting_required
		);
	},
});
