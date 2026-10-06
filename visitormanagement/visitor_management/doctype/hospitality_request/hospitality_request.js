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

// One line telling whoever opens the request what it is waiting for. Frappe
// empties the form's message area at the start of every refresh (form.js), so
// this adds exactly one line each time and never has to clear anything itself.
function set_hospitality_intro(frm) {
	if (frm.is_new()) {
		frm.set_intro(
			__(
				"Pick the Visitor Pass first. What was asked for on the pass (meal, cab, hotel, tour, buggy, greeting) is filled in when you save."
			),
			"blue"
		);
		return;
	}

	const state = frm.doc.workflow_state || "Draft";
	if (frm.doc.status === "Cancelled" || state === "Cancelled") {
		frm.set_intro(__("This request was called off. Nothing needs to be arranged."), "red");
	} else if (state === "Rejected") {
		frm.set_intro(__("This request was rejected. Nothing needs to be arranged."), "red");
	} else if (state === "Draft") {
		frm.set_intro(
			__(
				"Draft. It can be sent for approval once the Visitor Pass is approved; until then nothing should be booked."
			),
			"blue"
		);
	} else if (state === "Pending Approval") {
		frm.set_intro(
			__(
				"Waiting for the Hospitality Manager. Check the sections below and complete the details before approving."
			),
			"orange"
		);
	} else if (frm.doc.docstatus === 1) {
		frm.set_intro(
			__("Approved. Arrange what is ticked below and update Status as each part is done."),
			"green"
		);
	}
}

// This form's Links to HRMS/ERPNext DocTypes, searched through the app's own
// query (public/js/core_link_pickers.js).
const HOSPITALITY_REQUEST_CORE_LINKS = {
	assigned_staff: { status: "Active" },
	tour_guide: { status: "Active" },
	buggy_driver: { status: "Active" },
	greeting_assigned_to: { status: "Active" },
	cab_vendor: {},
	hotel_name: {},
	"tour_areas.responsible_person": { status: "Active" },
};

frappe.ui.form.on("Hospitality Request", {
	onload(frm) {
		vms_setup_core_link_pickers(frm, HOSPITALITY_REQUEST_CORE_LINKS);
	},

	refresh(frm) {
		vms_setup_core_link_pickers(frm, HOSPITALITY_REQUEST_CORE_LINKS);
		if (!frm.is_new()) {
			frm.set_query("visitor_pass", () => ({
				filters: {
					docstatus: ["!=", 2],
				},
			}));
		}
		// No frm.dashboard.clear_headline() here. On Frappe 15 the headline and
		// every form message share one container (layout.show_message), so that
		// call wiped whatever the form had just been told — set_intro() text and
		// Frappe's own "this form has been modified" warning alike.
		set_hospitality_intro(frm);
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
		frm.toggle_reqd(["check_in", "check_out"], frm.doc.hotel_required);
		if (frm.doc.hotel_required && !frm.doc.buggy_required) {
			frm.set_value("buggy_required", 1);
		}
	},

	check_in(frm) {
		frm.trigger("_recalc_nights");
	},
	check_out(frm) {
		frm.trigger("_recalc_nights");
	},
	_recalc_nights(frm) {
		if (frm.doc.check_in && frm.doc.check_out) {
			const nights = frappe.datetime.get_day_diff(frm.doc.check_out, frm.doc.check_in);
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
		if (frm.doc.tour_date && frm.doc.buggy_required && !frm.doc.buggy_datetime) {
			const time = frm.doc.tour_start_time || "09:00:00";
			frm.set_value("buggy_datetime", `${frm.doc.tour_date} ${time}`);
		}
	},

	tour_start_time(frm) {
		if (frm.doc.tour_date && frm.doc.tour_start_time && frm.doc.buggy_required) {
			frm.set_value("buggy_datetime", `${frm.doc.tour_date} ${frm.doc.tour_start_time}`);
		}
	},

	buggy_required(frm) {
		frm.toggle_reqd(["buggy_pickup_point", "buggy_datetime"], frm.doc.buggy_required);
	},

	greeting_required(frm) {
		frm.toggle_reqd(["greeting_type", "greeting_delivery_time"], frm.doc.greeting_required);
	},
});
