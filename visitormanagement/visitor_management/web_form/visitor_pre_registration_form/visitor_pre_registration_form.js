const LOCKED_FIELDS = [
	"visitor_type",
	"email_id",
	"visit_date",
	"expected_checkin",
	"expected_checkout",
	"person_to_visit",
	"purpose_of_visit",
	"meal_required",
	"meal_type",
	"assigned_meal_slots",
	"hospitality_type",
	"refreshments_required",
	"conference_room",
];
const HOSPITALITY_FIELDS = [
	"meal_required",
	"meal_type",
	"assigned_meal_slots",
	"hospitality_type",
	"refreshments_required",
	"conference_room",
];
const HOSPITALITY_LABELS = {
	meal_required: __("Meal Required"),
	meal_type: __("Meal Type"),
	assigned_meal_slots: __("Meal Slots"),
	hospitality_type: __("Hospitality Type"),
	refreshments_required: __("Refreshments Required"),
	conference_room: __("Conference Room"),
};
let invitationContextState = {
	loaded: false,
	valid: false,
	invitation: null,
	values: {},
	afterLoadTriggered: false,
	hooksAttached: false,
};

function renderStatusPanel(type, title, message) {
	const statusHtml = `
		<div class="vm-status-panel vm-status-${type}">
			<div class="vm-status-title">${frappe.utils.escape_html(__(title))}</div>
			<div class="vm-status-message">${frappe.utils.escape_html(__(message))}</div>
		</div>
	`;

	$(".vm-status-panel").remove();
	$(".web-form-introduction").before(statusHtml);
}

function setFormVisibility(visible) {
	$(".web-form .form-column, .web-form .section-body, .web-form .web-form-footer").toggleClass(
		"vm-form-hidden",
		!visible
	);
}

function areInvitationFieldsReady() {
	return Boolean(
		frappe.web_form &&
			frappe.web_form.fields_dict &&
			frappe.web_form.fields_dict.visitor_invitation &&
			document.querySelector('.frappe-control[data-fieldname="visitor_type"]') &&
			document.querySelector(".web-form-footer")
	);
}

function getInvitationToken() {
	return new URLSearchParams(window.location.search).get("token");
}

function setSubmitDisabled(disabled) {
	$(".submit-btn").prop("disabled", disabled);
}

function syncVisibleLockedField(fieldname, value) {
	const $control = $(`.frappe-control[data-fieldname="${fieldname}"]`);
	if (!$control.length) {
		return;
	}

	const displayValue =
		value === null || value === undefined || value === ""
			? "-"
			: typeof value === "boolean"
				? value
					? __("Yes")
					: __("No")
				: String(value);
	const $wrapper = $control.find(".control-input-wrapper");
	$control.find(".control-input").hide();
	let $display = $wrapper.find(".vm-locked-display");
	if (!$display.length) {
		$display = $('<div class="vm-locked-display like-disabled-input"></div>');
		$wrapper.append($display);
	}
	$display.text(displayValue).show();
	$control.find(".control-value").text(displayValue).show();
	$control.addClass("vm-host-field");
}

function renderHospitalitySection(values = {}) {
	const hasHospitalityData = HOSPITALITY_FIELDS.some((fieldname) => fieldname in values);
	$(".vm-hospitality-section").remove();
	if (!hasHospitalityData) {
		return;
	}

	const cards = HOSPITALITY_FIELDS.map((fieldname) => {
		const rawValue = values[fieldname];
		const displayValue =
			rawValue === null || rawValue === undefined || rawValue === ""
				? "-"
				: typeof rawValue === "boolean"
					? rawValue
						? __("Yes")
						: __("No")
					: String(rawValue);
		return `
			<div class="vm-hospitality-card">
				<div class="vm-hospitality-label">${frappe.utils.escape_html(HOSPITALITY_LABELS[fieldname])}</div>
				<div class="vm-hospitality-value">${frappe.utils.escape_html(displayValue)}</div>
			</div>
		`;
	}).join("");

	const sectionHtml = `
		<div class="row form-section vm-hospitality-section">
			<div class="section-head">${__("Hospitality Details")}</div>
			<div class="vm-hospitality-grid">${cards}</div>
		</div>
	`;
	const $identitySection = $('.section-head').filter((_, el) => $(el).text().trim() === __("Identity Documents")).closest(".form-section");
	if ($identitySection.length) {
		$identitySection.before(sectionHtml);
	} else {
		$(".web-form-footer").before(sectionHtml);
	}
}

function renderLockedFieldValues(values = {}) {
	LOCKED_FIELDS.forEach((fieldname) => {
		if (!(fieldname in values)) {
			return;
		}

		syncVisibleLockedField(fieldname, values[fieldname]);
	});
}

async function applyInvitationValues(values) {
	for (const [fieldname, value] of Object.entries(values || {})) {
		if (!frappe.web_form.fields_dict[fieldname]) {
			continue;
		}

		await frappe.web_form.set_value(fieldname, value);
		frappe.web_form.doc[fieldname] = value;
		frappe.web_form.fields_dict[fieldname].refresh();
		if (LOCKED_FIELDS.includes(fieldname)) {
			syncVisibleLockedField(fieldname, value);
		}
	}
	renderHospitalitySection(values);
}

async function applyInvitationValuesWithRetry(values) {
	await applyInvitationValues(values);

	// Web Form fields can finish wiring their inputs slightly after after_load.
	// Re-applying once keeps the locked invitation values visible on first open.
	setTimeout(() => {
		applyInvitationValues(values);
	}, 150);
	setTimeout(() => {
		LOCKED_FIELDS.forEach((fieldname) => syncVisibleLockedField(fieldname, values?.[fieldname]));
	}, 300);
}

function ensureInvitationBinding() {
	const invitationName =
		invitationContextState.invitation || invitationContextState.values.visitor_invitation;
	if (!invitationName) {
		return false;
	}

	frappe.web_form.doc.visitor_invitation = invitationName;
	if (frappe.web_form.fields_dict.visitor_invitation) {
		frappe.web_form.fields_dict.visitor_invitation.value = invitationName;
		frappe.web_form.fields_dict.visitor_invitation.set_input?.(invitationName);
	}

	return true;
}

function lockInvitationFields() {
	LOCKED_FIELDS.forEach((fieldname) => {
		const field = frappe.web_form.fields_dict[fieldname];
		if (!field) {
			return;
		}

		frappe.web_form.set_df_property(fieldname, "read_only", 1);
		const $input = frappe.web_form.get_input(fieldname);
		$input.prop("readonly", true).prop("disabled", true);
		$input.attr("tabindex", "-1");
		$(field.wrapper).addClass("vm-locked-field vm-host-field");
		syncVisibleLockedField(fieldname, frappe.web_form.doc[fieldname]);
	});
}

async function handleInvitationAfterLoad() {
	if (invitationContextState.afterLoadTriggered) {
		return;
	}

	if (!areInvitationFieldsReady()) {
		setTimeout(() => handleInvitationAfterLoad(), 100);
		return;
	}

	invitationContextState.afterLoadTriggered = true;

	const token = getInvitationToken();
	invitationContextState = {
		...invitationContextState,
		loaded: false,
		valid: false,
		invitation: null,
		values: {},
	};

	frappe.web_form.set_df_property("visitor_invitation", "hidden", 1);
	$(".discard-btn").hide();
	setSubmitDisabled(true);
	setFormVisibility(false);
	renderStatusPanel(
		"loading",
		"Loading Invitation",
		"Invitation details are being fetched. The form will open once the host-set data is ready."
	);
	renderLockedFieldValues(window.vmInvitationValues || {});

	if (!token) {
		renderStatusPanel(
			"error",
			"Invitation Link Missing",
			"Invitation token is missing. Please open the latest invitation link again."
		);
		return;
	}

	try {
		let context = null;
		if (window.vmInvitationValues) {
			context = {
				valid: Boolean(window.vmInvitationValid),
				invitation: window.vmInvitationName,
				message: window.vmInvitationMessage,
				values: window.vmInvitationValues || {},
			};
		} else {
			const response = await frappe.call({
				method: "visitormanagement.visitor_management.doctype.visitor_invitation.visitor_invitation.get_web_form_context",
				args: { token },
			});
			context = response.message || {};
		}

		if (!context.valid) {
			renderStatusPanel(
				"error",
				"Invitation Not Available",
				context.message || "This invitation link is invalid, expired, or already used."
			);
			return;
		}

		invitationContextState = {
			...invitationContextState,
			loaded: true,
			valid: true,
			invitation: context.invitation,
			values: context.values || {},
		};
		renderLockedFieldValues(context.values || {});
		await applyInvitationValuesWithRetry(context.values || {});
		ensureInvitationBinding();
		lockInvitationFields();
		renderStatusPanel(
			"success",
			"Invitation Verified",
			"Host-approved visit details are prefilled inside the form below. Please review them and complete only your personal information."
		);
		setFormVisibility(true);
		setSubmitDisabled(false);
	} catch (error) {
		console.error("Failed to load invitation context", error);
		renderStatusPanel(
			"error",
			"Unable To Load Invitation",
			"Invitation details could not be loaded. Please reopen the invitation link and try again."
		);
	}
}

function setupInvitationHooks() {
	if (!frappe.web_form || invitationContextState.hooksAttached) {
		return;
	}

	invitationContextState.hooksAttached = true;
	frappe.web_form.after_load = handleInvitationAfterLoad;

	frappe.web_form.validate = () => {
		if (!invitationContextState.loaded || !invitationContextState.valid) {
			frappe.msgprint(__("Invitation details are still loading or invalid. Reopen the invitation link and try again."));
			return false;
		}

		if (!ensureInvitationBinding()) {
			frappe.msgprint(__("A valid invitation is required to submit this form."));
			return false;
		}

		return true;
	};

	frappe.web_form.save = function () {
		const valid = this.validate && this.validate();
		if (!valid && valid !== undefined) {
			frappe.msgprint(
				__("Couldn't save, please check the data you have entered"),
				__("Validation Error")
			);
			return false;
		}

		const docValues = this.get_values(false, true);
		if (!docValues || window.saving) {
			return false;
		}

		Object.assign(this.doc, docValues);
		this.doc.doctype = this.doc_type;
		this.doc.web_form_name = this.name;
		this.doc.invitation_token = getInvitationToken();
		this.doc.submission_action = "submit";
		this.doc.visitor_invitation =
			this.doc.visitor_invitation ||
			invitationContextState.invitation ||
			invitationContextState.values.visitor_invitation;

		window.saving = true;
		frappe.form_dirty = false;

		frappe.call({
			type: "POST",
			method: "visitormanagement.visitor_management.portal.submit_pre_registration",
			args: {
				payload: this.doc,
			},
			freeze: true,
			callback: (response) => {
				if (!response.exc) {
					this.handle_success(response.message);
					frappe.web_form.events.trigger("after_save");
					this.after_save && this.after_save();
				}
			},
			always: () => {
				window.saving = false;
			},
		});

		return false;
	};

	// If the form has already rendered before this script attached the hook,
	// run the invitation loader immediately.
	if (frappe.web_form.fields_dict && Object.keys(frappe.web_form.fields_dict).length) {
		setTimeout(() => {
			handleInvitationAfterLoad();
		}, 0);
	}
}

function bootstrapInvitationHooks(retries = 40) {
	setupInvitationHooks();
	renderLockedFieldValues(window.vmInvitationValues || {});

	if (!invitationContextState.hooksAttached && retries > 0) {
		setTimeout(() => bootstrapInvitationHooks(retries - 1), 100);
	}
}

bootstrapInvitationHooks();
frappe.ready(() => bootstrapInvitationHooks());
