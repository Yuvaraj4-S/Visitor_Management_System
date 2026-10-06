// For license information, please see license.txt

// This form's Links to Employee (HRMS), searched through the app's own query
// (public/js/core_link_pickers.js).
const SECURITY_LOG_CORE_LINKS = {
	security_officer: () => {
		if (is_security_admin()) {
			return { status: "Active" };
		}
		// Security staff may only log on their own behalf.
		return { user_id: frappe.session.user, status: "Active" };
	},
	// Fetched from the pass; listed so a fetched value is not re-checked per pick.
	person_to_visit: {},
};

const GATE_API = "visitormanagement.visitor_management.api.visitor_gate";
const SECURITY_LOG_API = "visitormanagement.visitor_management.doctype.security_log.security_log";
const GATE_MOVEMENTS = ["Check-In", "Check-Out"];

// How long a press of the Record button stays valid for the save it starts.
const RECORD_INTENT_MS = 5000;

frappe.ui.form.on("Security Log", {
	setup(frm) {
		frm.add_fetch("visitor_pass", "visitor_full_name", "visitor_name");
		frm.add_fetch("visitor_pass", "badge_number", "badge_number");
		frm.add_fetch("visitor_pass", "visitor_photo", "visitor_photo");
		frm.add_fetch("visitor_pass", "id_proof_scan", "id_proof_scan");

		// A deactivated gate has no business being offered at the gate — the
		// server also rejects one on save (security_log.py before_save), since
		// this filter is only a picker convenience, not enforcement.
		frm.set_query("gate_name", () => {
			return { filters: { is_active: 1 } };
		});
	},

	onload(frm) {
		vms_setup_core_link_pickers(frm, SECURITY_LOG_CORE_LINKS);
		// `frm` is reused for every Security Log opened in this session.
		frm.__gate_record_intent = 0;
		frm.__gate_recording = false;
		frm.__gate_refused = false;
		load_gate_policy(frm);
		if (frm.is_new()) {
			// A log opened from visitor_gate.scan_qr_checkin arrives with
			// ?qr_code_scanned=1 next to ?visitor_pass=… (Frappe's get_new_doc
			// copies route options onto the new doc before onload runs). Bind that
			// flag to the pass it was scanned for, so the visitor_pass handler can
			// keep it through the load-time link trigger but drop it if the
			// officer then switches to a different pass. `frm` is reused across
			// docs of this doctype, so always reset the binding here.
			frm.__qr_scanned_pass =
				cint(frm.doc.qr_code_scanned) && frm.doc.visitor_pass
					? frm.doc.visitor_pass
					: null;
		}
		if (
			frm.is_new() &&
			!frm.doc.security_officer &&
			!["Administrator", "Guest"].includes(frappe.session.user)
		) {
			// Not frappe.db.get_value: that needs READ on Employee, and Security
			// holds only SELECT there, so a guard without the Employee role got
			// "No permission for Employee" on every new log.
			frappe.call({
				method: "visitormanagement.visitor_management.link_details.get_own_employee",
				callback: (r) => {
					const emp = r && r.message;
					if (emp && !frm.doc.security_officer) frm.set_value("security_officer", emp);
				},
			});
		}
		if (!is_security_admin()) {
			frm.set_df_property("security_officer", "read_only", 1);
		}
	},

	refresh(frm) {
		vms_setup_core_link_pickers(frm, SECURITY_LOG_CORE_LINKS);
		setup_record_action(frm);
		apply_security_log_ui(frm);

		if (frm.doc.visitor_pass) {
			frm.add_custom_button(
				__("Print Badge"),
				() => frm.trigger("print_visitor_badge"),
				__("Actions")
			);
		}

		if (frm.is_new() && !frm.doc.visitor_pass) {
			frm.add_custom_button(
				__("Approved VIP Queue"),
				() => open_vip_queue(frm),
				__("Actions")
			);
		}

		// Lock the form once the gate event has been recorded — it is an audit record.
		// Admins (System Manager) can still amend; everyone else gets a read-only form.
		// The banner that says so is drawn by render_gate_intro.
		if (is_locked_log(frm)) {
			frm.disable_form();
		}
	},

	validate(frm) {
		// A new Security Log is the gate event: inserting it checks the visitor in
		// or out and locks the record. Frappe's Attach control saves the form as
		// soon as an upload finishes (form/controls/attach.js on_upload_complete,
		// and again when an attachment is cleared), so choosing the gate photo —
		// usually the first thing an officer does — recorded the check-in before
		// the identity boxes were ticked, or failed with "Items Not Verified".
		//
		// Only the Record button may save a new log. Any other save is dropped
		// here, without a message: the photo is already in the unsaved form and
		// its file is moved onto the record when the event is recorded.
		if (!frm.is_new()) return;
		const pressed = frm.__gate_record_intent || 0;
		frm.__gate_record_intent = 0;
		if (Date.now() - pressed > RECORD_INTENT_MS) {
			frappe.validated = false;
		}
	},

	security_officer(frm) {
		// Show the officer by name. The Link itself shows an Employee ID, and the
		// officer cannot read Employee; the server stores the name on save.
		const officer = frm.doc.security_officer;
		if (!officer) {
			frm.set_value("security_officer_name", "");
			return;
		}
		frappe.call({
			method: "visitormanagement.visitor_management.link_details.get_link_details",
			args: {
				doctype: "Employee",
				name: officer,
				reference_doctype: "Security Log",
				fieldname: "security_officer",
			},
			callback: (r) => {
				if (frm.doc.security_officer !== officer) return;
				frm.set_value("security_officer_name", (r.message || {}).employee_name || "");
			},
		});
	},

	event_type(frm) {
		if (!frm.doc.verification_started_on) {
			frm.set_value("verification_started_on", frappe.datetime.now_datetime());
		}
		if (!frm.doc.visited_area && frm.doc.gate_name) {
			frm.set_value("visited_area", frm.doc.gate_name);
		}
		if (frm.is_new() && frm.save_disabled) {
			// Keep the Record button's label in step with the event type.
			setup_record_action(frm);
		}
		apply_security_log_ui(frm);
	},

	gate_name(frm) {
		if (!frm.doc.visited_area) {
			frm.set_value("visited_area", frm.doc.gate_name);
		}
		apply_security_log_ui(frm);
	},

	symptoms_flag(frm) {
		apply_security_log_ui(frm);
	},

	photo_at_gate(frm) {
		apply_security_log_ui(frm);
	},

	id_proof_match(frm) {
		apply_security_log_ui(frm);
	},

	pass_photo_match(frm) {
		apply_security_log_ui(frm);
	},

	all_items_confirmed(frm) {
		// Derived server-side from items_verification rows. If a user manages
		// to toggle it (Frappe's Check read_only is unreliable), snap it back
		// to the computed value so the flag stays trustworthy.
		recompute_all_items_confirmed(frm);
		render_items_progress_summary(frm);
	},

	after_save(frm) {
		// Only for the save that recorded the event, not for a System Manager's
		// later correction of the same log.
		if (!frm.__gate_recording) return;
		frm.__gate_recording = false;

		if (frm.doc.event_type === "Check-In" && frm.doc.visitor_pass) {
			frappe.show_alert({
				message: __("Check-In recorded. Opening badge for printing..."),
				indicator: "green",
			});

			setTimeout(() => {
				frm.trigger("print_visitor_badge");
			}, 1000);
		} else if (frm.doc.event_type === "Check-Out") {
			frappe.show_alert({ message: __("Check-Out recorded."), indicator: "green" });
		}
	},

	print_visitor_badge(frm) {
		if (!frm.doc.visitor_pass) {
			frappe.msgprint(__("Please select a Visitor Pass first."));
			return;
		}

		if (frm.doc.event_type !== "Check-In") {
			open_badge(frm);
			return;
		}

		// A recorded check-in already met everything the site requires (the server
		// refuses to record it otherwise), so its badge can be printed as it is.
		if (!frm.is_new() && !frm.is_dirty()) {
			open_badge(frm);
			return;
		}

		// Not recorded yet: show what is still to do, from what this site actually
		// requires. The checklist used to hardcode all three, so on a site that
		// requires none of them the officer was told to finish steps that were not
		// needed.
		load_gate_policy(frm).then(() => {
			const steps = gate_steps(frm);
			steps.push({ ok: false, label: __("Record the check-in") });
			show_gate_checklist(__("Finish these steps to print the badge"), steps);
		});
	},

	qr_code_value(frm) {
		frappe.require("/assets/visitormanagement/js/libs/html5-qrcode.min.js", () => {
			if (typeof Html5Qrcode === "undefined") {
				frappe.msgprint({
					title: __("Error"),
					message: __(
						"QR scanner library did not load. Refresh the page and try again."
					),
					indicator: "red",
				});
				return;
			}

			const scanner_dialog = new frappe.ui.Dialog({
				title: __("Scan QR Code"),
				fields: [
					{
						fieldname: "qr_scanner_html",
						fieldtype: "HTML",
					},
				],
				primary_action_label: __("Stop Scanner"),
				primary_action() {
					scanner_dialog.hide();
				},
			});

			scanner_dialog.show();

			const scanner_id = "qr-reader";
			const $container = scanner_dialog.get_field("qr_scanner_html").$wrapper;
			$container.html(`
				<div id="${scanner_id}" style="width: 100%; min-height: 300px; border: 1px solid #ddd; border-radius: 8px; background: #000; position: relative;">
					<div style="position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); color: white; font-family: sans-serif;">
						${__("Initializing camera...")}
					</div>
				</div>
				<div id="qr-reader-results" style="margin-top: 10px; text-align: center; font-weight: bold; color: #555;"></div>
			`);

			let html5QrCode;

			function stop_scanner() {
				if (html5QrCode && html5QrCode.isScanning) {
					html5QrCode.stop().catch((err) => {
						console.warn("Failed to stop QR scanner", err);
					});
				}
			}

			setTimeout(() => {
				try {
					html5QrCode = new Html5Qrcode(scanner_id);

					const qrCodeSuccessCallback = (decodedText) => {
						let visitor_pass = decodedText;
						if (decodedText.includes("|") && decodedText.includes(":")) {
							for (const part of decodedText.split("|")) {
								if (part.startsWith("PASS:")) {
									visitor_pass = part.split(":")[1].trim();
									break;
								}
							}
						}

						frm.__scanned = true;
						// Bind before setting the pass, so the visitor_pass handler
						// sees this pass as the scanned one and does not reset the flag.
						frm.__qr_scanned_pass = visitor_pass;
						frm.set_value("visitor_pass", visitor_pass);
						frm.set_value("qr_code_scanned", 1);
						frappe.show_alert({
							message: __("QR Code scanned: {0}", [visitor_pass]),
							indicator: "green",
						});
						scanner_dialog.hide();
					};

					const config = {
						fps: 10,
						qrbox: { width: 250, height: 250 },
						aspectRatio: 1.0,
					};

					html5QrCode
						.start({ facingMode: "environment" }, config, qrCodeSuccessCallback)
						.catch(() =>
							html5QrCode.start(
								{ facingMode: "user" },
								config,
								qrCodeSuccessCallback
							)
						)
						.catch((err) => {
							frappe.msgprint({
								title: __("Camera Error"),
								message: __("Could not start camera. Error: {0}", [err]),
								indicator: "red",
							});
							scanner_dialog.hide();
						});
				} catch (e) {
					frappe.msgprint(__("Failed to initialize scanner: {0}", [e]));
				}
			}, 500);

			scanner_dialog.on_hide = () => stop_scanner();
		});
	},

	visitor_pass(frm) {
		// "QR Code Scanned" attests that THIS pass's code was read. It survives
		// the load-time link trigger for the pass it came with (route from
		// scan_qr_checkin, or the in-form scanner), but a pass picked or cleared
		// by hand afterwards was not scanned, so the flag must not carry over.
		if (
			frm.is_new() &&
			cint(frm.doc.qr_code_scanned) &&
			frm.doc.visitor_pass !== frm.__qr_scanned_pass
		) {
			frm.set_value("qr_code_scanned", 0);
		}

		if (!frm.doc.visitor_pass) {
			delete frm.__visitor_type;
			delete frm.__visitor_type_layout;
			apply_security_log_ui(frm);
			return;
		}

		frm.__gate_refused = false;

		// A recorded log's pass cannot change; there is nothing to look up.
		if (!frm.is_new()) {
			apply_security_log_ui(frm);
			return;
		}

		if (!frm.doc.verification_started_on) {
			frm.set_value("verification_started_on", frappe.datetime.now_datetime());
		}

		// Everything the form shows about the pass comes from one gate endpoint:
		// the ID number already masked, the host by name, the declared items, the
		// gate, and the movement the pass allows right now. The form used to read
		// the Visitor Pass itself, which put the full ID number in the browser
		// and left the "may this visitor enter?" decision to this script.
		const requested = frm.doc.visitor_pass;
		frappe.call({
			method: `${GATE_API}.get_gate_context`,
			args: { visitor_pass: requested },
			callback: (r) => {
				const context = r && r.message;
				// The officer may have picked another pass, or cleared it, meanwhile.
				if (!context || frm.doc.visitor_pass !== requested) {
					apply_security_log_ui(frm);
					return;
				}
				apply_gate_context(frm, context);
			},
			error: () => {
				// Not a pass this officer can use at the gate; the server has said
				// so. Leave nothing half-filled on the form.
				if (frm.doc.visitor_pass === requested) {
					frm.set_value("visitor_pass", "");
				}
			},
		});
	},

	capture_photo(frm) {
		const capture_dialog = new frappe.ui.Dialog({
			title: __("Capture Photo"),
			fields: [
				{
					fieldname: "camera_html",
					fieldtype: "HTML",
				},
			],
			primary_action_label: __("Capture"),
			primary_action() {
				const video = dialog_video(capture_dialog);
				if (!video || !video.videoWidth || !video.videoHeight) {
					frappe.msgprint(
						__("Camera is still loading. Wait a moment and capture again.")
					);
					return;
				}
				const canvas = document.createElement("canvas");
				canvas.width = video.videoWidth;
				canvas.height = video.videoHeight;
				const context = canvas.getContext("2d");
				context.drawImage(video, 0, 0, canvas.width, canvas.height);

				canvas.toBlob((blob) => {
					const file_name = `gate_photo_${frappe.datetime
						.now_datetime()
						.replace(/[: -]/g, "_")}.png`;
					if (!blob) {
						frappe.msgprint(__("Could not capture gate photo. Please try again."));
						return;
					}

					const file = new File([blob], file_name, { type: "image/png" });

					upload_captured_image({
						file,
						doctype: frm.doctype,
						docname: frm.doc.name,
						fieldname: "photo_at_gate",
					})
						.then((file_doc) => {
							frm.set_value("photo_at_gate", file_doc.file_url).then(() => {
								frm.refresh_field("photo_at_gate");
								apply_security_log_ui(frm);
								frappe.show_alert({
									message: __("Gate photo captured."),
									indicator: "green",
								});
								capture_dialog.hide();
							});
						})
						.catch(() => {
							frappe.msgprint(__("Could not upload gate photo."));
						});
				}, "image/png");
			},
		});

		capture_dialog.show();
		capture_dialog.get_primary_btn().prop("disabled", true);

		const video_id = "capture-video";
		capture_dialog.get_field("camera_html").$wrapper.html(`
			<div style="width: 100%; background: #000; border-radius: 8px; overflow: hidden;">
				<video id="${video_id}" width="100%" autoplay playsinline></video>
			</div>
		`);

		if (!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia)) {
			frappe.msgprint(__("Camera is not supported on this browser."));
			capture_dialog.hide();
			return;
		}

		navigator.mediaDevices
			.getUserMedia({ video: { facingMode: "environment" } })
			.then((stream) => {
				const video = dialog_video(capture_dialog);
				if (!video) {
					stream.getTracks().forEach((track) => track.stop());
					frappe.msgprint(__("Video element not found. Please try again."));
					return;
				}

				video.srcObject = stream;
				video.onloadedmetadata = () => {
					capture_dialog.get_primary_btn().prop("disabled", false);
				};
				capture_dialog.on_hide = () => {
					stream.getTracks().forEach((track) => track.stop());
				};
			})
			.catch((err) => {
				frappe.msgprint(__("Error accessing camera: {0}", [err]));
				capture_dialog.hide();
			});
	},
});

// The camera preview lives inside its own dialog. Looking it up by a page-wide id
// failed when the dialog was opened automatically right after a QR scan: the
// scanner dialog was still closing, the new dialog's body was not in the page
// yet, and getElementById found nothing ("Video element not found") on every
// scan. The element exists on the dialog from the moment its HTML is set, so
// take it from there; srcObject set before it is attached still plays once shown.
function dialog_video(dialog) {
	return dialog.get_field("camera_html").$wrapper.find("video").get(0);
}

frappe.ui.form.on("Security Item Verify", {
	item_verified(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row) return;

		if (row.item_verified && !row.security_remarks) {
			frappe.model.set_value(cdt, cdn, "security_remarks", __("Verified at gate"));
		} else if (!row.item_verified && row.security_remarks === __("Verified at gate")) {
			frappe.model.set_value(
				cdt,
				cdn,
				"security_remarks",
				__("Pending security verification")
			);
		}

		recompute_all_items_confirmed(frm);
		render_items_progress_summary(frm);
	},

	capture_item_image(frm, cdt, cdn) {
		const capture_dialog = new frappe.ui.Dialog({
			title: __("Capture Item Photo"),
			fields: [
				{
					fieldname: "camera_html",
					fieldtype: "HTML",
				},
			],
			primary_action_label: __("Capture"),
			primary_action() {
				const video = dialog_video(capture_dialog);
				if (!video || !video.videoWidth || !video.videoHeight) {
					frappe.msgprint(
						__("Camera is still loading. Wait a moment and capture again.")
					);
					return;
				}
				const canvas = document.createElement("canvas");
				canvas.width = video.videoWidth;
				canvas.height = video.videoHeight;
				const context = canvas.getContext("2d");
				context.drawImage(video, 0, 0, canvas.width, canvas.height);

				canvas.toBlob((blob) => {
					const file_name = `item_photo_${frappe.datetime
						.now_datetime()
						.replace(/[: -]/g, "_")}.png`;
					if (!blob) {
						frappe.msgprint(__("Could not capture item photo. Please try again."));
						return;
					}

					const file = new File([blob], file_name, { type: "image/png" });

					upload_captured_image({
						file,
						doctype: cdt,
						docname: cdn,
						fieldname: "item_image",
					})
						.then((file_doc) => {
							frappe.model.set_value(cdt, cdn, "item_image", file_doc.file_url);
							frappe.show_alert({
								message: __("Item photo captured and attached."),
								indicator: "green",
							});
							capture_dialog.hide();
						})
						.catch(() => {
							frappe.msgprint(__("Could not upload item photo."));
						});
				}, "image/png");
			},
		});

		capture_dialog.show();
		capture_dialog.get_primary_btn().prop("disabled", true);

		const video_id = "item-capture-video";
		capture_dialog.get_field("camera_html").$wrapper.html(`
			<div style="width: 100%; background: #000; border-radius: 8px; overflow: hidden;">
				<video id="${video_id}" width="100%" autoplay playsinline></video>
			</div>
		`);

		if (!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia)) {
			frappe.msgprint(__("Camera is not supported on this browser."));
			capture_dialog.hide();
			return;
		}

		navigator.mediaDevices
			.getUserMedia({ video: { facingMode: "environment" } })
			.then((stream) => {
				const video = dialog_video(capture_dialog);
				if (!video) {
					stream.getTracks().forEach((track) => track.stop());
					frappe.msgprint(__("Video element not found. Please try again."));
					return;
				}

				video.srcObject = stream;
				video.onloadedmetadata = () => {
					capture_dialog.get_primary_btn().prop("disabled", false);
				};
				capture_dialog.on_hide = () => {
					stream.getTracks().forEach((track) => track.stop());
				};
			})
			.catch((err) => {
				frappe.msgprint(__("Error accessing camera: {0}", [err]));
				capture_dialog.hide();
			});
	},
});

function upload_captured_image({ file, doctype, docname, fieldname }) {
	return new Promise((resolve, reject) => {
		const xhr = new XMLHttpRequest();
		const form_data = new FormData();

		form_data.append("file", file, file.name);
		// Gate and item photos are of a person and their belongings — private, like
		// the ID scan and visitor photo. They were uploaded public (is_private 0), so
		// anyone with the /files/ URL could open them without logging in.
		form_data.append("is_private", 1);
		form_data.append("doctype", doctype);
		form_data.append("docname", docname);
		form_data.append("fieldname", fieldname);

		xhr.open("POST", "/api/method/upload_file", true);
		xhr.setRequestHeader("Accept", "application/json");
		xhr.setRequestHeader("X-Frappe-CSRF-Token", frappe.csrf_token);

		xhr.onreadystatechange = () => {
			if (xhr.readyState !== XMLHttpRequest.DONE) {
				return;
			}

			if (xhr.status !== 200) {
				reject(xhr.responseText);
				return;
			}

			try {
				const response = JSON.parse(xhr.responseText);
				if (response.message && response.message.file_url) {
					resolve(response.message);
					return;
				}
			} catch (e) {
				console.error("Failed to parse uploaded image response", e);
			}

			reject(xhr.responseText);
		};

		xhr.onerror = () => reject(xhr.responseText);
		xhr.send(form_data);
	});
}

function is_security_admin() {
	// Only System Manager can override the security_officer field. HR Manager
	// has no server-side perm on Security Log, so they never reach this path.
	const roles = frappe.user_roles || [];
	return roles.includes("System Manager");
}

function is_locked_log(frm) {
	return !frm.is_new() && !is_security_admin();
}

// What this site requires before a Check-In / Check-Out may be recorded (VMS
// Settings). Read once per form load; the server enforces the same switches
// when the event is recorded, so this only decides what the form asks for.
function load_gate_policy(frm) {
	if (frm.__gate_policy_request) {
		return frm.__gate_policy_request;
	}
	frm.__gate_policy_request = frappe
		.xcall(`${SECURITY_LOG_API}.get_gate_policy`)
		.then((policy) => {
			frm.__gate_policy = policy || {};
			apply_security_log_ui(frm);
			return frm.__gate_policy;
		})
		.catch(() => {
			// Without the policy the form asks for nothing extra; the server still
			// refuses an event that does not meet the site's requirements.
			frm.__gate_policy = frm.__gate_policy || {};
			return frm.__gate_policy;
		})
		.finally(() => {
			// Read again on the next form load: an administrator may change it.
			frm.__gate_policy_request = null;
		});
	return frm.__gate_policy_request;
}

function gate_record_label(frm) {
	if (frm.doc.event_type === "Check-In") return __("Record Check-In");
	if (frm.doc.event_type === "Check-Out") return __("Record Check-Out");
	return __("Record Gate Event");
}

// A new Security Log is recorded with its own button, never with "Save".
//
// Saving a new log IS the gate event, so it must happen once, on purpose. The
// standard Save is switched off for the unsaved form and the primary button
// becomes "Record Check-In" / "Record Check-Out"; Ctrl+S presses the same
// button. (Frappe re-creates its Save button every time the form changes
// unless saving is disabled — form/toolbar.js add_update_button_on_dirty — so
// the button is replaced this way rather than relabelled.) A saved log keeps
// the standard Save, which only a System Manager can use.
function setup_record_action(frm) {
	if (!frm.is_new()) return;
	frm.disable_save(true);
	frm.page.set_primary_action(gate_record_label(frm), (btn) => record_gate_event(frm, btn));
}

function record_gate_event(frm, btn) {
	const pending = gate_steps(frm).filter((step) => !step.ok);
	if (pending.length) {
		show_gate_checklist(__("Finish these steps before recording"), gate_steps(frm));
		return;
	}
	frm.__gate_record_intent = Date.now();
	frm.__gate_recording = true;
	// The button is passed on so Frappe re-enables it if the save is refused.
	frm.save("Save", null, btn);
}

// The steps this site requires for the event on the form, each marked done or
// not. Empty for events that are not a movement.
function gate_steps(frm) {
	const policy = frm.__gate_policy || {};
	const steps = [];
	if (!GATE_MOVEMENTS.includes(frm.doc.event_type)) return steps;

	if (policy.qr_scan_required) {
		steps.push({
			ok: Boolean(cint(frm.doc.qr_code_scanned)),
			label: __("Scan the visitor's QR code"),
		});
	}
	if (policy.photo_required) {
		steps.push({
			ok: Boolean(frm.doc.photo_at_gate),
			label: __("Capture or attach the live gate photo"),
		});
	}
	if (policy.identity_match_required) {
		steps.push({
			ok: Boolean(cint(frm.doc.id_proof_match)),
			label: __("Tick Matches ID Proof"),
		});
		steps.push({
			ok: Boolean(cint(frm.doc.pass_photo_match)),
			label: __("Tick Matches Pass Photo"),
		});
	}
	if (policy.items_verification_required && frm.doc.event_type === "Check-In") {
		const rows = frm.doc.items_verification || [];
		const left = rows.filter((row) => !row.item_verified).length;
		if (rows.length) {
			steps.push({
				ok: left === 0,
				label: left
					? __("Verify the declared items ({0} left)", [left])
					: __("Verify the declared items"),
			});
		}
	}
	return steps;
}

function show_gate_checklist(title, steps) {
	const items = steps
		.map(
			(step) =>
				`<li style="color:${step.ok ? "#059669" : "#b45309"};">` +
				`${step.ok ? "&#10003;" : "&#9888;"} ${frappe.utils.escape_html(step.label)}` +
				`</li>`
		)
		.join("");
	frappe.msgprint({
		title,
		message: `<ul style="padding-left:18px; margin:0;">${items}</ul>`,
		indicator: "orange",
	});
}

// Server text for the officer: escaped (it names the visitor), line breaks kept.
function gate_message_html(text) {
	return frappe.utils.escape_html(String(text || "")).replace(/\n/g, "<br>");
}

// Fill the form from visitor_gate.get_gate_context for the pass just picked.
function apply_gate_context(frm, context) {
	if (context.policy) {
		frm.__gate_policy = context.policy;
	}
	frm.__visitor_type = context.visitor_type;
	// The layout drives VIP-specific gate behaviour, so a site's own
	// executive type behaves like VIP without being named "VIP".
	frm.__visitor_type_layout = context.visitor_type_layout;

	// Set even when empty: a value left over from a pass picked a moment ago
	// must not be shown against this visitor.
	frm.set_value("visitor_photo", context.visitor_photo || "");
	frm.set_value("id_proof_scan", context.id_proof_scan || "");
	frm.set_value("visitor_name", context.visitor_full_name || "");
	frm.set_value("host_name", context.host_name || "");
	// Already masked by the server; the full number never reaches this form.
	frm.set_value("id_proof_number", context.id_proof_number_masked || "");

	// A pass that has no badge yet gets one when the check-in is recorded (the
	// server mints it and writes it back to this log). Picking a pass here used
	// to mint the badge at once, which also moved the pass to "Items Verified"
	// before anything had been verified — a change made by merely looking.
	frm.set_value("badge_number", context.badge_number || "");

	if (context.id_proof_type && !frm.doc.id_proof_type_verified) {
		frm.set_value("id_proof_type_verified", context.id_proof_type);
	}

	// One verification row per declared item, rebuilt for the pass now on the
	// form (rows of a previously picked pass must not stay).
	frm.clear_table("items_verification");
	(context.items || []).forEach((item) => {
		const row = frm.add_child("items_verification");
		row.item_name = item.item_name;
		row.item_category = item.item_category;
		row.item_type = item.item_category;
		row.quantity_declared = item.quantity;
		row.uom = item.unit_of_measure;
		row.serial__asset_number = item.serial_number;
		row.quantity_found = item.quantity;
		row.item_verified = 0;
		row.visitor_item_row_name = item.name;
	});
	frm.refresh_field("items_verification");

	if (!frm.doc.gate_name && context.default_gate) {
		frm.set_value("gate_name", context.default_gate);
	}

	frm.__gate_refused = Boolean(context.refusal);
	if (context.refusal) {
		// No movement is possible for this pass right now, and the server would
		// refuse it on recording. Say so at once. The pass stays on the form so
		// the officer can still record an Alert against it.
		frappe.msgprint({
			title: context.refusal.title || __("Pass Not Valid"),
			message: gate_message_html(context.refusal.message),
			indicator: "red",
		});
		if (GATE_MOVEMENTS.includes(frm.doc.event_type)) {
			frm.set_value("event_type", "");
		}
	} else {
		if (context.warning) {
			frappe.msgprint({
				title: context.warning.title || __("Warning"),
				message: gate_message_html(context.warning.message),
				indicator: "orange",
			});
		}
		const event_type = context.event_type;
		frm.set_value("event_type", event_type);
		if (!frm.doc.visited_area && frm.doc.gate_name) {
			frm.set_value("visited_area", frm.doc.gate_name);
		}
		if (event_type === "Check-In" && !frm.doc.check_in_date_time) {
			frm.set_value("check_in_date_time", frappe.datetime.now_datetime());
		} else if (event_type === "Check-Out" && !frm.doc.check_out_date_time) {
			frm.set_value("check_out_date_time", frappe.datetime.now_datetime());
		}
	}

	if (frm.__scanned) {
		if (GATE_MOVEMENTS.includes(frm.doc.event_type) && !frm.doc.photo_at_gate) {
			setTimeout(() => frm.trigger("capture_photo"), 500);
		}
		delete frm.__scanned;
	}

	apply_security_log_ui(frm);
}

function apply_security_log_ui(frm) {
	[
		"badge_number",
		"visitor_name",
		"visitor_company",
		"host_name",
		"security_officer_name",
		"id_proof_number",
		"id_proof_scan",
		"visitor_photo",
		"qr_code_scanned",
		"gate_auto_assigned",
		"mdceo_notified",
		"vip_meeting_room",
		"vip_protocol_notes",
		"all_items_confirmed",
		"verification_duration",
	].forEach((fieldname) => frm.set_df_property(fieldname, "read_only", 1));

	const policy = frm.__gate_policy || {};
	const has_pass = !!frm.doc.visitor_pass;
	const is_vip = frm.__visitor_type_layout === "VIP";
	const is_check_in = frm.doc.event_type === "Check-In";
	const is_check_out = frm.doc.event_type === "Check-Out";
	const is_movement = is_check_in || is_check_out;
	const has_items = !!(frm.doc.items_verification && frm.doc.items_verification.length);
	const show_items = is_check_in || has_items;
	const show_gate_photo = [
		"Check-In",
		"Check-Out",
		"Alert",
		"Gate Transfer",
		"Badge Collected",
	].includes(frm.doc.event_type);
	const show_exception_reason = !frm.is_new() && !!frm.doc.exception_reason;

	frm.toggle_display(
		"check_in_date_time",
		is_check_in || (!frm.is_new() && !!frm.doc.check_in_date_time)
	);
	frm.toggle_display(
		"check_out_date_time",
		is_check_out || (!frm.is_new() && !!frm.doc.check_out_date_time)
	);
	frm.toggle_reqd("check_in_date_time", is_check_in);
	frm.toggle_reqd("check_out_date_time", is_check_out);

	// The officer is shown by name. The Link holds an Employee ID, which only a
	// System Manager (who may record on an officer's behalf) needs to see.
	frm.toggle_display("security_officer", is_security_admin());

	frm.toggle_display("visitor_photo", false);
	frm.toggle_display("id_proof_scan", false);
	frm.toggle_display(
		["section_break_identity_comparison", "identity_comparison_html"],
		has_pass
	);
	frm.toggle_display(["photo_at_gate", "capture_photo"], show_gate_photo);
	frm.toggle_display(["id_proof_match", "pass_photo_match", "verification_notes"], is_movement);
	frm.toggle_display("exception_reason", show_exception_reason);
	frm.toggle_display(["section_break_items", "all_items_confirmed"], show_items && has_pass);

	// Required marks follow what the site requires (VMS Settings), and only while
	// the event is being recorded: a System Manager correcting an old log must
	// not be stopped by a requirement switched on since. They are a prompt, not
	// the control — the Record button lists what is missing and the server
	// refuses an event that does not meet the requirements.
	const recording = frm.is_new() && is_movement;
	frm.toggle_reqd("photo_at_gate", recording && !!policy.photo_required);
	frm.toggle_reqd("id_proof_match", recording && !!policy.identity_match_required);
	frm.toggle_reqd("pass_photo_match", recording && !!policy.identity_match_required);
	frm.toggle_reqd("exception_reason", false);
	frm.toggle_display(
		["mdceo_notified", "vip_meeting_room", "vip_protocol_notes"],
		has_pass && is_vip
	);

	render_identity_comparison(frm);
	render_items_progress_summary(frm);
	render_gate_intro(frm);
	apply_badge_visibility_sl(frm);
	lock_all_items_confirmed(frm);
}

function render_items_progress_summary(frm) {
	// Surface a compact "X / Y verified — Z discrepancies" line above the items
	// grid so a busy gate officer doesn't need to count rows.
	const field = frm.fields_dict && frm.fields_dict.items_verification;
	if (!field || !field.$wrapper) return;

	const rows = frm.doc.items_verification || [];
	const total = rows.length;
	const verified = rows.filter((r) => r.item_verified).length;
	const discrepancies = rows.filter((r) => r.discrepancy).length;

	field.$wrapper.find(".vm-items-progress").remove();
	if (!total) return;

	const all_verified = verified === total;
	// Only offer the bulk action when there is something left to tick and the
	// form can actually be written to (a submitted or locked log must not).
	const can_verify_all = !all_verified && !frm.doc.docstatus && !frm.is_dirty_disabled;
	const bg = all_verified ? "#d9f3e4" : discrepancies ? "#fde2e2" : "#fff4d6";
	const fg = all_verified ? "#0d6b3e" : discrepancies ? "#9b1c1c" : "#8d5d00";
	const icon = all_verified ? "✅" : discrepancies ? "⚠️" : "🟡";

	const summary = `
		<div class="vm-items-progress" style="display: flex; align-items: center; gap: 10px; padding: 8px 12px; margin: 0 0 8px 0; border-radius: 8px; background: ${bg}; color: ${fg}; font-size: 12px; font-weight: 600;">
			<span style="font-size: 14px;">${icon}</span>
			<span>${__("Items: {0} / {1} verified", [verified, total])}</span>
			${
				discrepancies
					? `<span style="margin-left: auto;">${__("{0} discrepancy", [discrepancies])}${
							discrepancies > 1 ? __("ies") : ""
					  }</span>`
					: ""
			}
			${
				can_verify_all
					? `<button type="button" class="btn btn-xs btn-default vm-verify-all" style="margin-left: ${
							discrepancies ? "10px" : "auto"
					  };">${__("Verify All")}</button>`
					: ""
			}
		</div>
	`;
	field.$wrapper.prepend(summary);

	// One click for the whole list. Check-in cannot save until every row is
	// ticked (_assert_items_verified), and the rows are generated one-per-item
	// from the visitor's typed list — so a contractor with a toolkit produced
	// eight checkboxes for a guard to click with a queue waiting. Ticking each
	// row individually is still there for a genuine item-by-item check; this is
	// for the ordinary case where the officer has looked at the bag and is
	// confirming the lot. Sets the same remark the per-row handler sets, so a
	// row verified this way is indistinguishable from one ticked by hand.
	if (can_verify_all) {
		field.$wrapper.find(".vm-verify-all").on("click", () => {
			(frm.doc.items_verification || []).forEach((row) => {
				if (row.item_verified) return;
				frappe.model.set_value(row.doctype, row.name, "item_verified", 1);
				if (!row.security_remarks) {
					frappe.model.set_value(
						row.doctype,
						row.name,
						"security_remarks",
						__("Verified at gate")
					);
				}
			});
			frm.refresh_field("items_verification");
			recompute_all_items_confirmed(frm);
			render_items_progress_summary(frm);
		});
	}
}

function lock_all_items_confirmed(frm) {
	// Frappe's read_only on Check fields doesn't always disable the click in
	// every theme/version. Lock the wrapper so re-renders from toggle_display
	// can't resurrect interactivity.
	const field = frm.fields_dict && frm.fields_dict.all_items_confirmed;
	if (!field || !field.$wrapper) return;

	const apply = () => {
		const $wrapper = field.$wrapper;
		const $input = $wrapper.find("input[type='checkbox']").first();
		if ($input && $input.length) $input.prop("disabled", true);
		$wrapper.css({ "pointer-events": "none", opacity: 0.85 });
	};

	apply();
	// Catch late renders triggered by toggle_display / section re-layout.
	setTimeout(apply, 50);
	setTimeout(apply, 250);
}

function recompute_all_items_confirmed(frm) {
	const rows = frm.doc.items_verification || [];
	const expected = rows.length === 0 || rows.every((r) => !!r.item_verified) ? 1 : 0;
	if (Number(frm.doc.all_items_confirmed || 0) !== expected) {
		frm.set_value("all_items_confirmed", expected);
	}
}

// Hide badge_number on Security Log when VMS Settings → enable_badge is off.
// The setting arrives with the gate policy (one request per form load); until
// it has, the field stays hidden.
function apply_badge_visibility_sl(frm) {
	const policy = frm.__gate_policy;
	const hide = !(policy && policy.badge_enabled);
	frm.set_df_property("badge_number", "hidden", hide ? 1 : 0);
	frm.toggle_display("badge_number", !hide);
}

function render_identity_comparison(frm) {
	const field = frm.get_field("identity_comparison_html");
	if (!field || !field.$wrapper) return;

	if (!frm.doc.visitor_pass) {
		field.$wrapper.empty();
		return;
	}

	const cards = [
		get_identity_card(
			__("ID Proof Scan"),
			frm.doc.id_proof_scan,
			__("Uploaded with the visitor pass")
		),
		get_identity_card(
			__("Pass Photo"),
			frm.doc.visitor_photo,
			__("Captured during pass creation")
		),
		get_identity_card(
			__("Gate Capture"),
			frm.doc.photo_at_gate,
			frm.doc.photo_at_gate
				? __("This live photo will appear on the badge after save")
				: __("Capture a live photo now at the gate")
		),
	];

	field.$wrapper.html(`
		<div style="border: 1px solid #dbe3ea; border-radius: 14px; padding: 16px; background: linear-gradient(180deg, #f8fafc 0%, #eef4f8 100%);">
			<div style="display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-bottom: 14px;">
				<div style="font-size: 13px; font-weight: 700; color: #102a43;">${__("Visual Match Review")}</div>
				${get_identity_status_html(frm)}
			</div>
			<div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px;">
				${cards.join("")}
			</div>
		</div>
	`);
}

function get_identity_card(title, imageUrl, caption) {
	return `
		<div style="background: #fff; border: 1px solid #dbe3ea; border-radius: 12px; padding: 12px;">
			<div style="font-size: 12px; font-weight: 700; color: #102a43; margin-bottom: 8px;">${title}</div>
			${
				imageUrl
					? `<img src="${frappe.utils.escape_html(
							String(imageUrl)
					  )}" alt="${title}" style="width: 100%; height: 148px; object-fit: cover; border-radius: 10px; border: 1px solid #dbe3ea; background: #f8fafc;">`
					: `<div style="height: 148px; border-radius: 10px; border: 1px dashed #b8c4d0; background: #f8fafc; display: flex; align-items: center; justify-content: center; color: #7b8794; font-size: 12px; text-align: center; padding: 12px;">${__(
							"No image available"
					  )}</div>`
			}
			<div style="margin-top: 8px; font-size: 11px; line-height: 1.4; color: #52606d;">${caption}</div>
		</div>
	`;
}

function get_identity_status_html(frm) {
	if (!frm.doc.photo_at_gate) {
		return `<div style="display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; background: #fff4d6; color: #8d5d00; font-size: 11px; font-weight: 700;">${__(
			"Awaiting gate capture"
		)}</div>`;
	}

	if (frm.doc.id_proof_match && frm.doc.pass_photo_match) {
		return `<div style="display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; background: #d9f3e4; color: #0d6b3e; font-size: 11px; font-weight: 700;">${__(
			"Verified for badge issue"
		)}</div>`;
	}

	return `<div style="display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; background: #e8f1fb; color: #1f4f82; font-size: 11px; font-weight: 700;">${__(
		"Review and confirm both matches"
	)}</div>`;
}

// The guidance banner at the top of the form.
//
// On Frappe 15 `frm.set_intro(text)` ADDS a message block and
// `frm.dashboard.clear_headline()` removes every block (form/layout.js
// show_message). The old code set the guidance and then cleared the headline,
// so the officer never saw it. Clear first, then set exactly one banner.
function render_gate_intro(frm) {
	frm.set_intro("");
	const [text, colour] = gate_intro(frm);
	if (text) {
		frm.set_intro(text, colour);
	}
}

function gate_intro(frm) {
	if (is_locked_log(frm)) {
		return [
			__("This Security Log is locked. Gate events are immutable once recorded."),
			"blue",
		];
	}

	if (!frm.is_new()) {
		// A System Manager looking at a recorded log.
		return [
			__(
				"This gate event is recorded. Correct a mis-keyed detail here if needed; the Visitor Pass and Event Type cannot be changed."
			),
			"blue",
		];
	}

	if (!frm.doc.visitor_pass) {
		return [
			__(
				"Scan the QR code, select a Visitor Pass, or use Approved VIP Queue for priority visitors."
			),
			"blue",
		];
	}

	if (frm.doc.event_type === "Check-In") {
		return [
			frm.__visitor_type_layout === "VIP"
				? __(
						"VIP priority check-in. Review protocol notes, confirm MD/CEO notification and capture the gate photo. Nothing is recorded until you press Record Check-In."
				  )
				: __(
						"Compare the ID proof, pass photo and live gate photo, and verify the declared items. Nothing is recorded until you press Record Check-In."
				  ),
			"green",
		];
	}

	if (frm.doc.event_type === "Check-Out") {
		return [
			__(
				"Confirm the visitor's identity. Nothing is recorded until you press Record Check-Out."
			),
			"orange",
		];
	}

	if (frm.doc.event_type === "Gate Transfer") {
		return [
			__(
				"Record the visitor's new area so contact tracing and emergency muster stay accurate."
			),
			"blue",
		];
	}

	if (!frm.doc.event_type) {
		if (!frm.__gate_refused) {
			return [__("Choose the Event Type to record."), "blue"];
		}
		return [
			__(
				"This pass cannot be checked in or out right now. Choose another Event Type to record an alert or a note against it."
			),
			"orange",
		];
	}

	return [
		__("Record the gate event with concise notes and supporting capture if needed."),
		"blue",
	];
}

function get_security_event_color(event_type) {
	if (event_type === "Check-In") return "green";
	if (event_type === "Check-Out") return "orange";
	if (event_type === "Alert") return "red";
	if (event_type === "Gate Transfer") return "blue";
	return "gray";
}

function open_vip_queue(frm) {
	frappe.call({
		method: "visitormanagement.visitor_management.doctype.security_log.security_log.get_approved_vip_queue",
		args: {
			visit_date: frappe.datetime.get_today(),
		},
		callback: ({ message }) => {
			const vip_queue = message || [];
			if (!vip_queue.length) {
				frappe.msgprint(__("No approved VIP visitors are scheduled for today."));
				return;
			}

			const options = vip_queue.map((vip) => vip.name);
			const dialog = new frappe.ui.Dialog({
				title: __("Approved VIP Queue ({0})", [vip_queue.length]),
				fields: [
					{
						fieldname: "visitor_pass",
						fieldtype: "Select",
						label: __("VIP Visitor"),
						options: options.join("\n"),
						reqd: 1,
						change() {
							render_vip_queue_preview(dialog, vip_queue);
						},
					},
					{
						fieldname: "vip_preview",
						fieldtype: "HTML",
					},
				],
				primary_action_label: __("Use VIP Pass"),
				primary_action(values) {
					frm.set_value("visitor_pass", values.visitor_pass);
					dialog.hide();
				},
			});

			dialog.show();
			dialog.set_value("visitor_pass", options[0]);
			render_vip_queue_preview(dialog, vip_queue);
		},
	});
}

function render_vip_queue_preview(dialog, vip_queue) {
	const selected = vip_queue.find((vip) => vip.name === dialog.get_value("visitor_pass"));
	if (!selected) {
		dialog.get_field("vip_preview").$wrapper.empty();
		return;
	}

	// Escape every dynamic value before it enters .html(). visitor_full_name,
	// purpose_of_visit and protocol_notes are free text a visitor can set from
	// the public portal \u2014 rendering them raw is a stored-XSS sink that would
	// run in the privileged reviewer's session. esc() also coerces null/number.
	const esc = (v) => frappe.utils.escape_html(v == null ? "-" : String(v));

	dialog.get_field("vip_preview").$wrapper.html(`
		<div style="border: 1px solid #dbe3ea; border-radius: 12px; padding: 14px; background: #f8fafc; margin-top: 8px;">
			<div style="font-weight: 700; color: #102a43; margin-bottom: 10px;">${esc(
				selected.visitor_full_name
			)}</div>
			<div style="font-size: 12px; color: #334e68; line-height: 1.6;">
				<div><strong>${__("Stage")}:</strong> ${esc(
		selected.workflow_state || selected.status || "-"
	)}</div>
				<div><strong>${__("Visit Window")}:</strong> ${esc(selected.visit_date || "-")} | ${esc(
		selected.expected_checkin || "-"
	)} - ${esc(selected.expected_checkout || "-")}</div>
				<div><strong>${__("Host")}:</strong> ${esc(selected.host_name || "-")}</div>
				<div><strong>${__("Purpose")}:</strong> ${esc(selected.purpose_of_visit || "-")}</div>
				<div><strong>${__("Meeting Room")}:</strong> ${esc(selected.conference_room || "-")}</div>
				<div><strong>${__("MD/CEO Notified")}:</strong> ${
		selected.mdceo_notified ? __("Yes") : __("No")
	}</div>
				<div><strong>${__("Meal / People")}:</strong> ${esc(selected.meal_type || "-")} / ${esc(
		selected.number_of_people || "-"
	)}</div>
				<div><strong>${__("Protocol Notes")}:</strong> ${esc(selected.protocol_notes || "-")}</div>
			</div>
		</div>
	`);
}

function open_badge(frm) {
	const url = frappe.urllib.get_full_url(
		`/printview?doctype=Visitor%20Pass&name=${encodeURIComponent(
			frm.doc.visitor_pass
		)}&format=Visitor%20Badge&no_letterhead=1`
	);
	window.open(url, "_blank");
}
