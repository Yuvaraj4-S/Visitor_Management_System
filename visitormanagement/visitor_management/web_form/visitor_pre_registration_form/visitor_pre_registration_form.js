const LOCKED_FIELDS = [
	"visitor_type",
	"email_id",
	"visit_date",
	"expected_checkin",
	"expected_checkout",
	"person_to_visit",
	"purpose_of_visit",
];

const TYPE_SECTION_LABELS = {
	Contractor: "Contractor Details",
	Supplier: "Supplier Details",
	Customer: "Customer Details",
	Candidate: "Candidate Details",
	VIP: "VIP Details",
};

let invitationContextState = {
	loaded: false,
	valid: false,
	invitation: null,
	values: {},
	afterLoadTriggered: false,
	hooksAttached: false,
};
let hospitalityWatchState = {
	started: false,
	lastSignature: null,
};

function escapeHtml(value) {
	return frappe.utils.escape_html(value == null ? "" : String(value));
}

function setFormVisibility(visible) {
	$(".web-form .form-column, .web-form .section-body, .web-form .web-form-footer, .vm-custom-block").toggleClass(
		"vm-form-hidden",
		!visible
	);
}

function applyVisitorTypeSections(visitorType) {
	if (!visitorType) {
		return;
	}

	const activeLabel = TYPE_SECTION_LABELS[visitorType];

	$(".web-form .row.form-section").each(function () {
		const $section = $(this);
		const $head = $section.find(".section-head");
		if (!$head.length) {
			return;
		}

		const sectionLabel = $head.text().trim();
		const isTypeSection = Object.values(TYPE_SECTION_LABELS).includes(sectionLabel);
		if (!isTypeSection) {
			return;
		}

		if (sectionLabel === activeLabel) {
			$section.removeClass("vm-form-hidden").show();
		} else {
			$section.addClass("vm-form-hidden").hide();
		}
	});
}

function getVisitorItemsFromContext() {
	const items = invitationContextState.values?.visitor_items;
	return Array.isArray(items) ? items : [];
}

function getVisitorItemRowTemplate(item = {}) {
	return `
		<div class="vm-visitor-item-row vm-hospitality-card">
			<div class="vm-locked-grid">
				<div>
					<label class="control-label">${__("Item Name")}</label>
					<input type="text" class="form-control vm-item-name" value="${escapeHtml(item.item_name || "")}">
				</div>
				<div>
					<label class="control-label">${__("Quantity")}</label>
					<input type="number" min="1" step="0.01" class="form-control vm-item-quantity" value="${escapeHtml(item.quantity || 1)}">
				</div>
				<div>
					<label class="control-label">${__("Item Category")}</label>
					<input type="text" class="form-control vm-item-category" value="${escapeHtml(item.item_category || "")}">
				</div>
				<div>
					<label class="control-label">${__("Serial Number")}</label>
					<input type="text" class="form-control vm-item-serial-number" value="${escapeHtml(item.serial_number || "")}">
				</div>
				<div>
					<label class="control-label">${__("Unit Of Measure")}</label>
					<input type="text" class="form-control vm-item-uom" value="${escapeHtml(item.unit_of_measure || "")}">
				</div>
				<div>
					<label class="control-label">${__("Estimated Value")}</label>
					<input type="number" min="0" step="0.01" class="form-control vm-item-estimated-value" value="${escapeHtml(item.estimated_value || "")}">
				</div>
			</div>
			<div class="mt-3">
				<label class="control-label">${__("Description")}</label>
				<textarea class="form-control vm-item-description">${escapeHtml(item.description || "")}</textarea>
			</div>
			<div class="mt-3 d-flex justify-content-end">
				<button type="button" class="btn btn-default btn-sm vm-remove-item">${__("Remove Item")}</button>
			</div>
		</div>
	`;
}

function ensureVisitorItemsSection() {
	if ($(".vm-visitor-items-section").length) {
		return;
	}

	const sectionHtml = `
		<div class="vm-custom-block vm-visitor-items-section vm-locked-section">
			<div class="vm-locked-section-title">${__("Visitor Items")}</div>
			<div class="help-box small text-muted">
				${__("Add all items carried by the visitor. These details will be visible for security verification.")}
			</div>
			<div class="vm-visitor-items-list mt-3"></div>
			<div class="mt-3">
				<button type="button" class="btn btn-default vm-add-item">${__("Add Item")}</button>
			</div>
		</div>
	`;

	$(".web-form .web-form-footer").before(sectionHtml);
	$(".vm-visitor-items-section").on("click", ".vm-add-item", () => {
		$(".vm-visitor-items-list").append(getVisitorItemRowTemplate());
	});
	$(".vm-visitor-items-section").on("click", ".vm-remove-item", function () {
		$(this).closest(".vm-visitor-item-row").remove();
	});
}

function renderVisitorItems(items = []) {
	ensureVisitorItemsSection();
	const $list = $(".vm-visitor-items-list");
	$list.empty();

	if (!items.length) {
		$list.append(getVisitorItemRowTemplate());
		return;
	}

	items.forEach((item) => {
		$list.append(getVisitorItemRowTemplate(item));
	});
}

function collectVisitorItems() {
	return $(".vm-visitor-item-row")
		.map(function () {
			const $row = $(this);
			const itemName = ($row.find(".vm-item-name").val() || "").trim();
			if (!itemName) {
				return null;
			}

			return {
				item_name: itemName,
				quantity: $row.find(".vm-item-quantity").val() || 1,
				item_category: ($row.find(".vm-item-category").val() || "").trim(),
				serial_number: ($row.find(".vm-item-serial-number").val() || "").trim(),
				unit_of_measure: ($row.find(".vm-item-uom").val() || "").trim(),
				estimated_value: $row.find(".vm-item-estimated-value").val() || null,
				description: ($row.find(".vm-item-description").val() || "").trim(),
			};
		})
		.get()
		.filter(Boolean);
}

function getFieldValue(fieldname) {
	if (!frappe.web_form) {
		return null;
	}

	const fieldValue = frappe.web_form.fields_dict?.[fieldname]
		? frappe.web_form.get_value(fieldname)
		: undefined;
	if (fieldValue !== undefined && fieldValue !== null && fieldValue !== "") {
		return fieldValue;
	}

	const docValue = frappe.web_form.doc?.[fieldname];
	if (docValue !== undefined && docValue !== null && docValue !== "") {
		return docValue;
	}

	const invitationValue = invitationContextState.values?.[fieldname];
	if (invitationValue !== undefined && invitationValue !== null && invitationValue !== "") {
		return invitationValue;
	}

	const bootValue = window.vmInvitationValues?.[fieldname];
	return bootValue !== undefined ? bootValue : null;
}

async function setFieldValue(fieldname, value) {
	if (!frappe.web_form?.fields_dict?.[fieldname]) {
		return;
	}

	await frappe.web_form.set_value(fieldname, value);
	frappe.web_form.doc[fieldname] = value;
	frappe.web_form.fields_dict[fieldname].refresh?.();
}

async function syncHospitalityFieldsFromMealToggle() {
	if (!frappe.web_form?.fields_dict?.meal_required) {
		return;
	}

	const mealRequired = Number(getFieldValue("meal_required")) ? 1 : 0;
	if (!mealRequired) {
		await setFieldValue("meal_type", "");
		await setFieldValue("assigned_meal_slots", "");
		await setFieldValue("hospitality_type", "");
		return;
	}

	const visit_date = getFieldValue("visit_date");
	const expected_checkin = getFieldValue("expected_checkin");
	const expected_checkout = getFieldValue("expected_checkout");

	if (!visit_date || !expected_checkin || !expected_checkout) {
		return;
	}

	try {
		const { message } = await frappe.call({
			method: "visitormanagement.visitor_management.lifecycle.get_hospitality_meal_plan",
			args: { visit_date, expected_checkin, expected_checkout },
		});

		if (!message) {
			return;
		}

		if (!getFieldValue("meal_type")) {
			await setFieldValue("meal_type", message.meal_type || "");
		}
		await setFieldValue("assigned_meal_slots", message.assigned_meal_slots || "");
		await setFieldValue("hospitality_type", message.hospitality_type || "");
		if (frappe.web_form.fields_dict.service_time && !getFieldValue("service_time")) {
			await setFieldValue("service_time", message.service_time || null);
		}
	} catch (error) {
		console.error("Failed to derive hospitality meal plan", error);
	}
}

function attachHospitalityHandlers() {
	const mealRequiredField = frappe.web_form?.fields_dict?.meal_required;
	if (!mealRequiredField || mealRequiredField._vmHospitalityBound) {
		return;
	}

	mealRequiredField._vmHospitalityBound = true;
	const $input = frappe.web_form.get_input("meal_required");
	$input.on("change", () => {
		setTimeout(() => {
			syncHospitalityFieldsFromMealToggle();
		}, 0);
	});
}

function startHospitalityWatcher() {
	if (hospitalityWatchState.started || !frappe.web_form) {
		return;
	}

	hospitalityWatchState.started = true;
	window.setInterval(() => {
		if (!frappe.web_form?.fields_dict?.meal_required) {
			return;
		}

		const signature = JSON.stringify({
			meal_required: getFieldValue("meal_required"),
			visit_date: getFieldValue("visit_date"),
			expected_checkin: getFieldValue("expected_checkin"),
			expected_checkout: getFieldValue("expected_checkout"),
		});

		if (signature === hospitalityWatchState.lastSignature) {
			return;
		}

		hospitalityWatchState.lastSignature = signature;
		syncHospitalityFieldsFromMealToggle();
	}, 400);
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

function getBootInvitationContext() {
	if (window.vmInvitationValues === undefined) {
		return null;
	}

	return {
		valid: Boolean(window.vmInvitationValid),
		invitation: window.vmInvitationName,
		message: window.vmInvitationMessage,
		values: window.vmInvitationValues || {},
	};
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
		applyVisitorTypeSections(values?.visitor_type);
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
	renderLockedFieldValues(window.vmInvitationValues || {});

	if (!token) {
		return;
	}

	try {
		let context = getBootInvitationContext();
		if (!context) {
			const response = await frappe.call({
				method: "visitormanagement.visitor_management.doctype.visitor_invitation.visitor_invitation.get_web_form_context",
				args: { token },
			});
			context = response.message || {};
		}

		if (!context.valid) {
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
		applyVisitorTypeSections(context.values?.visitor_type);
		attachHospitalityHandlers();
		startHospitalityWatcher();
		await syncHospitalityFieldsFromMealToggle();
		renderVisitorItems(getVisitorItemsFromContext());
		setFormVisibility(true);
		setSubmitDisabled(false);
	} catch (error) {
		console.error("Failed to load invitation context", error);
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
		if (window.saving) {
			return false;
		}
		if (!docValues) {
			frappe.msgprint(
				__("Please fill all required fields before submitting."),
				__("Missing Information")
			);
			return false;
		}

		Object.assign(this.doc, docValues);
		this.doc.visitor_items = collectVisitorItems();
		this.doc.doctype = this.doc_type;
		this.doc.web_form_name = this.name;
		this.doc.invitation_token = getInvitationToken();
		this.doc.entry_type = "New";
		this.doc.request_channel = "Portal";
		this.doc.status = "Draft";
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
	startHospitalityWatcher();
	renderLockedFieldValues(window.vmInvitationValues || {});

	if (invitationContextState.hooksAttached && !invitationContextState.afterLoadTriggered) {
		handleInvitationAfterLoad();
	}

	if ((!invitationContextState.hooksAttached || !invitationContextState.afterLoadTriggered) && retries > 0) {
		setTimeout(() => bootstrapInvitationHooks(retries - 1), 100);
	}
}

bootstrapInvitationHooks();
frappe.ready(() => bootstrapInvitationHooks());
