// For license information, please see license.txt

// Fill a field from a linked master ONLY when the user has not already typed
// something there, and never blank it out.
//
// The three "link to an existing record" pickers (supplier_link, contractor_link,
// job_applicant_link) used to call frm.set_value(..., master.field || '')
// unconditionally. Two ways that went wrong, both reported live:
//   - the master has no phone/email, so `|| ''` ERASED the visitor's real details
//     that a receptionist had just typed, and the save then failed on mandatory
//     fields — annoying but at least loud;
//   - the master DOES have its own phone/email (a company switchboard, a
//     recruiter's inbox), so the visitor's personal contact details were silently
//     replaced and the save SUCCEEDED. Nobody notices, and the pass now carries
//     the wrong way to reach that person — which is the whole point of the field.
// A linked master is a convenience for blank fields, not an authority over what a
// human just entered. Same "did a person choose this?" principle the server side
// already applies to meal_type and special_diet in lifecycle.py.
// Returns only the fields a pass copies from a picked Employee / Job Applicant /
// Supplier, to anyone allowed to pick one in this form — so no role needs any
// permission on those HRMS/ERPNext DocTypes (see link_details.py).
const LINK_DETAILS_METHOD = "visitormanagement.visitor_management.link_details.get_link_details";

function fill_if_blank(frm, fieldname, value) {
	if (!value) {
		return;
	}
	const current = frm.doc[fieldname];
	if (current === undefined || current === null || String(current).trim() === "") {
		frm.set_value(fieldname, value);
	}
}

// What the form needs to know that is not on the pass: the home country, the
// default dialling code, whether badges are in use, and which ID Proof Types a
// foreign visitor may present. One request per form load, shared by everything
// below (the promise is dropped in `onload`, so a setting changed meanwhile is
// picked up the next time a pass is opened).
//
// Not the generic client API (frappe.db.get_single_value / get_value on VMS
// Settings and ID Proof Type): that answers only users who may read those
// records. An approver whose login has no Employee role can open and approve
// passes, and got a red "No permission for VMS Settings" on every one, with the
// badge fields left hidden. The method returns these few values to anyone who
// may read Visitor Passes (visitor_pass.get_pass_form_settings).
const PASS_FORM_SETTINGS_METHOD =
	"visitormanagement.visitor_management.doctype.visitor_pass.visitor_pass.get_pass_form_settings";
// Used if the request cannot be answered: the server's own fallbacks.
const PASS_FORM_SETTINGS_DEFAULTS = {
	home_country: "India",
	default_country_code: "91",
	enable_badge: 1,
	badge_required_for: "",
	foreign_national_id_types: null,
};
let _pass_form_settings_promise = null;

function get_pass_form_settings() {
	if (!_pass_form_settings_promise) {
		_pass_form_settings_promise = new Promise((resolve) => {
			const fallback = () => resolve(Object.assign({}, PASS_FORM_SETTINGS_DEFAULTS));
			// The form is only open for someone who can read passes, which is all
			// the method asks; asked anyway, so that no refusal can ever be shown.
			if (!frappe.model.can_read("Visitor Pass")) {
				fallback();
				return;
			}
			frappe.call({
				method: PASS_FORM_SETTINGS_METHOD,
				silent: true,
				callback: (r) =>
					resolve(
						Object.assign({}, PASS_FORM_SETTINGS_DEFAULTS, (r && r.message) || {})
					),
				error: fallback,
			});
		});
	}
	return _pass_form_settings_promise;
}

function get_vms_home_country() {
	return get_pass_form_settings().then((settings) => settings.home_country || "India");
}

// Approval-lane vocabulary (which role a Visitor Type's pass is currently
// awaiting) is generated server-side from the Visitor Type masters
// (workflow_builder.approver_roles() / lane_for_role()) -- it is NOT a fixed
// list. This file used to hardcode a 5-entry map keyed on Visitor Type name
// (Contractor/Supplier/Customer/Candidate/VIP); any type routed to another
// approver role -- e.g. "Auditor" -> Facility Manager -- showed no approver
// name here, and the same hardcoded list in show_web_submissions_dialog's
// filter meant passes sitting in that lane never appeared in the Pending Web
// Submissions dialog at all. Fetched once and cached for the desk session,
// same pattern as get_vms_home_country above -- a Visitor Type's approver
// role changes rarely, and a normal page reload after such a change is
// expected (same as any other masters-driven Desk vocabulary).
let _visitor_type_approver_cache = null;
let _visitor_type_approver_promise = null;

function get_visitor_type_approvers() {
	if (_visitor_type_approver_cache) {
		return Promise.resolve(_visitor_type_approver_cache);
	}
	if (!_visitor_type_approver_promise) {
		_visitor_type_approver_promise = frappe
			.call({
				method: "visitormanagement.visitor_management.workflow_builder.get_visitor_type_approvers",
			})
			.then((r) => {
				_visitor_type_approver_cache = r.message || {};
				return _visitor_type_approver_cache;
			});
	}
	return _visitor_type_approver_promise;
}

// Frappe's core Phone control defaults to the site's System Settings country
// (e.g. India) the first time it renders, regardless of the visitor's actual
// nationality. Once custom_nationality is set, nudge the phone widget's
// displayed flag/ISD prefix to match it.
function sync_mobile_country_with_nationality(frm) {
	const country_name = frm.doc.custom_nationality;
	if (!country_name) {
		return;
	}
	const control = frm.get_field("mobile_number");
	// `$isd` is the last piece the Phone control builds. Without checking it we
	// can fire on_change before the control's DOM exists, and core's
	// set_formatted_input then throws on `this.$isd.text()` — a console error on
	// every new Visitor Pass form.
	if (
		control &&
		control.country_codes &&
		control.country_codes[country_name] &&
		control.country_code_picker &&
		control.$isd &&
		control.$isd.length
	) {
		control.country_code_picker.on_change(country_name, false);
	}
}

// Mirrors VisitorPass.VISIT_ONLY_FIELDS. Frappe copies no_copy fields on Amend
// (frappe.model.copy_doc skips them only when not amending), so an amended copy
// of a cancelled pass opened showing the old visit's approval, check-in times,
// QR code and hospitality request. The server clears them on save as well.
const VISIT_ONLY_FIELDS = [
	"approved_by",
	"approval_date",
	"badge_number",
	"qr_code_image",
	"gate_verified_photo",
	"gate_verified_on",
	"gate_verified_by",
	"actual_checkin",
	"actual_checkout",
	"no_show",
	"current_location",
	"item_verification_status",
	"items_verified",
	"all_items_verified",
	"hospitality_request",
	"hospitality_overall_status",
	"food_status",
	"food_dept_staff_assigned",
];

// Frappe 15's Phone control builds itself asynchronously: make_input() awaits the
// country list (localforage, or a server call when the browser has no cached
// copy) before it creates the flag/ISD element (`$isd`) and the country picker.
// The form does not wait — refresh_input() calls set_input() straight after
// make_input() — and set_formatted_input() fetches the country list again on its
// own. When its fetch finishes first (two server calls racing on a cold cache),
// it reaches `this.$isd.text()` before the control has built `$isd` and throws
// "Cannot read properties of undefined (reading 'text')"
// (frappe/public/js/frappe/form/controls/phone.js, make_input /
// set_formatted_input; base_input.js refresh_input).
//
// Guarded on this form's own Phone control instances only — core's
// ControlPhone class, and every other form on the site, are left alone. Runs
// from `setup`, before the first refresh builds the control.
const VMS_PHONE_FIELDS = ["mobile_number"];

// This form's Links to HRMS/ERPNext DocTypes, searched through the app's own
// query (public/js/core_link_pickers.js).
const VISITOR_PASS_CORE_LINKS = {
	person_to_visit: { status: "Active" },
	sales_executive: { status: "Active" },
	food_dept_staff_assigned: { status: "Active" },
	gate_verified_by: {},
	supplier_link: {},
	contractor_link: {},
	job_applicant_link: {},
	work_order_ref: {},
};

function wait_for_phone_build(frm) {
	VMS_PHONE_FIELDS.forEach((fieldname) => {
		const control = frm.fields_dict[fieldname];
		if (!control || control.df.fieldtype !== "Phone" || control.__vms_waits_for_build) {
			return;
		}
		control.__vms_waits_for_build = true;
		const make_input = control.make_input;
		const set_formatted_input = control.set_formatted_input;
		const set_default_country = control.set_default_country;
		control.make_input = function (...args) {
			this.__vms_built = make_input.apply(this, args);
			return this.__vms_built;
		};
		// Already async in core, so no caller relies on it having finished.
		control.set_formatted_input = async function (...args) {
			if (this.__vms_built) {
				await this.__vms_built;
			}
			return set_formatted_input.apply(this, args);
		};
		// refresh() calls this too; make_input() calls it again once built.
		control.set_default_country = function (...args) {
			if (!this.country_code_picker) {
				return;
			}
			return set_default_country.apply(this, args);
		};
	});
}

frappe.ui.form.on("Visitor Pass", {
	setup(frm) {
		wait_for_phone_build(frm);
	},

	onload(frm) {
		// A pass is being opened: read the settings afresh, once, for this load.
		_pass_form_settings_promise = null;
		vms_setup_core_link_pickers(frm, VISITOR_PASS_CORE_LINKS);
		if (frm.is_new() && frm.doc.amended_from) {
			VISIT_ONLY_FIELDS.forEach((fieldname) => {
				frm.doc[fieldname] = null;
			});
			frm.doc.status = "Draft";
		}
	},

	refresh(frm) {
		vms_setup_core_link_pickers(frm, VISITOR_PASS_CORE_LINKS);
		ensure_customer_crm_defaults(frm);
		setup_supplier_pass_query(frm);
		// A refresh follows every load and every successful save, and by then the
		// server has taken whatever was typed: the "changing" state is over.
		if (!frm.doc.id_proof_number_entry) {
			frm.__vms_changing_id = null;
		}
		apply_visitor_pass_ui(frm);
		add_action_buttons(frm);
		add_hospitality_buttons(frm);
		add_gate_buttons(frm);
	},

	// The full ID number is never sent to this form. It shows the masked number
	// (id_proof_number_masked); a number is typed into id_proof_number_entry and
	// the server stores it (VisitorPass._take_id_numbers).
	change_id_number(frm) {
		frm.__vms_changing_id = frm.doc.name;
		apply_id_number_ui(frm);
		const field = frm.get_field("id_proof_number_entry");
		if (field && field.$input) {
			field.$input.focus();
		}
	},

	show_full_id_number(frm) {
		show_full_id_number(frm);
	},

	id_proof_type(frm) {
		// A different kind of document has a different number. The server checks
		// the number on record against the new type; offer the field to retype it.
		if (frm.doc.id_proof_number_masked && frm.doc.docstatus === 0) {
			frm.__vms_changing_id = frm.doc.name;
		}
		apply_id_number_ui(frm);
	},

	id_proof_number_entry(frm) {
		lookup_existing_visitor_match(frm, "id_proof_number");
	},

	visitor_type(frm) {
		ensure_customer_crm_defaults(frm);
		setup_supplier_pass_query(frm);
		apply_visitor_pass_ui(frm);
	},

	custom_nationality(frm) {
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
				method: LINK_DETAILS_METHOD,
				args: {
					doctype: "Supplier",
					name: frm.doc.supplier_link,
					reference_doctype: frm.doctype,
					fieldname: "supplier_link",
				},
				callback: function (r) {
					if (r.message) {
						fill_if_blank(frm, "visitor_full_name", r.message.supplier_name);
						fill_if_blank(frm, "company__organisation", r.message.supplier_name);
					}
				},
			});
		}
	},

	contractor_link(frm) {
		if (frm.doc.contractor_link) {
			frappe.call({
				method: LINK_DETAILS_METHOD,
				args: {
					doctype: "Supplier",
					name: frm.doc.contractor_link,
					reference_doctype: frm.doctype,
					fieldname: "contractor_link",
				},
				callback: function (r) {
					if (r.message) {
						fill_if_blank(frm, "visitor_full_name", r.message.supplier_name);
						fill_if_blank(frm, "company__organisation", r.message.supplier_name);
					}
				},
			});
		}
	},

	job_applicant_link(frm) {
		if (frm.doc.job_applicant_link) {
			frappe.call({
				method: LINK_DETAILS_METHOD,
				args: {
					doctype: "Job Applicant",
					name: frm.doc.job_applicant_link,
					reference_doctype: frm.doctype,
					fieldname: "job_applicant_link",
				},
				callback: function (r) {
					if (r.message) {
						fill_if_blank(frm, "visitor_full_name", r.message.applicant_name);
						frm.set_value("position_applied", r.message.job_title || "");
					}
				},
			});
		} else {
			frm.set_value("position_applied", "");
		}
	},

	person_to_visit(frm) {
		// Was `fetch_from` on the three host fields, which needs READ on the whole
		// Employee record; this returns only the name and department (see
		// link_details.py), and the server fills all three on save.
		if (!frm.doc.person_to_visit) {
			frm.set_value({ host_name: "", host_department: "", host_email: "" });
			return;
		}
		frappe.call({
			method: LINK_DETAILS_METHOD,
			args: {
				doctype: "Employee",
				name: frm.doc.person_to_visit,
				reference_doctype: frm.doctype,
				fieldname: "person_to_visit",
			},
			callback: function (r) {
				const d = r.message || {};
				frm.set_value({
					host_name: d.employee_name || "",
					host_department: d.department || "",
					// The host's login email is copied on save (_fill_from_linked_records).
					host_email: "",
				});
			},
		});
	},

	mobile_number(frm) {
		preview_normalised_mobile(frm);
		lookup_existing_visitor_match(frm, "mobile_number");
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
			// A new person: the number shown from the earlier pass no longer
			// applies, and theirs has to be typed.
			frm.set_value("id_proof_number_masked", "");
			frm.set_value("id_proof_number_entry", "");
			// Clear type-specific links for whichever layout this type uses
			const layout = frm.doc.visitor_type_layout || "";
			if (layout === "Supplier") {
				frm.set_value("supplier_link", "");
			} else if (layout === "Customer") {
				frm.set_value("crm_reference_type", "");
				frm.set_value("crm_lead_opportunity", "");
			} else if (layout === "Contractor") {
				frm.set_value("contractor_link", "");
				frm.set_value("work_order_ref", "");
			} else if (layout === "Candidate") {
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
			method: "visitormanagement.visitor_management.doctype.visitor_pass.visitor_pass.get_existing_visitor_pass_details",
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
	// On save the server normalises the phone to "+<isd>-XXXXXXXXXX". Show the
	// reception staff what they actually typed *will become*, so they catch
	// typos before submitting (a wrong number = approvals never land).
	const raw = (frm.doc.mobile_number || "").trim();
	if (!raw) {
		frm.set_df_property("mobile_number", "description", "");
		frm.refresh_field("mobile_number");
		return;
	}
	const digits = raw.replace(/\D/g, "");
	// The ISD prefix the server will apply comes from VMS Settings, so preview
	// it from there rather than assuming +91.
	get_pass_form_settings().then((settings) => {
		const code = String(settings.default_country_code || "91").replace(/\D/g, "") || "91";
		let normalised = raw;
		if (digits.length >= 10) {
			normalised = `+${code}-${digits.slice(-10)}`;
		}
		const description =
			normalised !== raw
				? __("Will be saved as: <b>{0}</b>", [normalised])
				: __("✓ Format looks good");
		frm.set_df_property("mobile_number", "description", description);
		frm.refresh_field("mobile_number");
	});
}

function apply_visitor_pass_ui(frm) {
	apply_visitor_pass_field_rules(frm);
	apply_id_number_ui(frm);
	apply_id_document_ui(frm);
	set_visitor_pass_intro(frm);
	explain_closed_pickers(frm);
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
	const docname = frm.doc.name;
	get_pass_form_settings().then((s) => {
		if (frm.doc.name !== docname) {
			return; // another pass was opened while this was in flight
		}
		// "0" is truthy in JS: coerce with cint to get a real boolean.
		let show = !!cint(s.enable_badge);
		if (show) {
			const list = (s.badge_required_for || "")
				.split(/[\n,]/)
				.map((x) => x.trim())
				.filter(Boolean);
			if (list.length && frm.doc.visitor_type && !list.includes(frm.doc.visitor_type)) {
				show = false;
			}
		}
		setHidden(!show);
	});
}

function ensure_customer_crm_defaults(frm) {
	if (
		frm.doc.visitor_type_layout === "Customer" &&
		frm.doc.entry_type === "New" &&
		!frm.doc.crm_reference_type
	) {
		frm.set_value("crm_reference_type", "Lead");
		return;
	}

	if (frm.doc.visitor_type_layout !== "Customer" || frm.doc.entry_type !== "New") {
		if (frm.doc.crm_reference_type || frm.doc.crm_lead_opportunity) {
			frm.set_value({
				crm_reference_type: "",
				crm_lead_opportunity: "",
			});
		}
	}
}

function fetch_customer_crm_details(frm) {
	if (
		frm.doc.visitor_type_layout !== "Customer" ||
		!frm.doc.crm_reference_type ||
		!frm.doc.crm_lead_opportunity
	) {
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
	// Field rules key off the Visitor Type's *layout* (visitor_type_layout, fetched
	// from Visitor Type.detail_layout) rather than its name, so a site can add a
	// type that reuses the Supplier/Customer/Contractor/Candidate/VIP layout and
	// gets the same behaviour with no code change.
	const layout = frm.doc.visitor_type_layout || "";
	const is_supplier_existing = layout === "Supplier" && frm.doc.entry_type === "Existing";
	const is_existing =
		["Supplier", "Customer", "Contractor", "Candidate"].includes(layout) &&
		frm.doc.entry_type === "Existing";
	const is_follow_up = layout === "Customer" && frm.doc.meeting_outcome === "Follow-Up Needed";
	const needs_interpreter = layout === "VIP" && !!frm.doc.interpreter_required;
	const is_multi_day_contractor = layout === "Contractor" && !!frm.doc.multi_day_pass;
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
	frm.toggle_display("supplier_link", layout === "Supplier" && frm.doc.entry_type === "New");
	frm.toggle_display(
		"crm_reference_type",
		layout === "Customer" && frm.doc.entry_type === "New"
	);
	frm.toggle_display(
		"crm_lead_opportunity",
		layout === "Customer" && frm.doc.entry_type === "New"
	);
	frm.toggle_display("contractor_link", layout === "Contractor" && frm.doc.entry_type === "New");
	frm.toggle_display("work_order_ref", layout === "Contractor" && frm.doc.entry_type === "New");
	frm.toggle_display(
		"job_applicant_link",
		layout === "Candidate" && frm.doc.entry_type === "New"
	);
	const is_supplier_meeting = layout === "Supplier" && frm.doc.supplier_visit_mode === "Meeting";
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

	sync_mobile_country_with_nationality(frm);

	get_vms_home_country().then((home_country) => {
		const is_foreign_national =
			frm.doc.custom_nationality && frm.doc.custom_nationality !== home_country;
		frm.toggle_display("custom_visa_copy", is_foreign_national);
		frm.toggle_reqd("custom_visa_copy", is_foreign_national);

		// id_proof_type is a Link to the ID Proof Type master, so the foreign
		// national restriction is a link filter on the master's own flag rather
		// than rewriting a hardcoded Select option list.
		frm.set_query("id_proof_type", () => {
			const filters = { is_active: 1 };
			if (is_foreign_national) {
				filters.valid_for_foreign_nationals = 1;
			}
			return { filters };
		});

		// Clear a now-invalid selection when the visitor turns out to be foreign.
		// The list of types a foreign visitor may present comes with the form's
		// settings (the ID Proof Type master itself is closed to several roles
		// that can open a pass). Unknown (null) means "do not touch".
		if (is_foreign_national && frm.doc.id_proof_type) {
			get_pass_form_settings().then((settings) => {
				const allowed = settings.foreign_national_id_types;
				if (
					Array.isArray(allowed) &&
					frm.doc.id_proof_type &&
					!allowed.includes(frm.doc.id_proof_type)
				) {
					frm.set_value("id_proof_type", "");
				}
			});
		}
	});
}

// Three pickers on this form list masters that only some roles may list
// (Visitor Type, ID Proof Type, Conference Room). An approver whose login has
// no Employee role can open and edit a pass in their lane, but typing in these
// fields offers nothing — and nothing said why. A line under the field does;
// permissions are unchanged. Decided from the session's own permission lists
// (no request), and only where the field could otherwise be edited.
const PASS_MASTER_PICKERS = {
	visitor_type: "Visitor Type",
	id_proof_type: "ID Proof Type",
	conference_room: "Conference Room",
};

function explain_closed_pickers(frm) {
	Object.keys(PASS_MASTER_PICKERS).forEach((fieldname) => {
		const field = frm.fields_dict[fieldname];
		const doctype = PASS_MASTER_PICKERS[fieldname];
		if (!field || frappe.model.can_read(doctype) || frappe.model.can_select(doctype)) {
			return;
		}
		if (field.df.__vms_base_description === undefined) {
			field.df.__vms_base_description = field.df.description || "";
		}
		const editable =
			frm.doc.docstatus === 0 && frm.perm[0] && frm.perm[0].write && !field.df.read_only;
		const hint = editable
			? __(
					"Your role cannot list {0} records, so this field cannot be changed from your login. Ask the host or an administrator if it needs correcting.",
					[__(doctype)]
			  )
			: "";
		const description = [field.df.__vms_base_description, hint].filter(Boolean).join(" ");
		if ((field.df.description || "") !== description) {
			frm.set_df_property(fieldname, "description", description);
		}
	});
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
		// The desk's own, signed-in method: the public form's endpoint is rate
		// limited per network address, which is not a limit for staff.
		method: "visitormanagement.visitor_management.lifecycle.get_meal_plan_for_pass",
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

// frm.set_intro() ADDS a banner to the form's message area on Frappe 15
// (frappe/form/layout.js show_message appends a block), and
// frm.dashboard.clear_headline() empties that whole area. This form sets its
// stage banner on every refresh and again on field changes, and used to call
// clear_headline() straight after set_intro() — so the guidance never showed.
// The banner is now marked, and only that one is replaced: nothing Frappe put
// there ("modified after you loaded it") is removed, and banners do not pile up.
const PASS_INTRO_CLASS = "vm-pass-intro";

function set_pass_intro(frm, text, color) {
	const $area = frm.layout && frm.layout.message;
	const has_area = !!($area && $area.length);
	if (has_area) {
		$area.find(`.${PASS_INTRO_CLASS}`).remove();
		if (!$area.children().length) {
			$area.addClass("hidden");
		}
	}
	if (!text) {
		return;
	}
	frm.set_intro(text, color);
	if (has_area) {
		$area.children(".form-message").last().addClass(PASS_INTRO_CLASS);
	}
}

function set_visitor_pass_intro(frm) {
	const stage = get_pass_stage(frm);

	// Clear approver card at the start; it's re-rendered only for Pending stages.
	clear_approver_context_card(frm);

	if (frm.is_new()) {
		set_pass_intro(
			frm,
			__(
				"Complete the Visitor Profile and Visit Plan first, then fill the section that matches the selected visitor type before submitting."
			),
			"blue"
		);
		return;
	}

	if (stage.startsWith("Pending")) {
		// An approver looking at a pass they created, sent for approval or host:
		// Approve is not in their Actions menu (the server refuses it as well), and
		// this says why. The sentence comes from the server, already translated
		// (VisitorPass._approval_blocked_notice); it is only there for such a user.
		const blocked = frm.doc.__onload && frm.doc.__onload.approval_blocked;
		if (blocked) {
			set_pass_intro(frm, blocked, "orange");
			render_approver_context_card(frm);
			return;
		}

		// Generic text (correct for every lane, including one from a Visitor
		// Type created seconds ago) until the server-driven lane map is known,
		// then the approver by name. Once the map is cached the named text is
		// set straight away, so the banner does not flicker on field changes.
		const named_intro = (approvers) => {
			const approval_lane = (approvers || {})[frm.doc.visitor_type];
			if (!approval_lane) {
				return false;
			}
			set_pass_intro(
				frm,
				__(
					"Awaiting approval from {0}. Review the request snapshot and visit-specific details carefully.",
					[frappe.utils.escape_html(approval_lane)]
				),
				"orange"
			);
			return true;
		};

		if (!named_intro(_visitor_type_approver_cache)) {
			set_pass_intro(
				frm,
				__("Awaiting approval. Review the visitor details before taking action."),
				"orange"
			);
		}
		render_approver_context_card(frm);

		if (!_visitor_type_approver_cache) {
			const docname = frm.doc.name;
			get_visitor_type_approvers().then((approvers) => {
				// The form may have moved on (record switched, or no longer
				// pending) while this call was in flight -- don't stomp its intro.
				if (frm.doc.name !== docname || !get_pass_stage(frm).startsWith("Pending")) {
					return;
				}
				named_intro(approvers);
			});
		}
		return;
	}

	if (stage === "Approved") {
		set_pass_intro(
			frm,
			frm.doc.visitor_type_layout === "VIP"
				? __(
						"Approved. Security should use the VIP priority lane and issue the badge during gate check-in."
				  )
				: __(
						"Approved. Security can now verify declared items, issue the badge, and record the visitor check-in."
				  ),
			"green"
		);
		return;
	}

	if (stage === "Items Verified") {
		set_pass_intro(
			frm,
			__(
				"Items are verified and the pass is gate-ready. Proceed with Security Log check-in."
			),
			"blue"
		);
		return;
	}

	if (stage === "Checked-In") {
		set_pass_intro(
			frm,
			__(
				"Visitor is currently inside the premises. Use Security Log to record checkout when they exit."
			),
			"green"
		);
		return;
	}

	if (stage === "Checked-Out") {
		set_pass_intro(frm, __("Visit completed and gate exit recorded."), "blue");
		return;
	}

	if (stage === "Rejected") {
		set_pass_intro(
			frm,
			__(
				"Request rejected. Update the details and reapply if the visit still needs to happen."
			),
			"red"
		);
		return;
	}

	set_pass_intro(frm, null);
}

const APPROVER_CARD_CLASS = "vm-approver-card-host";

function render_approver_context_card(frm) {
	// One-glance card for approvers: visit details + attachment checklist.
	// Intentionally NO SLA timer and NO risk badge — kept minimal so approvers
	// see only verifiable facts about the request.
	//
	// Added as a dashboard section. The form dashboard on Frappe 15 is built on
	// a parent element and has no `wrapper` (frappe/form/dashboard.js), which
	// is what this used to look for — so the card never rendered. Sections added
	// through add_section() carry the "custom" class and Frappe removes them
	// itself on the next refresh (Dashboard.reset), so a card never lingers on
	// another record.
	if (!frm.dashboard || !frm.dashboard.add_section) return;

	const photo_ok = !!frm.doc.visitor_photo;
	const id_ok = !!frm.doc.id_proof_scan;
	const items_declared =
		(frm.doc.visitor_items || []).length > 0 || !!(frm.doc.items_carried || "").trim();

	const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));

	const checklist_item = (label, ok) => `
		<span style="display:inline-flex; align-items:center; gap:4px; padding:2px 8px; border-radius:999px; font-size:11px; font-weight:600; background:${
			ok ? "#d9f3e4" : "#fde2e2"
		}; color:${ok ? "#0d6b3e" : "#9b1c1c"};">
			${ok ? "✅" : "⚠️"} ${label}
		</span>
	`;

	const meta_row = (label, value) => `
		<div style="font-size:12px; color:#334e68; line-height:1.6;">
			<strong>${label}:</strong> ${value || "-"}
		</div>
	`;

	const visit_window = [
		esc(frm.doc.visit_date || ""),
		esc(frm.doc.expected_checkin || ""),
		frm.doc.expected_checkout ? "→ " + esc(frm.doc.expected_checkout) : "",
	]
		.filter(Boolean)
		.join(" ");

	const html = `
		<div class="vm-approver-card" style="width:100%; border:1px solid #dbe3ea; border-radius:12px; padding:14px 16px; background:linear-gradient(180deg,#f8fafc 0%, #eef4f8 100%);">
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
				${meta_row(__("Visitor"), esc(frm.doc.visitor_full_name))}
				${meta_row(__("Type"), esc(frm.doc.visitor_type))}
				${meta_row(__("Company"), esc(frm.doc.company__organisation))}
				${meta_row(__("Host"), esc(frm.doc.host_name || frm.doc.person_to_visit))}
				${meta_row(__("ID Proof"), esc(frm.doc.id_proof_number_masked))}
				${meta_row(__("Visit Window"), visit_window)}
				${meta_row(__("Purpose"), esc(frm.doc.purpose_of_visit))}
			</div>
		</div>
	`;

	clear_approver_context_card(frm);
	frm.dashboard.add_section(html, null, `custom ${APPROVER_CARD_CLASS}`);
	frm.dashboard.show();
}

function clear_approver_context_card(frm) {
	const $parent = frm.dashboard && frm.dashboard.parent;
	if ($parent && $parent.find) {
		$parent.find(`.${APPROVER_CARD_CLASS}`).remove();
	}
}

// ── ID number: masked on screen, typed into the entry field ────────────────
function can_view_full_id(frm) {
	// The server decides (id_masking.reveal_id checks again); this only hides a
	// button nobody else could use.
	const flag = frm.doc.__onload && frm.doc.__onload.can_view_full_id;
	return flag === undefined ? frappe.user.has_role("System Manager") : !!flag;
}

function apply_id_number_ui(frm) {
	if (!frm.fields_dict.id_proof_number_entry) {
		return;
	}
	const has_number = !!frm.doc.id_proof_number_masked;
	const editable = frm.doc.docstatus === 0;
	const changing =
		editable && (frm.__vms_changing_id === frm.doc.name || !!frm.doc.id_proof_number_entry);

	// Only when a value really changes: set_df_property redraws the field, and
	// this runs on many field events — it must not redraw a box being typed in.
	const set_prop = (fieldname, property, value) => {
		const field = frm.fields_dict[fieldname];
		if (field && (field.df[property] || 0) !== (value || 0)) {
			frm.set_df_property(fieldname, property, value);
		}
	};
	const show = (fieldname, visible) => set_prop(fieldname, "hidden", visible ? 0 : 1);

	// With a number on record the entry field appears only to change it.
	show("id_proof_number_entry", editable && (!has_number || changing));
	set_prop(
		"id_proof_number_entry",
		"label",
		has_number ? __("New ID Proof Number") : __("ID Proof Number")
	);
	set_prop(
		"id_proof_number_entry",
		"description",
		has_number
			? __("Leave blank to keep the number on record.")
			: __(
					"Type the number as printed on the document. After saving, only the last characters are shown."
			  )
	);
	// Both buttons also carry a depends_on in the DocType; these only narrow it.
	show("change_id_number", has_number && editable && !changing);
	show("show_full_id_number", has_number && !frm.is_new() && can_view_full_id(frm));
}

function show_full_id_number(frm, row) {
	if (frm.is_new() || (row && row.__islocal)) {
		frappe.msgprint(__("Save the pass first."));
		return;
	}
	const args = { doctype: frm.doctype, name: frm.doc.name, fieldname: "id_proof_number" };
	if (row) {
		args.row_name = row.name;
	}
	frappe.call({
		method: "visitormanagement.visitor_management.id_masking.reveal_id",
		type: "POST",
		args: args,
		freeze: true,
		callback: (r) => {
			if (r.exc) {
				return;
			}
			// Shown once in a dialog and dropped when it closes. It is never
			// written into the form, so it cannot be saved, copied to another
			// record or left on screen behind the dialog.
			const value = r.message || "";
			const dialog = new frappe.ui.Dialog({
				title: __("Full ID Number"),
				fields: [{ fieldtype: "HTML", fieldname: "full_id" }],
				primary_action_label: __("Close"),
				primary_action: () => dialog.hide(),
			});
			const who = row ? row.visitor_name : frm.doc.visitor_full_name;
			dialog.fields_dict.full_id.$wrapper.html(
				value
					? `<div class="text-muted small">${frappe.utils.escape_html(who || "")}</div>
						<div style="font-size:20px; font-family:monospace; letter-spacing:1px; margin:8px 0;">${frappe.utils.escape_html(
							value
						)}</div>
						<div class="text-muted small">${__("This view has been recorded against your name.")}</div>`
					: `<div class="text-muted">${__("No ID number is stored.")}</div>`
			);
			dialog.onhide = () => dialog.fields_dict.full_id.$wrapper.empty();
			dialog.show();
		},
	});
}

// ── ID scan and visa copy: restricted files ────────────────────────────────
// Only Security, the pass's approvers and System Manager can open these two
// files (visitormanagement.permissions.can_open_id_documents; the server
// refuses the download for everyone else). Whoever raises the pass can still
// attach or replace them. For someone who cannot open them, say so instead of
// leaving a link and a preview that answer "not permitted".
const ID_DOCUMENT_FIELDS = ["id_proof_scan", "custom_visa_copy"];
const ID_DOCUMENT_DESCRIPTIONS = {};

function can_open_id_documents(frm) {
	if (frm.is_new()) {
		return true;
	}
	const flag = frm.doc.__onload && frm.doc.__onload.can_open_id_documents;
	return flag === undefined ? true : !!flag;
}

function apply_id_document_ui(frm) {
	const restricted = !can_open_id_documents(frm);
	ID_DOCUMENT_FIELDS.forEach((fieldname) => {
		const field = frm.fields_dict[fieldname];
		if (!field) {
			return;
		}
		if (!(fieldname in ID_DOCUMENT_DESCRIPTIONS)) {
			ID_DOCUMENT_DESCRIPTIONS[fieldname] = field.df.description || "";
		}
		const on_file = !!frm.doc[fieldname];
		const note =
			restricted && on_file
				? __(
						"On file. Only Security, the approver and a System Manager can open it. You can still replace it."
				  )
				: ID_DOCUMENT_DESCRIPTIONS[fieldname];
		if ((field.df.description || "") !== note) {
			frm.set_df_property(fieldname, "description", note);
		}
		if (!field.$wrapper) {
			return;
		}
		// The link stays in the page (clicking it gets the server's refusal);
		// the hover preview would only show a broken image.
		const $link = field.$wrapper.find(".attached-file-link");
		$link.toggleClass("text-muted", restricted && on_file);
		if ($link.length && $link.data("bs.popover")) {
			$link.popover(restricted && on_file ? "disable" : "enable");
		}
	});
}

frappe.ui.form.on("Visitor Group Member", {
	show_full_id_number(frm, cdt, cdn) {
		show_full_id_number(frm, locals[cdt][cdn]);
	},

	form_render(frm, cdt, cdn) {
		// The button only makes sense on a saved row that has a number, for
		// someone who may use it.
		const row = locals[cdt][cdn];
		const grid = frm.fields_dict.group_members && frm.fields_dict.group_members.grid;
		const grid_row = grid && grid.grid_rows_by_docname && grid.grid_rows_by_docname[cdn];
		const button =
			grid_row && grid_row.grid_form && grid_row.grid_form.fields_dict.show_full_id_number;
		if (button && button.$wrapper) {
			button.$wrapper.toggle(
				!!(row && row.id_proof_number_masked && !row.__islocal) && can_view_full_id(frm)
			);
		}
	},
});

function add_action_buttons(frm) {
	// "Actions" group removed — "Open Hospitality" is already available
	// under the "Hospitality" group (see add_hospitality_buttons).
	return;
}

function setup_supplier_pass_query(frm) {
	if (!["Supplier", "Customer", "Contractor", "Candidate"].includes(frm.doc.visitor_type))
		return;

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

// get_approval_lane(visitor_type) used to live here as a hardcoded 5-entry
// map. Removed: its one caller (set_visitor_pass_intro) now resolves the
// approver name through get_visitor_type_approvers() above, which reads the
// live Visitor Type -> approver-role vocabulary instead of a fixed list.

function get_pass_stage_color(stage) {
	// Pending-lane state names are generated as `Pending <approver role>` for
	// every role a Visitor Type configures (workflow_builder.lane_for_role) --
	// there is no fixed list to enumerate, and the generic status fallback
	// ("Pending Approval") shares the same prefix. The prefix IS the shared
	// vocabulary: this used to be a hardcoded 6-entry list that silently
	// stopped matching the moment a Visitor Type routed to a role outside it.
	if (["Approved", "Checked-In"].includes(stage)) return "green";
	if (String(stage || "").startsWith("Pending")) return "orange";
	if (["Rejected", "Cancelled"].includes(stage)) return "red";
	if (["Items Verified", "Checked-Out"].includes(stage)) return "blue";
	return "gray";
}

// show_web_submissions_dialog / select_submission used to live here: an unused
// dialog whose global `select_submission()` copied another pass into the open
// form through the browser, ID number and scan included. Nothing called it.
// A returning visitor's details are loaded through `existing_visitor_pass`
// below, and the ID number is copied on the server.

function apply_existing_pass_data(frm, data) {
	const fields = [
		"visitor_full_name",
		"mobile_number",
		"email_id",
		"company__organisation",
		"id_proof_type",
		// The earlier pass's number arrives masked, for display. The full number
		// is copied on the server when this pass is saved — and so is the ID
		// scan, for anyone who is not allowed to open it (the server leaves it
		// out of `data` then).
		"id_proof_number_masked",
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
	Promise.resolve(frm.set_value(updates)).then(() => {
		// Setting the ID type above is not the user changing it.
		if (!frm.doc.id_proof_number_entry) {
			frm.__vms_changing_id = null;
		}
		apply_id_number_ui(frm);
		if (data.has_id_proof_scan && !data.id_proof_scan && !frm.doc.id_proof_scan) {
			frappe.show_alert(
				{
					message: __("The ID scan on the earlier pass will be attached when you save."),
					indicator: "blue",
				},
				7
			);
		}
	});
}

function lookup_existing_visitor_match(frm, trigger_field) {
	if (
		!frm.doc.visitor_type_layout ||
		!["Supplier", "Customer", "Contractor", "Candidate"].includes(frm.doc.visitor_type_layout)
	) {
		return;
	}
	// The number just typed, if any. It is only an input to the search: the
	// answer carries masked numbers.
	const typed_id = (frm.doc.id_proof_number_entry || "").trim();
	if (!frm.doc.mobile_number && !typed_id) {
		return;
	}

	frappe.call({
		method: "visitormanagement.visitor_management.doctype.visitor_pass.visitor_pass.get_existing_visitor_matches",
		args: {
			visitor_type: frm.doc.visitor_type,
			id_proof_number: typed_id,
			id_proof_type: frm.doc.id_proof_type,
			mobile_number: frm.doc.mobile_number,
			exclude_name: frm.doc.name,
		},
		callback: ({ message }) => {
			if (!message || !message.best_match) {
				return;
			}

			const best = message.best_match;
			const signature = `${best.name}:${trigger_field}:${typed_id}:${
				frm.doc.mobile_number || ""
			}`;
			if (frm.__last_existing_prompt_signature === signature) {
				return;
			}
			frm.__last_existing_prompt_signature = signature;

			const prompt = __(
				"Existing {0} record found: {1} ({2}). Do you want to load this data?",
				[
					frappe.utils.escape_html(best.visitor_type || ""),
					frappe.utils.escape_html(best.name || ""),
					frappe.utils.escape_html(best.visitor_full_name || ""),
				]
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
				const url =
					`/printview?doctype=${encodeURIComponent("Hospitality Request")}` +
					`&name=${encodeURIComponent(frm.doc.hospitality_request)}` +
					`&format=${encodeURIComponent("Visitor Itinerary")}` +
					`&no_letterhead=0`;
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
		const any_arrangement =
			frm.doc.cab_required ||
			frm.doc.hotel_required ||
			frm.doc.factory_tour_required ||
			frm.doc.buggy_required ||
			frm.doc.greeting_required ||
			frm.doc.meal_required ||
			frm.doc.conference_room;
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

// Gate check-in/out: visitor_gate.visitor_checkin/visitor_checkout validate the
// movement server-side and hand back the prefilled Security Log route where the
// officer completes qr_code_scanned/photo_at_gate/id_proof_match/pass_photo_match
// (SecurityLog.before_save requires all four, so these endpoints deliberately do
// not insert the log themselves -- see visitor_gate.py's module docstring).
function call_gate_endpoint(frm, method) {
	frappe.call({
		method: `visitormanagement.visitor_management.api.visitor_gate.${method}`,
		args: { docname: frm.doc.name },
		freeze: true,
		callback: (r) => {
			if (!r.message) return;
			frappe.show_alert({ message: r.message.message, indicator: "green" });
			window.location.href = r.message.route;
		},
	});
}

// Same window as visitor_gate._valid_today: the visit date, or visit_date ..
// pass_valid_until for a multi-day pass. Checking out does not end it.
function pass_valid_today(frm) {
	const { visit_date, multi_day_pass, pass_valid_until } = frm.doc;
	if (!visit_date) return false;
	const today = frappe.datetime.get_today();
	const last_day = multi_day_pass && pass_valid_until ? pass_valid_until : visit_date;
	return visit_date <= today && today <= last_day;
}

function add_gate_buttons(frm) {
	// Only a submitted (approved) pass reaches the gate — never a draft or a cancelled one.
	if (frm.is_new() || frm.doc.docstatus !== 1) return;

	const stage = frm.doc.status;
	// A visitor who stepped out comes back in on the same pass.
	const reentry = stage === "Checked-Out" && pass_valid_today(frm);

	if (["Approved", "Items Verified"].includes(stage) || reentry) {
		frm.add_custom_button(__("Check In"), () => call_gate_endpoint(frm, "visitor_checkin"));
	}

	if (stage === "Checked-In") {
		frm.add_custom_button(__("Check Out"), () => call_gate_endpoint(frm, "visitor_checkout"));
	}
}
