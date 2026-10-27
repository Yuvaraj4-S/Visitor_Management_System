// "New Walk-In": register a visitor who arrives at the gate without a pass.
//
// Loaded on the Visitor Pass list and the Security Log list and form only
// (hooks.py doctype_js / doctype_list_js), so it runs on no other app's pages.
// The server side is visitor_management/walk_in.py: it creates the pass for
// today, at the server's time, and sends it for approval through the normal lane.
//
// Frappe evaluates this file once for each of those views, so it is written to
// be run again: the shared code is set up once, and each view is hooked once.
// The Gate Desk opens the form with /desk/visitor-pass?walk_in=1.

window.vms_walk_in =
	window.vms_walk_in ||
	(() => {
		const API = "visitormanagement.visitor_management.walk_in";
		const LINK_QUERY = "visitormanagement.visitor_management.link_queries.search";
		const LABEL = __("New Walk-In");
		let enabled_promise = null;

		// Asked once per page load: switched on in VMS Settings, and may this user raise a pass?
		function when_enabled(callback) {
			if (!enabled_promise) {
				enabled_promise = frappe.xcall(`${API}.walk_in_enabled`).catch(() => false);
			}
			enabled_promise.then((enabled) => enabled && callback());
		}

		function open_dialog() {
			const dialog = new frappe.ui.Dialog({
				title: __("New Walk-In Visitor"),
				size: "large",
				fields: [
					{
						fieldtype: "HTML",
						fieldname: "intro",
						options: `<p class="text-muted small">${__(
							"For a visitor at the gate today. Create sends the pass for approval; you are told when it is approved."
						)}</p>`,
					},
					{ fieldtype: "Section Break", label: __("Visitor") },
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
						fieldtype: "Link",
						fieldname: "visitor_type",
						label: __("Visitor Type"),
						options: "Visitor Type",
						reqd: 1,
						get_query: () => ({ filters: { is_active: 1 } }),
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
						fieldname: "id_proof_masked",
						label: __("ID Proof Number"),
						reqd: 1,
					},
					{
						fieldtype: "Attach Image",
						fieldname: "visitor_photo",
						label: __("Visitor Photo"),
						reqd: 1,
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
						// Guards cannot read Employee; the app's picker returns name and title only,
						// and the server checks the host on save.
						ignore_link_validation: 1,
						get_query: () => ({ query: LINK_QUERY, filters: { status: "Active" } }),
						onchange() {
							show_host(dialog);
						},
					},
					{ fieldtype: "Column Break" },
					{
						fieldtype: "Small Text",
						fieldname: "purpose_of_visit",
						label: __("Purpose of Visit"),
						reqd: 1,
					},
				],
				primary_action_label: __("Create and Send for Approval"),
				primary_action(values) {
					dialog.disable_primary_action();
					frappe
						.xcall(`${API}.create_walk_in_pass`, { values }, "POST", { freeze: true })
						.then((result) => {
							dialog.hide();
							if (result.submitted) {
								frappe.show_alert(
									{
										message: __("{0} sent for approval.", [result.name]),
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
							frappe.set_route("Form", "Visitor Pass", result.name);
						})
						.catch(() => dialog.enable_primary_action());
				},
			});
			// The picker checks which form is searching (link_queries.authorize).
			const host = dialog.fields_dict.person_to_visit;
			host.doctype = "Visitor Pass";
			host.df.parent = "Visitor Pass";
			dialog.show();
			return dialog;
		}

		function show_host(dialog) {
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
				})
				.then((details) => {
					const label = [details && details.employee_name, details && details.department]
						.filter(Boolean)
						.join(", ");
					field.set_description(label ? frappe.utils.escape_html(label) : "");
				})
				.catch(() => field.set_description(""));
		}

		function open_from_route() {
			// /desk/visitor-pass?walk_in=1 (the Gate Desk link): open the form once.
			const params = new URLSearchParams(window.location.search);
			if (params.get("walk_in") !== "1") return;
			params.delete("walk_in");
			const query = params.toString();
			window.history.replaceState(
				null,
				"",
				window.location.pathname + (query ? `?${query}` : "") + window.location.hash
			);
			when_enabled(open_dialog);
		}

		// Adds the button to a list view, keeping whatever onload the list already has.
		function hook_list(doctype) {
			const settings = (frappe.listview_settings[doctype] =
				frappe.listview_settings[doctype] || {});
			if (settings.__vms_walk_in) return;
			settings.__vms_walk_in = true;
			const onload = settings.onload;
			settings.onload = function (listview) {
				if (onload) onload.apply(this, arguments);
				when_enabled(() => listview.page.add_inner_button(LABEL, open_dialog));
				if (doctype === "Visitor Pass") open_from_route();
			};
		}

		function hook_form(doctype) {
			frappe.ui.form.on(doctype, {
				refresh(frm) {
					when_enabled(() => frm.add_custom_button(LABEL, open_dialog));
				},
			});
		}

		let form_hooked = false;
		return {
			open_dialog,
			when_enabled,
			hook() {
				hook_list("Visitor Pass");
				hook_list("Security Log");
				if (!form_hooked) {
					form_hooked = true;
					hook_form("Security Log");
				}
			},
		};
	})();

window.vms_walk_in.hook();
