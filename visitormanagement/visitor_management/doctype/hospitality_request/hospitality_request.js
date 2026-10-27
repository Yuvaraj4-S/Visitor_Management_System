// For license information, please see license.txt

// Each service a request can carry: [key, its progress field, label, the flags
// that ask for it] (SERVICES in hospitality_request.py).
const HOSPITALITY_SERVICES = [
	["meal", "meal_status", __("Meal"), ["meal_required"]],
	[
		"refreshments",
		"refreshments_status",
		__("Refreshments"),
		["snacks_required", "tea_coffee_required"],
	],
	["cab", "cab_status", __("Cab"), ["cab_required"]],
	["hotel", "hotel_status", __("Hotel"), ["hotel_required"]],
	["tour", "tour_status", __("Factory Tour"), ["factory_tour_required"]],
	["buggy", "buggy_status", __("Buggy"), ["buggy_required"]],
	["greeting", "greeting_status", __("Greeting"), ["greeting_required"]],
];

// The booking details recorded with "Booked" (SERVICE_DETAIL_FIELDS in
// hospitality_request.py, which is where the rule is enforced).
const SERVICE_DETAIL_FIELDS = {
	cab: ["cab_vendor", "driver_name", "driver_phone"],
	hotel: ["hotel_name", "booking_reference", "hotel_cost"],
	tour: ["tour_guide"],
	buggy: ["buggy_number", "buggy_driver"],
	greeting: ["greeting_assigned_to", "greeting_cost"],
};

// What each progress can move to, as the buttons offer it.
const NEXT_SERVICE_STEPS = {
	Pending: ["Booked", "Done", "Cancelled"],
	Booked: ["Done", "Cancelled", "Pending"],
	Done: ["Pending"],
	Cancelled: ["Pending"],
};
const SERVICE_STEP_LABELS = {
	Booked: __("Mark Booked"),
	Done: __("Mark Done"),
	Cancelled: __("Call Off"),
	Pending: __("Back to Pending"),
};

const LINK_QUERY = "visitormanagement.visitor_management.link_queries.search";

function asked_services(frm) {
	return HOSPITALITY_SERVICES.filter(([, , , flags]) => flags.some((flag) => frm.doc[flag]));
}

// "Cab: Booked · Hotel: Pending · Meal: Done" — what is left to do, at a glance.
function services_progress_line(frm) {
	return asked_services(frm)
		.map(([, status, label]) => `${label}: ${__(frm.doc[status] || "Pending")}`)
		.join(" · ");
}

function sync_fulfilment_fields(frm) {
	// Assigned Staff and Notes are the Hospitality Manager's. The server refuses
	// anyone else's change (_guard_fulfilment_fields); this only keeps the form
	// from offering an edit that would be refused. No set_value() here. Status
	// and each service's progress are read-only: the status follows the
	// services, and a service is moved with the buttons below.
	const is_manager = frappe.user.has_role(["Hospitality Manager", "System Manager"]);
	["assigned_staff", "notes"].forEach((fieldname) =>
		frm.set_df_property(fieldname, "read_only", is_manager ? 0 : 1)
	);
}

function show_progress(frm) {
	if (frm.is_new() || frm.doc.docstatus !== 1) return;
	const line = services_progress_line(frm);
	if (line) frm.set_intro(__("Progress: {0}", [frappe.utils.escape_html(line)]), "blue");
}

// One button group per service the person opening the form delivers
// (`services_you_update`, from the server's may_update_service), offering the
// steps that service can take. Nothing for anybody else, and nothing before the
// request is Approved.
function add_service_buttons(frm) {
	if (frm.is_new() || frm.doc.docstatus !== 1 || frm.doc.workflow_state !== "Approved") return;
	const mine = (frm.doc.__onload && frm.doc.__onload.services_you_update) || [];
	asked_services(frm)
		.filter(([key]) => mine.includes(key))
		.forEach(([key, status_field, label]) => {
			const current = frm.doc[status_field] || "Pending";
			(NEXT_SERVICE_STEPS[current] || []).forEach((next) =>
				frm.add_custom_button(
					SERVICE_STEP_LABELS[next],
					() => move_service(frm, key, label, next),
					__("{0}: {1}", [label, __(current)])
				)
			);
		});
}

function move_service(frm, key, label, status) {
	const fields = SERVICE_DETAIL_FIELDS[key] || [];
	if (status === "Booked" && fields.length) {
		// The details go in with the click: the person booking the cab types the
		// driver here, without editing the request.
		const dialog = new frappe.ui.Dialog({
			title: __("{0}: Booked", [label]),
			fields: fields.map((fieldname) => {
				const df = frappe.meta.get_docfield(frm.doctype, fieldname, frm.doc.name);
				const field = {
					fieldname,
					fieldtype: df.fieldtype,
					label: __(df.label),
					options: df.options,
					default: frm.doc[fieldname],
				};
				if (df.fieldtype === "Link") field.get_query = () => ({ query: LINK_QUERY });
				return field;
			}),
			primary_action_label: __("Mark Booked"),
			primary_action(values) {
				dialog.hide();
				save_service(frm, key, status, values);
			},
		});
		dialog.show();
		return;
	}
	if (status === "Cancelled") {
		frappe.confirm(__("Call off the {0} for this visit?", [label]), () =>
			save_service(frm, key, status)
		);
		return;
	}
	save_service(frm, key, status);
}

function save_service(frm, key, status, details) {
	frappe
		.call({
			method: "visitormanagement.visitor_management.doctype.hospitality_request.hospitality_request.update_service_status",
			args: { request: frm.doc.name, service: key, status, details: details || {} },
			// Frozen until the server answers: a second click cannot send it twice.
			freeze: true,
		})
		.then((r) => {
			if (!r.exc) frm.reload_doc();
		});
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
		show_progress(frm);
		add_service_buttons(frm);
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
