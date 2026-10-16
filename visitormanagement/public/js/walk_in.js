// "New Walk-In": register a visitor standing at the gate in one short form.
//
// Loaded only on Visitor Pass (form and list) — hooks.py doctype_js /
// doctype_list_js — so nothing here runs on any other app's pages. The server
// side is visitor_management/walk_in.py: it creates the pass for today with the
// "Walk-In" channel and sends it for approval through the normal approval step.

const VMS_WALK_IN_API = "visitormanagement.visitor_management.walk_in.create_walk_in_pass";
const VMS_WALK_IN_LINK_QUERY = "visitormanagement.visitor_management.link_details.link_query";
// How long a walk-in usually stays; the guard can change it.
const VMS_WALK_IN_DEFAULT_HOURS = 2;

const VMS_WALK_IN_ENABLED_API = "visitormanagement.visitor_management.walk_in.walk_in_enabled";
let vms_walk_in_enabled_promise = null;

// VMS Settings -> Let the Gate Register Walk-In Visitors. Asked once per page
// load; `callback` runs only when it is on.
function vms_when_walk_in_enabled(callback) {
	if (!vms_walk_in_enabled_promise) {
		vms_walk_in_enabled_promise = frappe.xcall(VMS_WALK_IN_ENABLED_API).catch(() => false);
	}
	vms_walk_in_enabled_promise.then((enabled) => enabled && callback());
}

function vms_walk_in_times() {
	const now = moment();
	const until = moment(now).add(VMS_WALK_IN_DEFAULT_HOURS, "hours");
	// Never past midnight: a walk-in pass is for today.
	const end = until.isSame(now, "day") ? until : moment(now).endOf("day").subtract(1, "minute");
	return { checkin: now.format("HH:mm:ss"), checkout: end.format("HH:mm:ss") };
}

function vms_open_walk_in_dialog(on_done) {
	if (!vms_walk_in_enabled_promise) {
		vms_walk_in_enabled_promise = frappe.xcall(VMS_WALK_IN_ENABLED_API).catch(() => false);
	}
	vms_walk_in_enabled_promise.then((enabled) => {
		if (enabled) {
			vms_build_walk_in_dialog(on_done);
			return;
		}
		frappe.msgprint({
			title: __("Walk-Ins Switched Off"),
			indicator: "orange",
			message: __(
				"Walk-in registration is switched off in VMS Settings. Ask the host to raise a Visitor Pass."
			),
		});
	});
}

function vms_build_walk_in_dialog(on_done) {
	const times = vms_walk_in_times();
	const dialog = new frappe.ui.Dialog({
		title: __("New Walk-In Visitor"),
		size: "large",
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "intro",
				options: `<div class="text-muted small" style="margin-bottom:8px;">${__(
					"For a visitor at the gate today. The pass is sent for approval as soon as you press Create; you are told when it is approved."
				)}</div>`,
			},
			{ fieldtype: "Section Break", label: __("Who") },
			{
				fieldtype: "Link",
				fieldname: "visitor_type",
				label: __("Visitor Type"),
				options: "Visitor Type",
				reqd: 1,
				get_query: () => ({ filters: { is_active: 1 } }),
			},
			{
				fieldtype: "Data",
				fieldname: "visitor_full_name",
				label: __("Visitor Name"),
				reqd: 1,
			},
			{
				fieldtype: "Data",
				fieldname: "mobile_number",
				label: __("Mobile Number"),
				options: "Phone",
				reqd: 1,
			},
			{
				fieldtype: "Data",
				fieldname: "company__organisation",
				label: __("Company / Organisation"),
			},
			{
				fieldtype: "Data",
				fieldname: "email_id",
				label: __("Email (optional)"),
				options: "Email",
				description: __("If given, the QR pass is emailed to the visitor."),
			},
			{ fieldtype: "Column Break" },
			{
				fieldtype: "Link",
				fieldname: "id_proof_type",
				label: __("ID Proof Type"),
				options: "ID Proof Type",
				reqd: 1,
				get_query: () => ({ filters: { is_active: 1 } }),
			},
			{
				fieldtype: "Data",
				fieldname: "id_proof_number_entry",
				label: __("ID Proof Number"),
				reqd: 1,
			},
			{
				fieldtype: "Attach Image",
				fieldname: "visitor_photo",
				label: __("Visitor Photo"),
				reqd: 1,
				description: __("Attach → Camera takes it now."),
			},
			{
				fieldtype: "Attach Image",
				fieldname: "id_proof_scan",
				label: __("ID Proof Scan"),
				reqd: 1,
			},
			{ fieldtype: "Section Break", label: __("Visit") },
			{
				fieldtype: "Link",
				fieldname: "person_to_visit",
				label: __("Person to Visit"),
				options: "Employee",
				reqd: 1,
				// Guards cannot read Employee records; the app's own picker returns name and label only.
				ignore_link_validation: 1,
				get_query: () => ({
					query: VMS_WALK_IN_LINK_QUERY,
					filters: { status: "Active", link_fieldname: "person_to_visit" },
				}),
				onchange() {
					vms_walk_in_show_host(dialog);
				},
			},
			{
				fieldtype: "Small Text",
				fieldname: "purpose_of_visit",
				label: __("Purpose of Visit"),
				reqd: 1,
			},
			{ fieldtype: "Column Break" },
			{
				fieldtype: "Time",
				fieldname: "expected_checkin",
				label: __("Check-In"),
				default: times.checkin,
				reqd: 1,
			},
			{
				fieldtype: "Time",
				fieldname: "expected_checkout",
				label: __("Expected Check-Out"),
				default: times.checkout,
				reqd: 1,
				description: __("About {0} hours from now; change it if needed.", [
					VMS_WALK_IN_DEFAULT_HOURS,
				]),
			},
			{ fieldtype: "Data", fieldname: "vehicle_number", label: __("Vehicle Number") },
			{ fieldtype: "Small Text", fieldname: "items_carried", label: __("Items Carried") },
		],
		primary_action_label: __("Create and Send for Approval"),
		primary_action(values) {
			dialog.disable_primary_action();
			frappe
				.xcall(VMS_WALK_IN_API, { values })
				.then((result) => {
					dialog.hide();
					if (result.submitted) {
						frappe.show_alert(
							{
								message: __(
									"{0} sent for approval. You will get a bell alert when it is approved.",
									[result.name]
								),
								indicator: "green",
							},
							10
						);
					} else {
						frappe.msgprint({
							title: __("Saved as Draft"),
							indicator: "orange",
							message: __("{0} was saved but not sent for approval: {1}", [
								frappe.utils.escape_html(result.name),
								frappe.utils.escape_html(result.message || ""),
							]),
						});
					}
					if (on_done) on_done(result);
					frappe.set_route("Form", "Visitor Pass", result.name);
				})
				.catch(() => dialog.enable_primary_action());
		},
	});
	// The app's picker checks which form is searching (link_details.can_pick).
	const host = dialog.fields_dict.person_to_visit;
	host.doctype = "Visitor Pass";
	host.df.parent = "Visitor Pass";
	dialog.show();
	return dialog;
}

function vms_walk_in_show_host(dialog) {
	const field = dialog.fields_dict.person_to_visit;
	const value = dialog.get_value("person_to_visit");
	if (!value) {
		field.set_description("");
		return;
	}
	frappe
		.xcall("visitormanagement.visitor_management.link_details.get_link_details", {
			doctype: "Employee",
			name: value,
			reference_doctype: "Visitor Pass",
			fieldname: "person_to_visit",
		})
		.then((details) => {
			const label = [details && details.employee_name, details && details.department]
				.filter(Boolean)
				.join(", ");
			field.set_description(label ? frappe.utils.escape_html(label) : "");
		})
		.catch(() => field.set_description(""));
}
