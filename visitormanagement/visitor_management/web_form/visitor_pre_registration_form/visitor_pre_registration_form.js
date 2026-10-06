const MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024;

// The ID scan, photo and visa copy are read here in the browser and sent inside
// the submit_pre_registration payload as "filename,data:...;base64,..." — the
// same shape Frappe's Attach control already understands. On Frappe 15 this app
// keeps the System Setting "Allow Guests to Upload Files" off, so a guest
// /upload_file call would be refused; the server checks the real file type and
// size again (portal.py _validate_upload) and stores each one as a private File
// on the new Visitor Pass. Mirrors portal_upload.ALLOWED_EXTENSIONS / MAX_BYTES.
const PORTAL_ATTACH_FIELDS = ["id_proof_scan", "visitor_photo", "custom_visa_copy"];
const ALLOWED_ATTACHMENT_TYPES = [".jpg", ".jpeg", ".png", ".pdf"];

function readAttachmentsInBrowser() {
	PORTAL_ATTACH_FIELDS.forEach((fieldname) => {
		const field = frappe.web_form?.fields_dict?.[fieldname];
		if (!field || !field.df || field._vmReadsInBrowser) {
			return;
		}
		field._vmReadsInBrowser = true;
		// ControlAttach.set_upload_options merges df.options into the uploader
		// options, so these replace its upload-to-server defaults.
		field.df.options = {
			as_dataurl: true,
			allow_multiple: false,
			disable_file_browser: true,
			allow_web_link: false,
			allow_google_drive: false,
			allow_toggle_private: false,
			allow_toggle_optimize: false,
			// The uploader's own "Camera" tile opens a desk webcam dialog. A phone's
			// file picker already offers the camera, and that is the one visitors know.
			allow_take_photo: false,
			restrictions: {
				allowed_file_types: ALLOWED_ATTACHMENT_TYPES,
				// The uploader skips files whose size is not strictly below this.
				max_file_size: MAX_ATTACHMENT_BYTES + 1,
				max_number_of_files: 1,
			},
			on_success: (file) => {
				if (!file || !file.dataurl) {
					return;
				}
				// "," and ":" would break the "filename,dataurl" value format.
				const name = String(file.name || fieldname).replace(/[,:]/g, "_");
				field.set_value(`${name},${file.dataurl}`);
			},
		};
		tidyUploadDialog(field);
	});
}

// The upload dialog always carries a "Set all private / Set all public" button
// (file_uploader.bundle.js make_dialog), whatever options it is given. It means
// nothing to a visitor — and nothing at all here, since the file is read in the
// browser and stored privately by the server. Hide it each time this control
// opens its dialog. Done on the control instance, so nothing of Frappe's is
// changed for any other page.
function tidyUploadDialog(field) {
	if (typeof field.on_attach_click !== "function") {
		return;
	}
	const open = field.on_attach_click.bind(field);
	field.on_attach_click = function () {
		open();
		const dialog = field.file_uploader && field.file_uploader.dialog;
		if (dialog && dialog.$wrapper) {
			// Our own class, not Frappe's "hide": the dialog removes that one again
			// every time a file is added (set_secondary_action_label).
			dialog.$wrapper.find(".btn-modal-secondary").addClass("vm-form-hidden");
			dialog.set_title && dialog.set_title(__("Choose a file"));
		}
	};
}

const ALWAYS_LOCKED_FIELDS = [
	"visitor_type",
	"email_id",
	"visit_date",
	"expected_checkin",
	"expected_checkout",
	"person_to_visit",
];
const CONDITIONALLY_LOCKED_FIELDS = ["purpose_of_visit"];
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

// Choosing a nationality moves the phone's country code with it, as the desk
// form does (visitor_pass.js sync_mobile_country_with_nationality). A foreign
// visitor otherwise typed their number under +91 and it was refused. Bound from
// attachMobileValidator, which runs for both the invitation and direct links.
function bindNationalityToPhone() {
	const $nationality = frappe.web_form.get_input("custom_nationality");
	if (!$nationality || !$nationality.length || $nationality.data("vmPhoneSync")) {
		return;
	}
	$nationality.data("vmPhoneSync", true);
	$nationality.on("change awesomplete-selectcomplete", () => {
		setTimeout(() => {
			const country = getFieldValue("custom_nationality");
			const phone = frappe.web_form.fields_dict?.mobile_number;
			if (
				country &&
				phone &&
				phone.country_codes &&
				phone.country_codes[country] &&
				phone.country_code_picker &&
				phone.$isd &&
				phone.$isd.length
			) {
				phone.country_code_picker.on_change(country, false);
			}
		}, 0);
	});
}

function attachMobileValidator() {
	const field = frappe.web_form?.fields_dict?.mobile_number;
	if (!field) {
		return;
	}
	// Do this before anything else touches the control — core throws on its own
	// first render, whether or not we are prefilling a value.
	guardPhoneControl(field);
	bindNationalityToPhone();
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
			__(
				"Enter 10 digits (e.g. 9876543210) or +country code + number (e.g. +91 9876543210)."
			)
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
	// Either a bare data URI or "filename,data:..." (readAttachmentsInBrowser).
	const dataStart = value.indexOf("data:");
	if (dataStart < 0) return null;
	const bytes = approxBase64Bytes(value.slice(dataStart));
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

// What the visitor sees once the form has gone through: that it did, the
// reference to quote if they ring reception, and what happens next.
//
// On Frappe 15 handle_success() hides .web-form-container and shows its sibling
// .success-page (web_form.js). This panel used to be prepended to the container
// — the element that had just been hidden — so a visitor at home was left with
// the one generic line and no reference at all.
function renderSuccessPanel(reference) {
	const ref = reference ? escapeHtml(reference) : "";
	const html = `
		<div class="vm-success-panel" role="status" aria-live="polite">
			<div style="font-size:1.1rem; font-weight:700; color:var(--vr-ok);">
				${__("Pre-registration submitted")}
			</div>
			${
				ref
					? `<div class="vm-success-ref-label">${__("Your reference")}</div>
					   <div class="vm-success-ref">${ref}</div>
					   <div style="font-size:0.85rem; color:var(--vr-muted); margin-top:6px;">
						${__("Keep this reference. Quote it if you need to contact your host or the reception.")}
					   </div>`
					: ""
			}
			<div class="vm-success-next">
				<strong>${__("What happens next")}</strong>
				<ol>
					<li>${__("Your request is reviewed by the person you are visiting.")}</li>
					<li>${__("Once it is approved, you get an email with your visitor pass and a QR code.")}</li>
					<li>${__("Show that email, and the ID you registered with, at the gate.")}</li>
				</ol>
			</div>
		</div>
	`;

	let $page = $(".success-page").first();
	if (!$page.length) {
		// A Frappe that lays the page out differently: put the confirmation where
		// it is certain to be visible rather than nowhere.
		$page = $('<div class="success-page"></div>').insertAfter(
			$(".web-form-container").first()
		);
	}
	$page.removeClass("hide").addClass("vm-has-panel").show();
	$page.find(".vm-success-panel").remove();
	const $body = $page.find(".success-body").first();
	if ($body.length) {
		$body.after(html);
	} else {
		$page.append(html);
	}
	window.scrollTo({ top: 0, behavior: "smooth" });
}

// ── privacy notice ──────────────────────────────────────────────────────────
// get_context renders the notice and its tick box (hidden) when the site asks
// for one. It belongs right above the Submit button, which only exists once
// the form has been drawn, so it is moved there. The tick is checked again by
// the server, which is also what records it on the pass.
function placeConsentBlock() {
	const $block = $("#vm-consent");
	if (!$block.length) {
		return;
	}
	const $footer = $(".web-form .web-form-footer").first();
	if ($footer.length && !$block.data("vmPlaced")) {
		$block.data("vmPlaced", true).insertBefore($footer);
		$block.find("#vm-consent-check").on("change", function () {
			if (this.checked) {
				$block.removeClass("vm-consent-missing");
			}
		});
	}
	$block.removeClass("vm-form-hidden");
}

function consentGiven() {
	const $block = $("#vm-consent");
	if (!$block.length) {
		return true; // the site does not ask for one
	}
	placeConsentBlock();
	if ($block.find("#vm-consent-check").prop("checked")) {
		return true;
	}
	$block
		.addClass("vm-consent-missing")
		.find(".vm-consent-error")
		.text(__("Please tick the box to confirm you have read the privacy notice."));
	$block.get(0).scrollIntoView({ behavior: "smooth", block: "center" });
	return false;
}

// ── a draft re-opened with the invitation link ──────────────────────────────
// The server never sends the ID number back, only its masked form, and never
// a file's address, only whether one is on file. So the number box stays empty
// with a note of what is held, and what is already held is no longer asked for.
function showStoredIdentity(values) {
	const masked = values && values.id_proof_number_masked;
	const idField = frappe.web_form?.fields_dict?.id_proof_number_entry;
	if (masked && idField) {
		frappe.web_form.set_df_property("id_proof_number_entry", "reqd", 0);
		const $wrap = $(idField.wrapper);
		$wrap.find(".vm-id-on-file").remove();
		$wrap
			.find(".control-input-wrapper")
			.first()
			.append(
				`<div class="vm-id-on-file">${__(
					"On file: {0}. Leave this blank to keep it, or type the number again to change it.",
					[`<strong>${escapeHtml(masked)}</strong>`]
				)}</div>`
			);
		clearStaleError(idField);
		$wrap.removeClass("has-error");

		// A different kind of ID needs its own number.
		const $type = frappe.web_form.get_input("id_proof_type");
		if ($type && $type.length && !$type.data("vmIdTypeWatch")) {
			$type.data("vmIdTypeWatch", true);
			$type.on("change awesomplete-selectcomplete", () => {
				setTimeout(() => {
					const changed = getFieldValue("id_proof_type") !== values.id_proof_type;
					frappe.web_form.set_df_property(
						"id_proof_number_entry",
						"reqd",
						changed ? 1 : 0
					);
				}, 0);
			});
		}
	}

	PORTAL_ATTACH_FIELDS.forEach((fieldname) => {
		const field = frappe.web_form?.fields_dict?.[fieldname];
		if (!field || !values || !values[`${fieldname}_on_file`]) {
			return;
		}
		frappe.web_form.set_df_property(fieldname, "reqd", 0);
		field.df.mandatory_depends_on = null;
		const $wrap = $(field.wrapper);
		$wrap.removeClass("has-error");
		if (!$wrap.find(".vm-id-on-file").length) {
			$wrap
				.find(".control-input-wrapper")
				.first()
				.append(
					`<div class="vm-id-on-file">${__(
						"Already uploaded. Attach a file only if you want to replace it."
					)}</div>`
				);
		}
	});
}

// A dead invitation link, or a site that takes no walk-ins: get_context shows
// the reason and sends no fields. Take the empty card and its Submit button
// off the page as well.
function closeForm() {
	$(".web-form").addClass("vm-form-hidden");
	$(".vm-custom-block").addClass("vm-form-hidden");
	setSubmitDisabled(true);
}

function setFormVisibility(visible) {
	$(
		".web-form .form-column, .web-form .section-body, .web-form .web-form-footer, .vm-custom-block"
	).toggleClass("vm-form-hidden", !visible);
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
			<textarea rows="3" class="form-control vm-item-name" placeholder="${__(
				"e.g. Dell laptop, USB drive, toolkit"
			)}">${escapeHtml(item.item_name || "")}</textarea>
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
				${__(
					"Will you be carrying any laptops, storage devices, tools, or similar items? List them below so security can verify them at the gate."
				)}
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
//
// Three things, all on this one control object of this form:
//
// 1. Calls made from now on (the wrapper below).
// 2. The call that is ALREADY RUNNING. Core renders the form before this script
//    is run (web_form.js make(): super.make() first, the client script after),
//    and that first render calls set_formatted_input() while make_input() is
//    still waiting for the country list. Both wait on their own request; when
//    set_formatted_input's answer comes back first — a slow first visit on
//    mobile data, nothing in the browser's storage yet — it carries on to
//    `this.$isd.text()` with no `$isd`, and the visitor's page logs
//    "Cannot read properties of undefined (reading 'text')". That call cannot be
//    wrapped any more, so it is given something harmless to read: an empty
//    jQuery set, whose text() is "". make_input() replaces it with the real
//    element when it gets there (setup_country_code_picker), and everything in
//    this file that asks "is the selector built?" asks for `$isd.length`.
// 3. set_default_country(), which refresh() calls and which uses the country
//    picker as soon as the country list is known — which the second request can
//    make true before the picker exists. make_input() calls it again itself
//    once the picker is there, so skipping the early call loses nothing.
function guardPhoneControl(field) {
	if (!field || field._vmPhoneGuarded || typeof field.set_formatted_input !== "function") {
		return;
	}
	field._vmPhoneGuarded = true;
	if (!field.$isd) {
		field.$isd = $();
	}
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
	if (typeof field.set_default_country === "function") {
		const set_default_country = field.set_default_country.bind(field);
		field.set_default_country = function () {
			if (!this.country_code_picker) {
				return;
			}
			return set_default_country();
		};
	}
}

// Every Phone field of this form, as soon as this script runs — which is right
// after core's first render and before any of its pending requests can answer.
function guardPhoneControls() {
	const fields = frappe.web_form?.fields_dict || {};
	Object.keys(fields).forEach((fieldname) => {
		if (fields[fieldname]?.df?.fieldtype === "Phone") {
			guardPhoneControl(fields[fieldname]);
		}
	});
}

// A Phone control draws its own country-code selector beside the input, so a
// value that ALREADY starts with a country code would be shown twice
// ("+91 +91-…"), and the visitor could not tell which part was real.
//
// The host stores the number in full ("+91 9876543210") and that is correct;
// only the on-screen split is wrong. So strip a leading "+<code>" off the value
// before handing it to the control and let the selector own the prefix. If the
// control has no selector (some renders), the value is passed through whole so
// nothing is lost.
function splitIsdFromNumber(field, value) {
	const raw = (value == null ? "" : String(value)).trim();
	if (!raw.startsWith("+") || !field?.$isd?.length) {
		return raw;
	}
	// "+91 98765 43210" / "+91-9876543210" -> code "91", rest "9876543210"
	const m = raw.match(/^\+(\d{1,4})[\s-]*(.*)$/);
	if (!m) {
		return raw;
	}
	const [, code, rest] = m;
	if (!rest.replace(/\D/g, "")) {
		return raw; // nothing but a code — leave it alone rather than blanking it
	}
	try {
		field.$isd.val("+" + code).trigger("change");
	} catch (e) {
		return raw; // selector would not take it; better a doubled prefix than a lost number
	}
	return rest.trim();
}

async function ensurePhoneValueApplied(field, value) {
	guardPhoneControl(field);
	for (let attempt = 0; attempt < 25; attempt++) {
		if (field.$isd && field.$isd.length && field.$input) {
			break;
		}
		await new Promise((resolve) => setTimeout(resolve, 100));
	}
	setFieldInputDirectly(field, splitIsdFromNumber(field, value));
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
	readAttachmentsInBrowser();
	unlockDirectAccessFields();
	bindGenericFormHandlers();
	attachHospitalityHandlers();
	startHospitalityWatcher();
	renderVisitorItems();
	applyVisitorTypeSections(getFieldValue("visitor_type"));
	attachMobileValidator();
	placeConsentBlock();
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

		// Show the host by name where there is one. The field itself holds an
		// internal record id, which means nothing to a visitor; the stored value
		// is unchanged, only what is displayed.
		const display =
			fieldname === "person_to_visit" && values.person_to_visit_display
				? values.person_to_visit_display
				: values[fieldname];

		syncVisibleLockedField(fieldname, display);
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
		LOCKED_FIELDS.forEach((fieldname) =>
			syncVisibleLockedField(fieldname, values?.[fieldname])
		);
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
	return invitationContextState.values?.[fieldname] ?? window.vmInvitationValues?.[fieldname];
}

function isMissingRequiredValue(value, field) {
	if (value === null || value === undefined) {
		return true;
	}

	if (field?.df?.fieldtype === "Text Editor") {
		return !String(value)
			.replace(/<[^>]*>/g, "")
			.trim();
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

// Which ID types and visitor types are offered is decided on the server
// (visitor_pre_registration_form.py _limit_public_pickers): on Frappe 15 a web
// form's pickers arrive as complete option lists inside the page, so a
// `get_query` set here — how inactive ID types used to be filtered — never ran.

async function handleInvitationAfterLoad() {
	if (invitationContextState.afterLoadTriggered) {
		return;
	}

	if (window.vmFormClosed) {
		// Nothing to wait for: the page has no fields.
		invitationContextState.afterLoadTriggered = true;
		$(".discard-btn").hide();
		closeForm();
		return;
	}

	if (!areInvitationFieldsReady()) {
		setTimeout(() => handleInvitationAfterLoad(), 100);
		return;
	}

	invitationContextState.afterLoadTriggered = true;
	readAttachmentsInBrowser();

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
			closeForm();
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
		showStoredIdentity(context.values || {});
		placeConsentBlock();
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
			frappe.msgprint(
				__(
					"Invitation details are still loading or invalid. Reopen the invitation link and try again."
				)
			);
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
			if (
				invitationValue !== undefined &&
				invitationValue !== null &&
				invitationValue !== ""
			) {
				docValues[fieldname] = invitationValue;
			}
		});

		if (!validateRequiredFieldsForSave(docValues)) {
			return false;
		}

		const mobileForCheck = docValues.mobile_number || frappe.web_form.doc?.mobile_number || "";
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
			checkAttachmentSize("visitor_photo", "Visitor Photo") ||
			checkAttachmentSize("custom_visa_copy", "Visa Copy");
		if (sizeIssue) {
			frappe.msgprint({
				title: __("File too large"),
				message: sizeIssue,
				indicator: "orange",
			});
			return false;
		}

		// Last, so that the visitor is sent to the tick box only when it is the
		// one thing left to do.
		if (!consentGiven()) {
			return false;
		}

		Object.assign(this.doc, docValues);

		// The tick box, when the site shows one. The server decides whether it was
		// needed and stamps the time itself; nothing else about consent is sent.
		this.doc.consent_given = $("#vm-consent-check").prop("checked") ? 1 : 0;
		this.doc.visitor_items = collectVisitorItems();
		this.doc.doctype = this.doc_type;
		this.doc.web_form_name = this.name;
		this.doc.invitation_token = getInvitationToken();
		this.doc.entry_type = "New";
		this.doc.request_channel = "Portal";
		this.doc.submission_action = "submit";
		const targetState = getPortalSubmissionState(
			this.doc.visitor_type,
			this.doc.submission_action
		);
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
	// First, and on every pass: a form instance core swaps in later has fresh
	// controls (guardPhoneControl does nothing to one it has already seen).
	guardPhoneControls();
	setupInvitationHooks();
	readAttachmentsInBrowser();
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
