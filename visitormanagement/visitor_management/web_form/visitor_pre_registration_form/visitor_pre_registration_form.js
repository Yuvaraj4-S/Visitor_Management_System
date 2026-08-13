const MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024;

const ALWAYS_LOCKED_FIELDS = [
	"visitor_type",
	"email_id",
	"visit_date",
	"expected_checkin",
	"expected_checkout",
	"person_to_visit",
];
const CONDITIONALLY_LOCKED_FIELDS = [
	"purpose_of_visit",
];
let LOCKED_FIELDS = [...ALWAYS_LOCKED_FIELDS];

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
	// The WebForm object our overrides are currently installed on. Core builds
	// its instance after this script first runs and assigns it to
	// frappe.web_form, discarding whatever we patched onto the earlier one — so
	// remembering *which* object we patched is the only reliable latch.
	patchedForm: null,
};
let hospitalityWatchState = {
	started: false,
	lastSignature: null,
};
let genericFormState = {
	bound: false,
};

function escapeHtml(value) {
	return frappe.utils.escape_html(value == null ? "" : String(value));
}

function isValidMobile(value) {
	if (!value) return false;
	const trimmed = String(value).trim();
	if (!trimmed) return false;
	const digits = trimmed.replace(/\D/g, "");
	if (trimmed.startsWith("+")) {
		return digits.length >= 11 && digits.length <= 15;
	}
	if (digits.length === 10) return true;
	if (digits.length >= 11 && digits.length <= 15) return true;
	return false;
}

function attachMobileValidator() {
	const field = frappe.web_form?.fields_dict?.mobile_number;
	if (!field) {
		return;
	}
	// Do this before anything else touches the control — core throws on its own
	// first render, whether or not we are prefilling a value.
	guardPhoneControl(field);
	if (field._vmMobileValidatorBound) {
		return;
	}
	field._vmMobileValidatorBound = true;

	const $wrap = $(field.wrapper);
	let $err = $wrap.find(".vm-mobile-error");
	if (!$err.length) {
		$err = $('<div class="vm-mobile-error"></div>');
		const $target = $wrap.find(".control-input-wrapper").first();
		if ($target.length) {
			$target.append($err);
		} else {
			$wrap.append($err);
		}
	}

	const $input = frappe.web_form.get_input("mobile_number");
	if (!$input || !$input.length) {
		return;
	}

	const showError = () => {
		$err.text(
			__("Enter 10 digits (e.g. 9876543210) or +country code + number (e.g. +91 9876543210).")
		).addClass("visible");
	};
	const hideError = () => $err.removeClass("visible");

	$input.on("blur.vmMobile", () => {
		const value = $input.val() || "";
		if (!value.trim()) {
			hideError();
			return;
		}
		if (isValidMobile(value)) {
			hideError();
		} else {
			showError();
		}
	});
	$input.on("input.vmMobile", () => {
		if (isValidMobile($input.val())) {
			hideError();
		}
	});
}

function approxBase64Bytes(value) {
	if (!value || typeof value !== "string") return 0;
	const idx = value.indexOf(",");
	const payload = idx > -1 ? value.slice(idx + 1) : value;
	// each 4 base64 chars encode 3 bytes
	const padding = (payload.match(/=+$/) || [""])[0].length;
	return Math.max(0, Math.floor((payload.length * 3) / 4) - padding);
}

function checkAttachmentSize(fieldname, label) {
	const value = frappe.web_form?.doc?.[fieldname];
	if (!value || typeof value !== "string") return null;
	if (!value.startsWith("data:")) return null;
	const bytes = approxBase64Bytes(value);
	if (bytes > MAX_ATTACHMENT_BYTES) {
		return __("{0} is {1} MB — please upload a file under 5 MB.", [
			__(label),
			(bytes / 1024 / 1024).toFixed(1),
		]);
	}
	return null;
}


// Frappe prepares the success message with `frappe.db.escape()` — a *SQL* string
// escaper — before handing it to the HTML template (web_form.py:461). Every
// apostrophe therefore reaches the page as a literal backslash: a visitor who
// submits is told "We\'ve sent a confirmation". The stored text is clean; the
// damage happens on the way out, so it is undone on the way in.
function unescapeSuccessMessage() {
	document.querySelectorAll(".success-message, .success-title").forEach((el) => {
		if (el.textContent && el.textContent.includes("\\'")) {
			el.textContent = el.textContent.replace(/\\'/g, "'").replace(/\\"/g, '"');
		}
	});
}

function renderSuccessPanel(reference) {
	const ref = reference ? escapeHtml(reference) : "";
	const html = `
		<div class="vm-success-panel" role="status" aria-live="polite">
			<div style="font-size:1.1rem; font-weight:700; color:var(--vr-success);">
				${__("Pre-registration submitted")}
			</div>
			<div style="font-size:0.9rem; color:var(--vr-text-muted); margin-top:4px;">
				${__("Keep this reference for your records.")}
			</div>
			${ref ? `<div class="vm-success-ref">${ref}</div>` : ""}
			<div class="vm-success-next">
				<strong>${__("What happens next")}</strong>
				<ol>
					<li>${__("A confirmation email is on its way to you.")}</li>
					<li>${__("Your host will review and approve the request.")}</li>
					<li>${__("Once approved, you'll get a final email with a QR pass — show it at the gate.")}</li>
				</ol>
			</div>
		</div>
	`;
	const $target = $(".web-form-container").first();
	if (!$target.length) return;
	$target.find(".vm-success-panel").remove();
	$target.prepend(html);
	window.scrollTo({ top: 0, behavior: "smooth" });
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
			<label class="control-label">${__("Items")}</label>
			<textarea rows="3" class="form-control vm-item-name" placeholder="${__("e.g. Dell laptop, USB drive, toolkit")}">${escapeHtml(item.item_name || "")}</textarea>
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
			<div class="vm-items-intro">
				${__("Will you be carrying any laptops, storage devices, tools, or similar items? List them below so security can verify them at the gate.")}
			</div>
			<div class="vm-visitor-items-list mt-3"></div>
		</div>
	`;

	$(".web-form .web-form-footer").before(sectionHtml);
	// Single fixed row — no add/remove controls.
	$(".vm-visitor-items-list").append(getVisitorItemRowTemplate());
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
				quantity: 1,
				description: "",
			};
		})
		.get()
		.filter(Boolean);
}

function getFieldValue(fieldname) {
	if (!frappe.web_form) {
		return null;
	}

	if (LOCKED_FIELDS.includes(fieldname)) {
		const lockedDocValue = frappe.web_form.doc?.[fieldname];
		if (lockedDocValue !== undefined && lockedDocValue !== null && lockedDocValue !== "") {
			return lockedDocValue;
		}

		const lockedInvitationValue = invitationContextState.values?.[fieldname];
		if (
			lockedInvitationValue !== undefined &&
			lockedInvitationValue !== null &&
			lockedInvitationValue !== ""
		) {
			return lockedInvitationValue;
		}

		const lockedBootValue = window.vmInvitationValues?.[fieldname];
		return lockedBootValue !== undefined ? lockedBootValue : null;
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

function setFieldInputDirectly(field, value) {
	field.value = value;
	if (field.$input) {
		field.$input.val(value == null ? "" : value);
	} else if (field.input) {
		$(field.input).val(value == null ? "" : value);
	}
	field.set_disp_area?.(value);
}

async function setFieldValue(fieldname, value) {
	const field = frappe.web_form?.fields_dict?.[fieldname];
	if (!field) {
		return;
	}

	if (LOCKED_FIELDS.includes(fieldname)) {
		setFieldInputDirectly(field, value);
		frappe.web_form.doc[fieldname] = value;
		field.refresh?.();
		clearStaleError(field);
		return;
	}

	// Phone is handled on its own path: core's set_formatted_input throws while
	// the control is still building its ISD element, which both loses the value
	// and floods the console. Wait for the control, then write it directly.
	if (field.df?.fieldtype === "Phone") {
		frappe.web_form.doc[fieldname] = value;
		await ensurePhoneValueApplied(field, value);
		clearStaleError(field);
		return;
	}

	try {
		await frappe.web_form.set_value(fieldname, value);
	} catch (error) {
		// Some web form controls, especially autocomplete/link-like fields,
		// can throw during early boot if suggestion lists are not ready yet.
		console.warn(`Falling back to direct assignment for ${fieldname}`, error);
		setFieldInputDirectly(field, value);
	}

	frappe.web_form.doc[fieldname] = value;
	field.refresh?.();
	clearStaleError(field);
}

// A mandatory control is painted has-error while it is empty. Filling it from an
// invitation never runs the validation that clears that flag, so a perfectly
// valid prefilled value greets the visitor outlined in red. Drop the flag once
// we have actually put something in the field.
function clearStaleError(field) {
	if (!field?.$wrapper) {
		return;
	}
	const filled = field.value ?? frappe.web_form?.doc?.[field.df?.fieldname];
	if (filled !== undefined && filled !== null && String(filled).trim() !== "") {
		field.$wrapper.removeClass("has-error");
	}
}

// Core's Phone control builds its ISD element asynchronously. When a prefilled
// value arrives before that finishes, set_formatted_input throws on `this.$isd`
// and leaves the input blank — the visitor then sees an empty Mobile Number on
// an invitation link. Re-apply the value once the control has rendered.
// Frappe's own ControlPhone.set_formatted_input reads `this.$isd.text()`, and
// on a web form the ISD element is built after the first render — so core throws
// a TypeError on a page an anonymous visitor is looking at. The value still
// lands (see ensurePhoneValueApplied), but the console error is noise on a
// guest-facing page and would mask a real one during support.
//
// Guard the instance, not the class: once the ISD element exists core's own
// implementation takes over again.
function guardPhoneControl(field) {
	if (!field || field._vmPhoneGuarded || typeof field.set_formatted_input !== "function") {
		return;
	}
	field._vmPhoneGuarded = true;
	const original = field.set_formatted_input.bind(field);
	field.set_formatted_input = function (value) {
		if (!this.$isd || !this.$isd.length) {
			this.value = value;
			if (this.$input) {
				this.$input.val(value == null ? "" : value);
			}
			return;
		}
		return original(value);
	};
}

async function ensurePhoneValueApplied(field, value) {
	guardPhoneControl(field);
	for (let attempt = 0; attempt < 25; attempt++) {
		if (field.$isd && field.$isd.length && field.$input) {
			break;
		}
		await new Promise((resolve) => setTimeout(resolve, 100));
	}
	setFieldInputDirectly(field, value);
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
			Object.keys(frappe.web_form.fields_dict).length > 0 &&
			document.querySelector('.frappe-control[data-fieldname="visitor_type"]') &&
			document.querySelector(".web-form-footer")
	);
}

function getInvitationToken() {
	return new URLSearchParams(window.location.search).get("token");
}

function getPortalSubmissionState() {
	// Portal submissions always land as Draft. The server is authoritative here
	// (portal._get_portal_submission_state) and staff advance the pass into the
	// approval workflow from the desk. The approval lane itself is derived from
	// the Visitor Type's approver_role by the workflow — never hardcoded per type.
	return "Draft";
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

function bindGenericFormHandlers() {
	if (genericFormState.bound || !frappe.web_form) {
		return;
	}

	genericFormState.bound = true;

	const $visitorTypeInput = frappe.web_form.get_input("visitor_type");
	$visitorTypeInput.on("change", () => {
		setTimeout(() => {
			applyVisitorTypeSections(getFieldValue("visitor_type"));
		}, 0);
	});
}

function unlockDirectAccessFields() {
	LOCKED_FIELDS.forEach((fieldname) => {
		const field = frappe.web_form?.fields_dict?.[fieldname];
		if (!field) {
			return;
		}

		frappe.web_form.set_df_property(fieldname, "read_only", 0);
		const $input = frappe.web_form.get_input(fieldname);
		$input.prop("readonly", false).prop("disabled", false);
		$input.removeAttr("tabindex");
		$(field.wrapper).removeClass("vm-locked-field vm-host-field");
		$(field.wrapper).find(".vm-locked-display").remove();
		$(field.wrapper).find(".control-input").show();
		$(field.wrapper).find(".control-value").hide();
	});
}

function enableDirectAccessMode() {
	unlockDirectAccessFields();
	bindGenericFormHandlers();
	attachHospitalityHandlers();
	startHospitalityWatcher();
	renderVisitorItems();
	applyVisitorTypeSections(getFieldValue("visitor_type"));
	attachMobileValidator();
	setFormVisibility(true);
	setSubmitDisabled(false);
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
		const field = frappe.web_form.fields_dict[fieldname];
		if (!field) {
			continue;
		}

		await setFieldValue(fieldname, value);
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

function getInvitationBackedValue(fieldname) {
	return (
		invitationContextState.values?.[fieldname] ??
		window.vmInvitationValues?.[fieldname]
	);
}

function isMissingRequiredValue(value, field) {
	if (value === null || value === undefined) {
		return true;
	}

	if (field?.df?.fieldtype === "Text Editor") {
		return !String(value).replace(/<[^>]*>/g, "").trim();
	}

	if (typeof value === "string") {
		return !value.trim();
	}

	return false;
}

function validateRequiredFieldsForSave(docValues) {
	const missingLabels = [];

	Object.values(frappe.web_form.fields_dict || {}).forEach((field) => {
		if (!field?.df?.reqd) {
			return;
		}

		const fieldname = field.df.fieldname;
		const value = docValues[fieldname];
		if (!isMissingRequiredValue(value, field)) {
			return;
		}

		missingLabels.push(__(field.df.label));
	});

	if (!missingLabels.length) {
		return true;
	}

	frappe.msgprint({
		title: __("Missing Values Required"),
		message:
			__("Following fields have missing values:") +
			"<br><br><ul><li>" +
			missingLabels.join("<li>") +
			"</ul>",
		indicator: "orange",
	});
	return false;
}

function lockInvitationFields() {
	LOCKED_FIELDS.forEach((fieldname) => {
		const field = frappe.web_form.fields_dict[fieldname];
		if (!field) {
			return;
		}

		// Locked invitation fields are source-of-truth values from the host.
		// Skip client-side option/link validation that may run before controls finish booting.
		field.df.ignore_validation = 1;
		field.df.ignore_link_validation = 1;
		frappe.web_form.set_df_property(fieldname, "reqd", 0);
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

	// `visitor_invitation` is no longer one of the form's fields — it is set
	// server-side from the token, so it is not bound from the request body at
	// all. Core's set_df_property dereferences the control without checking it
	// exists, so calling it for a field that was never rendered throws.
	$(".discard-btn").hide();
	setSubmitDisabled(true);
	setFormVisibility(false);
	renderLockedFieldValues(window.vmInvitationValues || {});

	if (!token) {
		enableDirectAccessMode();
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
			setFormVisibility(true);
			setSubmitDisabled(true);
			return;
		}

		invitationContextState = {
			...invitationContextState,
			loaded: true,
			valid: true,
			invitation: context.invitation,
			values: context.values || {},
		};
		// Lock conditional fields only if host filled them
		LOCKED_FIELDS = [...ALWAYS_LOCKED_FIELDS];
		for (const fieldname of CONDITIONALLY_LOCKED_FIELDS) {
			const val = context.values?.[fieldname];
			if (val && String(val).trim()) {
				LOCKED_FIELDS.push(fieldname);
			}
		}

		renderLockedFieldValues(context.values || {});
		await applyInvitationValuesWithRetry(context.values || {});
		ensureInvitationBinding();
		lockInvitationFields();
		applyVisitorTypeSections(context.values?.visitor_type);
		attachHospitalityHandlers();
		startHospitalityWatcher();
		await syncHospitalityFieldsFromMealToggle();
		renderVisitorItems(getVisitorItemsFromContext());
		attachMobileValidator();
		setFormVisibility(true);
		setSubmitDisabled(false);
	} catch (error) {
		console.error("Failed to load invitation context", error);
		setFormVisibility(true);
		setSubmitDisabled(false);
	}
}

function setupInvitationHooks() {
	if (!frappe.web_form || invitationContextState.patchedForm === frappe.web_form) {
		return;
	}

	// A swapped-in instance is a fresh form: its fields are unlocked and its
	// save() is core's again, so the after-load work has to run over.
	invitationContextState.patchedForm = frappe.web_form;
	invitationContextState.afterLoadTriggered = false;
	invitationContextState.hooksAttached = true;
	frappe.web_form.after_load = handleInvitationAfterLoad;

	frappe.web_form.validate = () => {
		const token = getInvitationToken();
		if (token && (!invitationContextState.loaded || !invitationContextState.valid)) {
			frappe.msgprint(__("Invitation details are still loading or invalid. Reopen the invitation link and try again."));
			return false;
		}

		if (token && !ensureInvitationBinding()) {
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

		const docValues = this.get_values(true, true) || {};
		if (window.saving) {
			return false;
		}

		LOCKED_FIELDS.forEach((fieldname) => {
			const invitationValue = getInvitationBackedValue(fieldname);
			if (invitationValue !== undefined && invitationValue !== null && invitationValue !== "") {
				docValues[fieldname] = invitationValue;
			}
		});

		if (!validateRequiredFieldsForSave(docValues)) {
			return false;
		}

		const mobileForCheck =
			docValues.mobile_number || frappe.web_form.doc?.mobile_number || "";
		if (mobileForCheck && !isValidMobile(mobileForCheck)) {
			frappe.msgprint({
				title: __("Check your mobile number"),
				message: __(
					"Mobile number doesn't look right. Enter 10 digits (e.g. 9876543210), or +country code + number (e.g. +91 9876543210)."
				),
				indicator: "orange",
			});
			return false;
		}

		const sizeIssue =
			checkAttachmentSize("id_proof_scan", "ID Proof Scan") ||
			checkAttachmentSize("visitor_photo", "Visitor Photo");
		if (sizeIssue) {
			frappe.msgprint({
				title: __("File too large"),
				message: sizeIssue,
				indicator: "orange",
			});
			return false;
		}

		Object.assign(this.doc, docValues);

		this.doc.visitor_items = collectVisitorItems();
		this.doc.doctype = this.doc_type;
		this.doc.web_form_name = this.name;
		this.doc.invitation_token = getInvitationToken();
		this.doc.entry_type = "New";
		this.doc.request_channel = "Portal";
		this.doc.submission_action = "submit";
		const targetState = getPortalSubmissionState(this.doc.visitor_type, this.doc.submission_action);
		this.doc.status = targetState;
		this.doc.workflow_state = targetState;
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
					try {
						renderSuccessPanel(response.message?.name);
					} catch (e) {
						console.warn("Could not render success panel", e);
					}
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

	// Keep polling for the whole window even once the hooks are on, rather than
	// stopping at the first success: core can replace frappe.web_form after we
	// have already patched, and setupInvitationHooks() re-installs on the new
	// instance when it sees one. Bailing early is what left the form running
	// core's plain save(), so the invitation was never marked submitted.
	if (retries > 0) {
		setTimeout(() => bootstrapInvitationHooks(retries - 1), 100);
	}
}

bootstrapInvitationHooks();
frappe.ready(() => {
	bootstrapInvitationHooks();
	unescapeSuccessMessage();
});
