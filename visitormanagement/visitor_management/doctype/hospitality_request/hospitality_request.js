// For license information, please see license.txt

// Each service asked for: its flag, its progress field and its label.
const HOSPITALITY_SERVICES = [
	["meal_required", "meal_status", __("Meal")],
	["cab_required", "cab_status", __("Cab")],
	["hotel_required", "hotel_status", __("Hotel")],
	["factory_tour_required", "tour_status", __("Factory Tour")],
	["buggy_required", "buggy_status", __("Buggy")],
	["greeting_required", "greeting_status", __("Greeting")],
];

function is_booked(frm, status_field) {
	return ["Booked", "Done"].includes(frm.doc[status_field]);
}

function sync_reqd_flags(frm) {
	// UI-only sync of required-field markers. Must NOT call set_value() — doing
	// so on refresh dirties the form and shows "Not Saved" before the user has
	// touched anything.
	//
	// The details a service needs are asked for when it is marked Booked, not
	// before approval: the Hospitality Manager approves what was asked for, and
	// whoever arranges it fills in the details as they become known.
	const t = frm.doc.cab_type;
	const needs_pickup = t === "Pickup" || t === "Both";
	const cab = frm.doc.cab_required && is_booked(frm, "cab_status");
	frm.toggle_reqd(["cab_type"], frm.doc.cab_required);
	frm.toggle_reqd(["pickup_location", "pickup_datetime"], cab && needs_pickup);
	frm.toggle_reqd(["drop_location", "drop_datetime"], cab && t === "Drop");
	frm.toggle_reqd(["check_in", "check_out"], frm.doc.hotel_required);
	frm.toggle_reqd(["hotel_name"], frm.doc.hotel_required && is_booked(frm, "hotel_status"));
	frm.toggle_reqd(
		["tour_guide", "tour_start_time"],
		frm.doc.factory_tour_required && is_booked(frm, "tour_status")
	);
	frm.toggle_reqd(
		["buggy_pickup_point"],
		frm.doc.buggy_required && is_booked(frm, "buggy_status")
	);
	frm.toggle_reqd(
		["greeting_type"],
		frm.doc.greeting_required && is_booked(frm, "greeting_status")
	);
}

// "Meal: Done · Cab: Booked · Hotel: Pending" — what is left to do, at a glance.
function hospitality_progress_line(frm) {
	const parts = HOSPITALITY_SERVICES.filter(([flag]) => frm.doc[flag]).map(
		([, status, label]) => `${label}: ${__(frm.doc[status] || "Pending")}`
	);
	return parts.length ? parts.join(" · ") : "";
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
				"Waiting for the Hospitality Manager. Approve what was asked for; booking details can be filled in now or after approval."
			),
			"orange"
		);
	} else if (frm.doc.docstatus === 1) {
		const progress = hospitality_progress_line(frm);
		frm.set_intro(
			frm.doc.status === "Completed"
				? __("Completed. Everything asked for has been delivered.")
				: __(
						"Approved. Set each service to Booked when it is arranged (the host is told) and Done when it has been delivered. To hand a service to a colleague, use Assigned To on the left."
				  ),
			"green"
		);
		if (progress) {
			frm.set_intro(__("Progress: {0}", [frappe.utils.escape_html(progress)]), "blue");
		}
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
		sync_reqd_flags(frm);
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
		sync_reqd_flags(frm);
	},

	greeting_required(frm) {
		sync_reqd_flags(frm);
	},

	// Marking a service Booked asks for the details it needs.
	meal_status: sync_reqd_flags,
	cab_status: sync_reqd_flags,
	hotel_status: sync_reqd_flags,
	tour_status: sync_reqd_flags,
	buggy_status: sync_reqd_flags,
	greeting_status: sync_reqd_flags,
});
