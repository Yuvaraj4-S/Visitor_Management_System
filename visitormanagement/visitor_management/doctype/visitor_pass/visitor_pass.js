// Copyright (c) 2026, Harthesh and contributors
// For license information, please see license.txt

frappe.ui.form.on("Visitor Pass", {
	refresh(frm) {
		ensure_customer_crm_defaults(frm);
		setup_supplier_pass_query(frm);
		apply_visitor_pass_ui(frm);
		add_action_buttons(frm);
	},

	visitor_type(frm) {
		ensure_customer_crm_defaults(frm);
		setup_supplier_pass_query(frm);
		apply_visitor_pass_ui(frm);
	},

	crm_reference_type(frm) {
		if (frm.doc.crm_lead_opportunity) {
			frm.set_value("crm_lead_opportunity", "");
		}
	},

	crm_lead_opportunity(frm) {
		fetch_customer_crm_details(frm);
	},

	supplier_visit_mode(frm) {
		apply_visitor_pass_ui(frm);
	},

	entry_type(frm) {
		if (frm.doc.entry_type === "New") {
			frm.set_value("existing_visitor_pass", "");
			frm.set_value("visitor_full_name", "");
			frm.set_value("mobile_number", "");
			frm.set_value("email_id", "");
			frm.set_value("company__organisation", "");
		}

		apply_visitor_pass_ui(frm);
	},

	existing_visitor_pass(frm) {
		if (!frm.doc.existing_visitor_pass) {
			apply_visitor_pass_ui(frm);
			return;
		}

		frappe.db.get_doc("Visitor Pass", frm.doc.existing_visitor_pass).then((doc) => {
			frm.set_value("visitor_full_name", doc.visitor_full_name);
			frm.set_value("mobile_number", doc.mobile_number);
			frm.set_value("email_id", doc.email_id);
			frm.set_value("company__organisation", doc.company__organisation);
			frm.set_value("id_proof_type", doc.id_proof_type);
			frm.set_value("id_proof_number", doc.id_proof_number);
			apply_visitor_pass_ui(frm);
		});
	},

	meeting_outcome(frm) {
		apply_visitor_pass_ui(frm);
	},

	meal_required(frm) {
		apply_visitor_pass_ui(frm);
	},

	refreshments_required(frm) {
		apply_visitor_pass_ui(frm);
	},

	interpreter_required(frm) {
		apply_visitor_pass_ui(frm);
	},

	multi_day_pass(frm) {
		apply_visitor_pass_ui(frm);
	},

	status(frm) {
		apply_visitor_pass_ui(frm);
	},

	workflow_state(frm) {
		apply_visitor_pass_ui(frm);
	},
});

function apply_visitor_pass_ui(frm) {
	apply_visitor_pass_field_rules(frm);
	set_visitor_pass_intro(frm);
	set_visitor_pass_headline(frm);
}

function ensure_customer_crm_defaults(frm) {
	if (frm.doc.visitor_type === "Customer" && !frm.doc.crm_reference_type) {
		frm.set_value("crm_reference_type", "Lead");
		return;
	}

	if (frm.doc.visitor_type !== "Customer" && (frm.doc.crm_reference_type || frm.doc.crm_lead_opportunity)) {
		frm.set_value({
			crm_reference_type: "",
			crm_lead_opportunity: "",
		});
	}
}

function fetch_customer_crm_details(frm) {
	if (frm.doc.visitor_type !== "Customer" || !frm.doc.crm_reference_type || !frm.doc.crm_lead_opportunity) {
		return;
	}

	frappe.call({
		method: "visitormanagement.visitor_management.doctype.visitor_pass.visitor_pass.get_customer_visit_details",
		args: {
			reference_type: frm.doc.crm_reference_type,
			reference_name: frm.doc.crm_lead_opportunity,
		},
		callback: ({ message }) => {
			if (!message) {
				return;
			}

			frm.set_value({
				visitor_full_name: message.visitor_full_name || "",
				mobile_number: message.mobile_number || "",
				email_id: message.email_id || "",
				company__organisation: message.company__organisation || "",
				sales_executive: message.sales_executive || "",
			});

			if (message.owner_user && !message.sales_executive) {
				frappe.show_alert(
					{
						message: __(
							"CRM owner {0} has no linked Employee, so Sales Executive was not auto-filled.",
							[message.owner_user]
						),
						indicator: "orange",
					},
					7
				);
			}
		},
	});
}

function apply_visitor_pass_field_rules(frm) {
	const is_supplier_existing = frm.doc.visitor_type === "Supplier" && frm.doc.entry_type === "Existing";
	const is_supplier_delivery =
		frm.doc.visitor_type === "Supplier" && frm.doc.supplier_visit_mode === "Delivery";
	const is_supplier_business =
		frm.doc.visitor_type === "Supplier" &&
		!!frm.doc.supplier_visit_mode &&
		frm.doc.supplier_visit_mode !== "Delivery";
	const is_follow_up = frm.doc.visitor_type === "Customer" && frm.doc.meeting_outcome === "Follow-Up Needed";
	const needs_interpreter = frm.doc.visitor_type === "VIP" && !!frm.doc.interpreter_required;
	const is_multi_day_contractor = frm.doc.visitor_type === "Contractor" && !!frm.doc.multi_day_pass;
	const has_contractor_nda = frm.doc.visitor_type === "Contractor" && !!frm.doc.contractor_nda_signed;
	const has_ppe_proof = frm.doc.visitor_type === "Contractor" && !!frm.doc.ppe_provided;
	const hospitality_requested =
		!!frm.doc.meal_required || !!frm.doc.refreshments_required || !!frm.doc.conference_room;
	const hospitality_recorded = hospitality_requested || !!frm.doc.hospitality_request;

	[
		"status",
		"workflow_state",
		"approval_date",
		"approved_by",
		"badge_number",
		"qr_code_image",
		"gate_verified_photo",
		"gate_verified_on",
		"gate_verified_by",
		"host_department",
		"item_verification_status",
		"items_verified",
		"all_items_verified",
		"actual_checkin",
		"actual_checkout",
		"no_show",
		"current_location",
		"last_health_screening",
		"health_screening_status",
		"hospitality_request",
	].forEach((fieldname) => frm.set_df_property(fieldname, "read_only", 1));

	frm.set_df_property("items_verification_status", "hidden", 1);
	frm.toggle_display("existing_visitor_pass", is_supplier_existing);
	frm.toggle_reqd("existing_visitor_pass", is_supplier_existing);
	frm.toggle_display(
		[
			"purchase_order",
			"delivery_note",
			"goods_received_by",
			"driver_id_number",
			"dock_bay_assigned",
			"store_officer",
			"goods_description",
		],
		is_supplier_delivery
	);
	frm.toggle_display(
		[
			"meeting_subject",
			"meeting_start_time",
			"meeting_end_time",
			"meeting_room",
			"attendees",
			"refreshments_required",
			"refreshment_notes",
			"presentation_material",
			"nda_required",
			"documents_shared",
		],
		is_supplier_business
	);
	frm.toggle_reqd("purchase_order", is_supplier_delivery);
	frm.toggle_reqd("meeting_subject", is_supplier_business);

	frm.toggle_display("followup_date", is_follow_up);
	frm.toggle_reqd("followup_date", is_follow_up);

	frm.toggle_display("interpreter_language", needs_interpreter);
	frm.toggle_reqd("interpreter_language", needs_interpreter);

	frm.toggle_display("pass_valid_until", is_multi_day_contractor);
	frm.toggle_reqd("pass_valid_until", is_multi_day_contractor);
	frm.toggle_display("contractor_nda_document", has_contractor_nda);
	frm.toggle_reqd("contractor_nda_document", has_contractor_nda);
	frm.toggle_display("ppe_provided_document", has_ppe_proof);
	frm.toggle_reqd("ppe_provided_document", has_ppe_proof);

	[
		"meal_type",
		"number_of_people",
		"special_diet",
		"food_dept_staff_assigned",
		"food_status",
		"hospitality_request",
	].forEach((fieldname) => frm.toggle_display(fieldname, hospitality_recorded));

	frm.toggle_display(["conference_room", "service_time"], true);
	frm.toggle_display(["rest_area", "hospitality_notes"], hospitality_recorded);
}

function set_visitor_pass_intro(frm) {
	const stage = get_pass_stage(frm);
	const approval_lane = get_approval_lane(frm.doc.visitor_type);

	if (frm.is_new()) {
		frm.set_intro(
			__(
				"Complete the Visitor Profile and Visit Plan first, then fill the section that matches the selected visitor type before submitting."
			),
			"blue"
		);
		return;
	}

	if (stage.startsWith("Pending")) {
		frm.set_intro(
			approval_lane
				? __("Awaiting approval from {0}. Review the request snapshot and visit-specific details carefully.", [
						approval_lane,
				  ])
				: __("Awaiting approval. Review the visitor details before taking action."),
			"orange"
		);
		return;
	}

	if (stage === "Approved") {
		frm.set_intro(
			__(
				"Approved. Security can now verify declared items, issue the badge, and record the visitor check-in."
			),
			"green"
		);
		return;
	}

	if (stage === "Items Verified") {
		frm.set_intro(
			__("Items are verified and the pass is gate-ready. Proceed with Security Log check-in."),
			"blue"
		);
		return;
	}

	if (stage === "Checked-In") {
		frm.set_intro(
			__("Visitor is currently inside the premises. Use Security Log to record checkout when they exit."),
			"green"
		);
		return;
	}

	if (stage === "Checked-Out") {
		frm.set_intro(__("Visit completed and gate exit recorded."), "blue");
		return;
	}

	if (stage === "Rejected") {
		frm.set_intro(
			__("Request rejected. Update the details and reapply if the visit still needs to happen."),
			"red"
		);
		return;
	}

	frm.set_intro(null);
}

function set_visitor_pass_headline(frm) {
	if (!frm.dashboard) return;

	const stage = get_pass_stage(frm);
	const visitor_type = frm.doc.visitor_type || __("Visitor");
	const visit_date = frm.doc.visit_date ? frappe.datetime.str_to_user(frm.doc.visit_date) : __("Date Pending");
	const headline = frm.doc.badge_number
		? __("{0} | {1} | Badge {2}", [visitor_type, stage, frm.doc.badge_number])
		: __("{0} | {1} | {2}", [visitor_type, stage, visit_date]);

	frm.dashboard.clear_headline();
	frm.dashboard.set_headline(headline, get_pass_stage_color(stage));
}

function add_action_buttons(frm) {
	if (frm.is_new()) return;

	if (frm.doc.hospitality_request) {
		frm.add_custom_button(
			__("Open Hospitality"),
			() => frappe.set_route("Form", "Hospitality Request", frm.doc.hospitality_request),
			__("Actions")
		);
	}
}

function setup_supplier_pass_query(frm) {
	if (frm.doc.visitor_type !== "Supplier") return;

	frm.set_query("existing_visitor_pass", () => ({
		filters: {
			visitor_type: "Supplier",
		},
	}));
}

function get_pass_stage(frm) {
	return frm.doc.workflow_state || frm.doc.status || __("Draft");
}

function get_approval_lane(visitor_type) {
	const lane = {
		Contractor: __("Visitor Manager"),
		Supplier: __("Visitor Manager"),
		Customer: __("Sales Manager"),
		Candidate: __("HR Manager"),
		VIP: __("HOD / CEO"),
	};

	return lane[visitor_type];
}

function get_pass_stage_color(stage) {
	if (["Approved", "Checked-In"].includes(stage)) return "green";
	if (["Pending Approval", "Pending Visitor Manager", "Pending Sales Manager", "Pending HR Manager", "Pending HOD", "Pending CEO"].includes(stage)) return "orange";
	if (["Rejected", "Cancelled"].includes(stage)) return "red";
	if (["Items Verified", "Checked-Out"].includes(stage)) return "blue";
	return "gray";
}
