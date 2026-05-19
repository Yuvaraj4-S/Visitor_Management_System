// For license information, please see license.txt

frappe.ui.form.on("Visitor Pass", {
	refresh(frm) {
		ensure_customer_crm_defaults(frm);
		setup_supplier_pass_query(frm);
		apply_visitor_pass_ui(frm);
		add_action_buttons(frm);
		add_hospitality_buttons(frm);
	},

	visitor_type(frm) {
		ensure_customer_crm_defaults(frm);
		setup_supplier_pass_query(frm);
		apply_visitor_pass_ui(frm);
	},

	factory_tour_required(frm) {
		if (frm.doc.factory_tour_required) {
			if (!frm.doc.buggy_required) {
				frm.set_value("buggy_required", 1);
			}
		} else if (frm.doc.buggy_required) {
			frm.set_value("buggy_required", 0);
		}
	},

	crm_reference_type(frm) {
		if (frm.doc.crm_lead_opportunity) {
			frm.set_value("crm_lead_opportunity", "");
		}
	},

	crm_lead_opportunity(frm) {
		fetch_customer_crm_details(frm);
	},

	supplier_link(frm) {
		if (frm.doc.supplier_link) {
			frappe.call({
				method: 'frappe.client.get',
				args: { doctype: 'Supplier', name: frm.doc.supplier_link },
				callback: function(r) {
					if (r.message) {
						frm.set_value('visitor_full_name', r.message.supplier_name);
						frm.set_value('mobile_number', r.message.mobile_no || '');
						frm.set_value('email_id', r.message.email_id || '');
						frm.set_value('company__organisation', r.message.supplier_name);
					}
				}
			});
		}
	},

	contractor_link(frm) {
		if (frm.doc.contractor_link) {
			frappe.call({
				method: 'frappe.client.get',
				args: { doctype: 'Supplier', name: frm.doc.contractor_link },
				callback: function(r) {
					if (r.message) {
						frm.set_value('visitor_full_name', r.message.supplier_name);
						frm.set_value('mobile_number', r.message.mobile_no || '');
						frm.set_value('email_id', r.message.email_id || '');
						frm.set_value('company__organisation', r.message.supplier_name);
					}
				}
			});
		}
	},

	job_applicant_link(frm) {
		if (frm.doc.job_applicant_link) {
			frappe.call({
				method: 'frappe.client.get',
				args: { doctype: 'Job Applicant', name: frm.doc.job_applicant_link },
				callback: function(r) {
					if (r.message) {
						frm.set_value('visitor_full_name', r.message.applicant_name);
						frm.set_value('mobile_number', r.message.phone_number || '');
						frm.set_value('email_id', r.message.email_id || '');
						frm.set_value('company__organisation', r.message.company_name || '');
					}
				}
			});
		}
	},

	mobile_number(frm) {
		preview_normalised_mobile(frm);
		lookup_existing_visitor_match(frm, "mobile_number");
	},

	id_proof_number(frm) {
		lookup_existing_visitor_match(frm, "id_proof_number");
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
			frm.set_value("id_proof_type", "");
			frm.set_value("id_proof_number", "");
			// Clear type-specific links
			if (frm.doc.visitor_type === "Supplier") {
				frm.set_value("supplier_link", "");
			} else if (frm.doc.visitor_type === "Customer") {
				frm.set_value("crm_reference_type", "");
				frm.set_value("crm_lead_opportunity", "");
			} else if (frm.doc.visitor_type === "Contractor") {
				frm.set_value("contractor_link", "");
				frm.set_value("work_order_ref", "");
			} else if (frm.doc.visitor_type === "Candidate") {
				frm.set_value("job_applicant_link", "");
			}
		}

		apply_visitor_pass_ui(frm);
	},

	existing_visitor_pass(frm) {
		if (!frm.doc.existing_visitor_pass) {
			apply_visitor_pass_ui(frm);
			return;
		}

		frappe.call({
			method:
				"visitormanagement.visitor_management.doctype.visitor_pass.visitor_pass.get_existing_visitor_pass_details",
			args: {
				visitor_pass: frm.doc.existing_visitor_pass,
				visitor_type: frm.doc.visitor_type,
			},
			callback: ({ message }) => {
				if (!message) {
					return;
				}
				apply_existing_pass_data(frm, message);
				apply_visitor_pass_ui(frm);
			},
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

	visit_date(frm) {
		refresh_hospitality_plan(frm);
	},

	expected_checkin(frm) {
		refresh_hospitality_plan(frm);
	},

	expected_checkout(frm) {
		refresh_hospitality_plan(frm);
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

function preview_normalised_mobile(frm) {
	// On save the server normalises the phone to "+91-XXXXXXXXXX". Show the
	// reception staff what they actually typed *will become*, so they catch
	// typos before submitting (a wrong +91 number = SMS approvals never land).
	const raw = (frm.doc.mobile_number || "").trim();
	if (!raw) {
		frm.set_df_property("mobile_number", "description", "");
		frm.refresh_field("mobile_number");
		return;
	}
	const digits = raw.replace(/\D/g, "");
	let normalised = raw;
	if (digits.length >= 10) {
		const last10 = digits.slice(-10);
		normalised = `+91-${last10}`;
	}
	const description = (normalised !== raw)
		? __("Will be saved as: <b>{0}</b>", [normalised])
		: __("✓ Format looks good");
	frm.set_df_property("mobile_number", "description", description);
	frm.refresh_field("mobile_number");
}

function apply_visitor_pass_ui(frm) {
	apply_visitor_pass_field_rules(frm);
	set_visitor_pass_intro(frm);
	if (frm.dashboard) frm.dashboard.clear_headline();
	apply_badge_visibility(frm);

	// Force Phone widget to re-render if mobile_number exists but display is blank.
	// Frappe's Phone control sometimes fails to parse "+91 XXXXXXXXXX" on initial load.
	if (frm.doc.mobile_number) {
		setTimeout(() => {
			const field = frm.get_field("mobile_number");
			if (field && field.$input && !field.$input.val()) {
				frm.refresh_field("mobile_number");
			}
		}, 400);
	}
}

// Hide badge_number / badge_colour when VMS Settings → enable_badge is off.
// Also respects badge_required_for (per-visitor-type opt-in list).
// Uses set_df_property("hidden", 1) + refresh_field — the most reliable
// force-hide in Frappe; toggle_display alone can flicker if the field
// was already painted before the async settings fetch resolved.
function apply_badge_visibility(frm) {
	const BADGE_FIELDS = ["badge_number", "badge_colour"];
	const setHidden = (hide) => {
		BADGE_FIELDS.forEach((fn) => {
			frm.set_df_property(fn, "hidden", hide ? 1 : 0);
			frm.toggle_display(fn, !hide);
			frm.refresh_field(fn);
		});
	};
	// Hide first so we never flash badge fields on before the fetch resolves.
	setHidden(true);
	frappe.db.get_value("VMS Settings", "VMS Settings",
			["enable_badge", "badge_required_for"])
		.then((r) => {
			const s = (r && r.message) || {};
			// Single-doctype fields come back as strings — "0" is truthy in JS.
			// Coerce to int with cint to get a real boolean.
			let show = !!cint(s.enable_badge);
			if (show) {
				const list = (s.badge_required_for || "").split(/[\n,]/)
					.map((x) => x.trim()).filter(Boolean);
				if (list.length && frm.doc.visitor_type
						&& !list.includes(frm.doc.visitor_type)) {
					show = false;
				}
			}
			setHidden(!show);
		});
}

function ensure_customer_crm_defaults(frm) {
	if (frm.doc.visitor_type === "Customer" && frm.doc.entry_type === "New" && !frm.doc.crm_reference_type) {
		frm.set_value("crm_reference_type", "Lead");
		return;
	}

	if (frm.doc.visitor_type !== "Customer" || frm.doc.entry_type !== "New") {
		if (frm.doc.crm_reference_type || frm.doc.crm_lead_opportunity) {
			frm.set_value({
				crm_reference_type: "",
				crm_lead_opportunity: "",
			});
		}
	}
}

function fetch_customer_crm_details(frm) {
	if (frm.doc.visitor_type !== "Customer" || !frm.doc.crm_reference_type || !frm.doc.crm_lead_opportunity) {
		return;
	}

	let doctype = frm.doc.crm_reference_type;
	if (doctype === "Customer") {
		doctype = "Customer";
	}

	frappe.call({
		method: "frappe.client.get",
		args: { doctype: doctype, name: frm.doc.crm_lead_opportunity },
		callback: ({ message }) => {
			if (!message) {
				return;
			}

			let visitor_full_name = "";
			let mobile_number = "";
			let email_id = "";
			let company__organisation = "";
			let sales_executive = "";

			if (frm.doc.crm_reference_type === "Lead") {
				visitor_full_name = message.lead_name || "";
				mobile_number = message.mobile_no || "";
				email_id = message.email_id || "";
				company__organisation = message.company_name || "";
				sales_executive = message.lead_owner || "";
			} else if (frm.doc.crm_reference_type === "Opportunity") {
				visitor_full_name = message.contact_display || message.customer_name || "";
				mobile_number = message.contact_mobile || "";
				email_id = message.contact_email || "";
				company__organisation = message.customer_name || "";
				sales_executive = message.opportunity_owner || "";
			} else if (frm.doc.crm_reference_type === "Customer") {
				visitor_full_name = message.customer_name || "";
				mobile_number = message.mobile_no || "";
				email_id = message.email_id || "";
				company__organisation = message.customer_name || "";
				// Sales executive might need to be fetched differently
			}

			frm.set_value({
				visitor_full_name: visitor_full_name,
				mobile_number: mobile_number,
				email_id: email_id,
				company__organisation: company__organisation,
				sales_executive: sales_executive,
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
	const is_existing = ['Supplier','Customer','Contractor','Candidate'].includes(frm.doc.visitor_type) && frm.doc.entry_type === "Existing";
	const is_follow_up = frm.doc.visitor_type === "Customer" && frm.doc.meeting_outcome === "Follow-Up Needed";
	const needs_interpreter = frm.doc.visitor_type === "VIP" && !!frm.doc.interpreter_required;
	const is_multi_day_contractor = frm.doc.visitor_type === "Contractor" && !!frm.doc.multi_day_pass;
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
		"hospitality_request",
	].forEach((fieldname) => frm.set_df_property(fieldname, "read_only", 1));

	frm.set_df_property("items_verification_status", "hidden", 1);
	frm.toggle_display("existing_visitor_pass", is_existing);
	frm.toggle_reqd("existing_visitor_pass", is_existing);
	frm.toggle_display("supplier_link", frm.doc.visitor_type === "Supplier" && frm.doc.entry_type === "New");
	frm.toggle_display("crm_reference_type", frm.doc.visitor_type === "Customer" && frm.doc.entry_type === "New");
	frm.toggle_display("crm_lead_opportunity", frm.doc.visitor_type === "Customer" && frm.doc.entry_type === "New");
	frm.toggle_display("contractor_link", frm.doc.visitor_type === "Contractor" && frm.doc.entry_type === "New");
	frm.toggle_display("work_order_ref", frm.doc.visitor_type === "Contractor" && frm.doc.entry_type === "New");
	frm.toggle_display("job_applicant_link", frm.doc.visitor_type === "Candidate" && frm.doc.entry_type === "New");
	const is_supplier_meeting =
		frm.doc.visitor_type === "Supplier" && frm.doc.supplier_visit_mode === "Meeting";
	frm.toggle_reqd("meeting_subject", is_supplier_meeting);

	frm.toggle_display("followup_date", is_follow_up);
	frm.toggle_reqd("followup_date", is_follow_up);

	frm.toggle_display("interpreter_language", needs_interpreter);
	frm.toggle_reqd("interpreter_language", needs_interpreter);

	frm.toggle_display("pass_valid_until", is_multi_day_contractor);
	frm.toggle_reqd("pass_valid_until", is_multi_day_contractor);

	// Hospitality field visibility is now DocType-driven:
	//   - assigned_meal_slots, hospitality_type, food_dept_staff_assigned,
	//     food_status, service_time, refreshments_required → hidden:1 in JSON
	//   - meal_type, number_of_people → depends_on:eval:doc.meal_required in JSON
	// special_diet, hospitality_request are always visible (no toggle needed).
	frm.toggle_display("conference_room", true);
	frm.toggle_display("hospitality_notes", hospitality_recorded);
}

function refresh_hospitality_plan(frm) {
	if (!frm.doc.visit_date || !frm.doc.expected_checkin || !frm.doc.expected_checkout) {
		frm.set_value({
			meal_required: 0,
			meal_type: "",
			assigned_meal_slots: "",
			hospitality_type: "",
			service_time: null,
		});
		apply_visitor_pass_ui(frm);
		return;
	}

	frappe.call({
		method: "visitormanagement.visitor_management.lifecycle.get_hospitality_meal_plan",
		args: {
			visit_date: frm.doc.visit_date,
			expected_checkin: frm.doc.expected_checkin,
			expected_checkout: frm.doc.expected_checkout,
		},
		callback: ({ message }) => {
			if (!message) {
				return;
			}

			frm.set_value({
				meal_required: message.meal_required || 0,
				meal_type: message.meal_type || "",
				assigned_meal_slots: message.assigned_meal_slots || "",
				hospitality_type: message.hospitality_type || "",
				service_time: message.service_time || null,
			});
			apply_visitor_pass_ui(frm);
		},
	});
}

function set_visitor_pass_intro(frm) {
	const stage = get_pass_stage(frm);
	const approval_lane = get_approval_lane(frm.doc.visitor_type);

	// Clear approver card at the start; it's re-rendered only for Pending stages.
	clear_approver_context_card(frm);

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
		render_approver_context_card(frm);
		return;
	}

	if (stage === "Approved") {
		frm.set_intro(
			__(
				frm.doc.visitor_type === "VIP"
					? "Approved. Security should use the VIP priority lane and issue the badge during gate check-in."
					: "Approved. Security can now verify declared items, issue the badge, and record the visitor check-in."
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

function render_approver_context_card(frm) {
	// One-glance card for approvers: visit details + attachment checklist.
	// Intentionally NO SLA timer and NO risk badge — kept minimal so approvers
	// see only verifiable facts about the request.
	if (!frm.dashboard || !frm.dashboard.wrapper) return;

	const photo_ok = !!frm.doc.visitor_photo;
	const id_ok = !!frm.doc.id_proof_scan;
	const items_declared = (frm.doc.visitor_items || []).length > 0
		|| !!(frm.doc.items_carried || "").trim();

	const checklist_item = (label, ok) => `
		<span style="display:inline-flex; align-items:center; gap:4px; padding:2px 8px; border-radius:999px; font-size:11px; font-weight:600; background:${ok ? "#d9f3e4" : "#fde2e2"}; color:${ok ? "#0d6b3e" : "#9b1c1c"};">
			${ok ? "✅" : "⚠️"} ${label}
		</span>
	`;

	const meta_row = (label, value) => `
		<div style="font-size:12px; color:#334e68; line-height:1.6;">
			<strong>${label}:</strong> ${value || "-"}
		</div>
	`;

	const visit_window = [
		frm.doc.visit_date || "",
		frm.doc.expected_checkin || "",
		frm.doc.expected_checkout ? "→ " + frm.doc.expected_checkout : "",
	].filter(Boolean).join(" ");

	const html = `
		<div class="vm-approver-card" style="border:1px solid #dbe3ea; border-radius:12px; padding:14px 16px; background:linear-gradient(180deg,#f8fafc 0%, #eef4f8 100%); margin-bottom:12px;">
			<div style="display:flex; align-items:center; justify-content:space-between; gap:10px; margin-bottom:10px; flex-wrap:wrap;">
				<div style="font-size:13px; font-weight:700; color:#102a43;">
					${__("Approver Snapshot")}
				</div>
				<div style="display:flex; gap:6px; flex-wrap:wrap;">
					${checklist_item(__("Visitor Photo"), photo_ok)}
					${checklist_item(__("ID Scan"), id_ok)}
					${checklist_item(__("Items Declared"), items_declared)}
				</div>
			</div>
			<div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap:8px;">
				${meta_row(__("Visitor"), frm.doc.visitor_full_name)}
				${meta_row(__("Type"), frm.doc.visitor_type)}
				${meta_row(__("Company"), frm.doc.company__organisation)}
				${meta_row(__("Host"), frm.doc.person_to_visit)}
				${meta_row(__("Visit Window"), visit_window)}
				${meta_row(__("Purpose"), frm.doc.purpose_of_visit)}
			</div>
		</div>
	`;

	clear_approver_context_card(frm);
	const $headline = frm.dashboard.wrapper.find(".form-headline").first();
	const $target = $headline.length ? $headline : $(frm.dashboard.wrapper);
	$target.before(`<div class="vm-approver-card-host">${html}</div>`);
}

function clear_approver_context_card(frm) {
	if (!frm.dashboard || !frm.dashboard.wrapper) return;
	frm.dashboard.wrapper.parent().find(".vm-approver-card-host").remove();
}

function add_action_buttons(frm) {
	// "Actions" group removed — "Open Hospitality" is already available
	// under the "Hospitality" group (see add_hospitality_buttons).
	return;
}

function setup_supplier_pass_query(frm) {
	if (!['Supplier','Customer','Contractor','Candidate'].includes(frm.doc.visitor_type)) return;

	frm.set_query("existing_visitor_pass", () => ({
		query: "visitormanagement.visitor_management.doctype.visitor_pass.visitor_pass.search_visitor_passes",
		filters: {
			visitor_type: frm.doc.visitor_type,
		},
	}));
}

function get_pass_stage(frm) {
	return frm.doc.workflow_state || frm.doc.status || __("Draft");
}

function get_approval_lane(visitor_type) {
	const lane = {
		Contractor: __("System Manager"),
		Supplier: __("System Manager"),
		Customer: __("Sales Manager"),
		Candidate: __("HR Manager"),
		VIP: __("HOD / CEO"),
	};

	return lane[visitor_type];
}

function get_pass_stage_color(stage) {
	if (["Approved", "Checked-In"].includes(stage)) return "green";
	if (["Pending Approval", "Pending System Manager", "Pending Sales Manager", "Pending HR Manager", "Pending HOD", "Pending CEO"].includes(stage)) return "orange";
	if (["Rejected", "Cancelled"].includes(stage)) return "red";
	if (["Items Verified", "Checked-Out"].includes(stage)) return "blue";
	return "gray";
}

function show_web_submissions_dialog(frm) {
	frappe.call({
		method: 'frappe.client.get_list',
		args: {
			doctype: 'Visitor Pass',
			filters: [
				['request_channel', '=', 'Portal'],
				['workflow_state', 'in', ['Pending System Manager', 'Pending Sales Manager', 'Pending HR Manager', 'Pending HOD', 'Pending CEO', 'Draft']]
			],
			fields: ['name', 'visitor_full_name', 'visitor_type', 'mobile_number', 'email_id', 'visit_date']
		},
		callback: function(r) {
			if (r.message && r.message.length > 0) {
				let dialog = new frappe.ui.Dialog({
					title: __('Pending Web Submissions'),
					fields: [
						{
							fieldtype: 'HTML',
							fieldname: 'submissions',
							options: generate_submissions_html(r.message, frm)
						}
					],
					size: 'large'
				});
				dialog.show();
			} else {
				frappe.msgprint(__('No pending web visitor pass submissions found.'));
			}
		}
	});
}

function generate_submissions_html(submissions, frm) {
	let html = '<div class="row">';
	submissions.forEach(sub => {
		html += `
			<div class="col-md-6 mb-3">
				<div class="card">
					<div class="card-body">
						<h5 class="card-title">${sub.visitor_full_name} (${sub.visitor_type})</h5>
						<p class="card-text">
							Phone: ${sub.mobile_number}<br>
							Email: ${sub.email_id}<br>
							Date: ${sub.visit_date}
						</p>
						<button class="btn btn-primary btn-sm" onclick="select_submission('${sub.name}', '${frm.doc.name}')">Select & Auto-Fetch</button>
					</div>
				</div>
			</div>
		`;
	});
	html += '</div>';
	return html;
}

window.select_submission = function(submission_name, frm_name) {
	frappe.call({
		method: 'frappe.client.get',
		args: { doctype: 'Visitor Pass', name: submission_name },
		callback: function(r) {
			if (r.message) {
				let data = r.message;
				// Set values in Visitor Pass
				frappe.set_route('Form', 'Visitor Pass', frm_name);
				setTimeout(() => {
					let formview = frappe.views.formview['Visitor Pass'];
					let frm = formview && formview.frm;
					if (!frm) {
						return;
					}
					frm.set_value('visitor_type', data.visitor_type);
					frm.set_value('visitor_full_name', data.visitor_full_name);
					frm.set_value('mobile_number', data.mobile_number);
					frm.set_value('email_id', data.email_id);
					frm.set_value('company__organisation', data.company__organisation);
					frm.set_value('visit_date', data.visit_date);
					frm.set_value('expected_checkin', data.expected_checkin);
					frm.set_value('expected_checkout', data.expected_checkout);
					frm.set_value('purpose_of_visit', resolve_purpose_of_visit(data));
					frm.set_value('person_to_visit', data.person_to_visit);
					frm.set_value('id_proof_type', data.id_proof_type);
					frm.set_value('id_proof_number', data.id_proof_number);
					frm.set_value('id_proof_scan', data.id_proof_scan);
					frm.set_value('visitor_photo', data.visitor_photo);
					frm.set_value('request_channel', 'Portal');
					frm.save();
				}, 500);
			}
		}
	});
};

function resolve_purpose_of_visit(data) {
	const explicit = (data.purpose_of_visit || "").trim();
	if (explicit) {
		return explicit;
	}

	if (data.visitor_type === "Supplier") {
		if ((data.supplier_visit_mode || "") === "Delivery") {
			return __("Supplier Delivery");
		}
		return __("Supplier Meeting");
	}
	if (data.visitor_type === "Customer") {
		return __("Customer Meeting");
	}
	if (data.visitor_type === "Contractor") {
		return __("Contract Work Visit");
	}
	if (data.visitor_type === "Candidate") {
		return __("Interview Visit");
	}
	if (data.visitor_type === "VIP") {
		return __("VIP Visit");
	}
	return "";
}

function apply_existing_pass_data(frm, data) {
	const fields = [
		"visitor_full_name",
		"mobile_number",
		"email_id",
		"company__organisation",
		"id_proof_type",
		"id_proof_number",
		"id_proof_scan",
		"visitor_photo",
		"purpose_of_visit",
		"person_to_visit",
		"host_department",
		"visit_date",
		"expected_checkin",
		"expected_checkout",
		"supplier_visit_mode",
		"supplier_link",
		"meeting_subject",
		"refreshments_required",
		"nda_required",
		"documents_shared",
		"crm_reference_type",
		"crm_lead_opportunity",
		"visit_category",
		"sales_executive",
		"products_discussed",
		"meeting_outcome",
		"followup_date",
		"meeting_minutes",
		"contractor_link",
		"work_order_ref",
		"tools_list",
		"multi_day_pass",
		"pass_valid_until",
		"job_applicant_link",
		"position_applied",
		"candidate_interview_type",
		"interview_panel",
	];

	const updates = {};
	fields.forEach((fieldname) => {
		if (Object.prototype.hasOwnProperty.call(data, fieldname)) {
			updates[fieldname] = data[fieldname];
		}
	});
	frm.set_value(updates);
}

function lookup_existing_visitor_match(frm, trigger_field) {
	if (!frm.doc.visitor_type || !["Supplier", "Customer", "Contractor", "Candidate"].includes(frm.doc.visitor_type)) {
		return;
	}
	if (!frm.doc.mobile_number && !frm.doc.id_proof_number) {
		return;
	}

	frappe.call({
		method: "visitormanagement.visitor_management.doctype.visitor_pass.visitor_pass.get_existing_visitor_matches",
		args: {
			visitor_type: frm.doc.visitor_type,
			id_proof_number: frm.doc.id_proof_number,
			mobile_number: frm.doc.mobile_number,
			exclude_name: frm.doc.name,
		},
		callback: ({ message }) => {
			if (!message || !message.best_match) {
				return;
			}

			const best = message.best_match;
			const signature = `${best.name}:${trigger_field}:${frm.doc.id_proof_number || ""}:${frm.doc.mobile_number || ""}`;
			if (frm.__last_existing_prompt_signature === signature) {
				return;
			}
			frm.__last_existing_prompt_signature = signature;

			const prompt = __(
				"Existing {0} record found: {1} ({2}). Do you want to load this data?",
				[best.visitor_type, best.name, best.visitor_full_name]
			);

			frappe.confirm(prompt, () => {
				if (frm.doc.entry_type !== "Existing") {
					frm.set_value("entry_type", "Existing");
				}
				frm.set_value("existing_visitor_pass", best.name);
			});
		},
	});
}

function add_hospitality_buttons(frm) {
	if (frm.is_new()) return;

	if (frm.doc.hospitality_request) {
		frm.add_custom_button(
			__("View Itinerary"),
			() => {
				const url = `/printview?doctype=${encodeURIComponent("Hospitality Request")}`
					+ `&name=${encodeURIComponent(frm.doc.hospitality_request)}`
					+ `&format=${encodeURIComponent("Visitor Itinerary")}`
					+ `&no_letterhead=0`;
				window.open(url, "_blank");
			},
			__("Hospitality")
		);

		frm.add_custom_button(
			__("Open Hospitality Request"),
			() => {
				frappe.set_route("Form", "Hospitality Request", frm.doc.hospitality_request);
			},
			__("Hospitality")
		);
	} else {
		const any_arrangement = (
			frm.doc.cab_required
			|| frm.doc.hotel_required
			|| frm.doc.factory_tour_required
			|| frm.doc.buggy_required
			|| frm.doc.greeting_required
			|| frm.doc.meal_required
			|| frm.doc.conference_room
		);
		if (any_arrangement) {
			frm.add_custom_button(
				__("Create Hospitality Request"),
				() => {
					frappe.new_doc("Hospitality Request", {
						visitor_pass: frm.doc.name,
					});
				},
				__("Hospitality")
			);
		}
	}
}
