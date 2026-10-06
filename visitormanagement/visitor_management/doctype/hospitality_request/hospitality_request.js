// For license information, please see license.txt

// Fulfilment progress of an approved request, in order (STATUS_STEPS in
// hospitality_request.py, which is where the rule is enforced).
const STATUS_STEPS = ["Pending", "Confirmed", "Served", "Completed"];

function sync_fulfilment_fields(frm) {
	// Status, Assigned Staff and Notes are the Hospitality Manager's. The server
	// refuses anyone else's change (_guard_fulfilment_fields); this only keeps
	// the form from offering an edit that would be refused. No set_value() here.
	const is_manager = frappe.user.has_role(["Hospitality Manager", "System Manager"]);
	["assigned_staff", "notes"].forEach((fieldname) =>
		frm.set_df_property(fieldname, "read_only", is_manager ? 0 : 1)
	);

	// Status follows the workflow until the request is Approved; from then on
	// the manager moves it forward only.
	const current = frm.doc.status || "Pending";
	const step = STATUS_STEPS.indexOf(current);
	const can_progress = is_manager && frm.doc.docstatus === 1 && step !== -1;
	frm.set_df_property(
		"status",
		"options",
		can_progress ? STATUS_STEPS.slice(step) : [...STATUS_STEPS, "Cancelled"]
	);
	frm.set_df_property("status", "read_only", can_progress ? 0 : 1);
}

function sync_reqd_flags(frm) {
	// UI-only sync of required-field markers. Must NOT call set_value() — doing
	// so on refresh dirties the form and shows "Not Saved" before the user has
	// touched anything. The server checks the same fields when the request is
	// approved (REQUIRED_FOR_APPROVAL in hospitality_request.py).
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
	setup(frm) {
		// Pickers for masters other apps own: searched through this app's query so
		// no role needs a permission on the ERPNext/HRMS DocType (see link_queries.py).
		const query = "visitormanagement.visitor_management.link_queries.search";
		[
			"assigned_staff",
			"tour_guide",
			"buggy_driver",
			"greeting_assigned_to",
			"cab_vendor",
			"hotel_name",
		].forEach((fieldname) => frm.set_query(fieldname, () => ({ query })));
		frm.set_query("responsible_person", "tour_areas", () => ({ query }));
	},

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
		sync_fulfilment_fields(frm);
	},

	// Which arrangements are wanted is decided on the Visitor Pass and only
	// fetched here. The handlers below used to tick Buggy Required whenever a
	// cab, hotel or tour was fetched, so the form showed a Buggy section and
	// demanded its fields — and on save the server copied the pass's own answer
	// back and threw what had been typed away.
	cab_required(frm) {
		frm.toggle_reqd(["cab_type"], frm.doc.cab_required);
		if (frm.doc.cab_required && !frm.doc.cab_type) {
			frm.set_value("cab_type", "Both");
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
