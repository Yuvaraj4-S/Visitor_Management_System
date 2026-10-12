// For license information, please see license.txt

const SERVICE_FLAGS = [
	"meal_required",
	"cab_required",
	"hotel_required",
	"factory_tour_required",
	"buggy_required",
	"greeting_required",
];

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
		add_assign_to_vms_user_button(frm);
	},

	visitor_pass(frm) {
		// Service checkboxes fetch from the Visitor Pass; the team can still change them here.
		if (!frm.doc.visitor_pass || frm.doc.docstatus !== 0) return;
		frappe.db
			.get_value("Visitor Pass", frm.doc.visitor_pass, SERVICE_FLAGS)
			.then(({ message }) => {
				if (!message) return;
				SERVICE_FLAGS.forEach((f) => frm.set_value(f, message[f] ? 1 : 0));
			});
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

// VMS Manager assigns the request to a VMS User, who then enters the cab / meal /
// factory-tour / greeting details. Assignment = ToDo + email/bell to the VMS User.
function add_assign_to_vms_user_button(frm) {
	if (frm.is_new() || frm.doc.docstatus === 2) return;
	if (!frappe.user.has_role("VMS Manager") && !frappe.user.has_role("System Manager")) return;

	frm.add_custom_button(__("Assign to VMS User"), () => {
		frappe.call({
			method: "visitormanagement.visitor_management.doctype.hospitality_request.hospitality_request.get_vms_users",
			callback: ({ message }) => {
				const users = message || [];
				if (!users.length) {
					frappe.msgprint(__("No active user has the VMS User role yet. Give the role to someone first."));
					return;
				}
				const assigned = JSON.parse(frm.doc._assign || "[]");
				const dialog = new frappe.ui.Dialog({
					title: __("Assign to VMS User"),
					fields: [
						{
							fieldname: "user",
							fieldtype: "Select",
							label: __("VMS User"),
							reqd: 1,
							options: users.map((u) => ({
								label: `${u.full_name || u.name}${assigned.includes(u.name) ? " " + __("(already assigned)") : ""}`,
								value: u.name,
							})),
						},
						{ fieldname: "note", fieldtype: "Small Text", label: __("Note for the VMS User") },
					],
					primary_action_label: __("Assign"),
					primary_action(values) {
						frappe.call({
							method: "visitormanagement.visitor_management.doctype.hospitality_request.hospitality_request.assign_to_vms_user",
							args: { request_name: frm.doc.name, user: values.user, note: values.note },
							freeze: true,
							callback: () => {
								dialog.hide();
								frappe.show_alert({ message: __("Assigned to {0}", [values.user]), indicator: "green" });
								frm.reload_doc();
							},
						});
					},
				});
				dialog.show();
			},
		});
	});
}
