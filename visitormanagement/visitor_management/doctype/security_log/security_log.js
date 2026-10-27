// For license information, please see license.txt

frappe.ui.form.on("Security Log", {
	setup(frm) {
		frm.add_fetch("visitor_pass", "visitor_full_name", "visitor_name");
		frm.add_fetch("visitor_pass", "badge_number", "badge_number");
		frm.add_fetch("visitor_pass", "visitor_photo", "visitor_photo");
		frm.add_fetch("visitor_pass", "id_proof_scan", "id_proof_scan");

		// Employee pickers search through this app's query: no role needs a
		// permission on the HRMS/ERPNext DocType (see link_queries.py).
		const employee_query = "visitormanagement.visitor_management.link_queries.search";
		frm.set_query("security_officer", () => {
			if (is_security_admin())
				return { query: employee_query, filters: { status: "Active" } };
			// Security staff may only log on their own behalf.
			return {
				query: employee_query,
				filters: { user_id: frappe.session.user, status: "Active" },
			};
		});
		frm.set_query("person_to_visit", () => ({ query: employee_query }));

		// A deactivated gate has no business being offered at the gate — the
		// server also rejects one on save (security_log.py before_save), since
		// this filter is only a picker convenience, not enforcement.
		frm.set_query("gate_name", () => {
			// Where VMS Settings restricts Security to their own gates, offer only
			// those (the server refuses any other, security_log._keep_to_assigned_gate).
			const allowed = (frm.__gate_policy || {}).allowed_gates || [];
			const filters = { is_active: 1 };
			if (allowed.length) filters.name = ["in", allowed];
			return { filters };
		});
	},

	onload(frm) {
		// A token that arrived with the form (the gate API's prefilled route)
		// belongs to the pass it came with; see the visitor_pass handler.
		if (frm.is_new() && frm.doc.scanned_qr_token) frm.__scanned_pass = frm.doc.visitor_pass;
		load_gate_policy(frm).then((policy) => {
			// A guard restricted to their own gates starts on theirs.
			if (frm.is_new() && !frm.doc.gate_name && (policy.allowed_gates || []).length) {
				frm.set_value("gate_name", policy.default_gate || policy.allowed_gates[0]);
			}
		});

		if (frm.is_new() && !frm.doc.security_officer) {
			// Not frappe.db.get_value("Employee", ...): that needs READ on Employee.
			frappe
				.xcall("visitormanagement.visitor_management.link_details.get_own_employee")
				.then((emp) => {
					if (emp && !frm.doc.security_officer) frm.set_value("security_officer", emp);
				});
		}
		if (!is_security_admin()) {
			frm.set_df_property("security_officer", "read_only", 1);
		}
	},

	refresh(frm) {
		apply_security_log_ui(frm);
		mask_id_proof_number(frm);

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
		if (!frm.is_new() && !(frappe.user_roles || []).includes("System Manager")) {
			frm.disable_form();
			frm.set_intro(
				__("This Security Log is locked. Gate events are immutable once saved."),
				"blue"
			);
		}
	},

	event_type(frm) {
		if (!frm.doc.verification_started_on) {
			frm.set_value("verification_started_on", frappe.datetime.now_datetime());
		}
		sync_event_fields(frm);
		load_item_rows(frm);
		load_member_rows(frm);
		// The booked-time warning depends on the movement (load_gate_policy).
		if (frm.doc.visitor_pass) load_gate_policy(frm);
		apply_security_log_ui(frm);
	},

	gate_name(frm) {
		// The server decides this on save; shown here so the tick matches.
		const default_gate = (frm.__gate_policy || {}).default_gate;
		if (frm.is_new() && default_gate) {
			frm.set_value("gate_auto_assigned", frm.doc.gate_name === default_gate ? 1 : 0);
		}
		sync_event_fields(frm);
		apply_security_log_ui(frm);
	},

	check_in_date_time(frm) {
		apply_exception_reason_ui(frm);
	},

	check_out_date_time(frm) {
		apply_exception_reason_ui(frm);
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
		if (frm.doc.event_type === "Check-In" && frm.doc.visitor_pass) {
			frappe.show_alert({
				message: __("Check-In recorded. Opening badge for printing..."),
				indicator: "green",
			});

			setTimeout(() => {
				frm.trigger("print_visitor_badge");
			}, 1000);
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

		// Ask the server what this site actually requires. The checklist used to
		// hardcode all three, so on a site that requires none of them the officer
		// was told to finish steps on a record that had already saved and locked
		// itself — an instruction that could not be carried out.
		frappe.call({
			method: "visitormanagement.visitor_management.doctype.security_log.security_log.get_gate_policy",
			callback: ({ message: policy = {} }) => {
				const checks = [];

				if (policy.photo_required) {
					checks.push({
						ok: Boolean(frm.doc.photo_at_gate),
						label: __("Capture the live gate photo"),
					});
				}
				if (policy.identity_match_required) {
					checks.push({
						ok: Boolean(frm.doc.id_proof_match) && Boolean(frm.doc.pass_photo_match),
						label: __("Confirm both identity matches (ID proof + pass photo)"),
					});
				}
				if (policy.qr_scan_required) {
					checks.push({
						ok: Boolean(frm.doc.qr_code_scanned),
						label: __("Scan the visitor's QR code"),
					});
				}
				checks.push({
					ok: !(frm.is_new() || frm.is_dirty()),
					label: __("Save the check-in"),
				});

				const pending = checks.filter((c) => !c.ok);
				if (!pending.length) {
					open_badge(frm);
					return;
				}
				const items = checks
					.map(
						(c) =>
							`<li style="color:${c.ok ? "#059669" : "#b45309"};">` +
							`${c.ok ? "&#10003;" : "&#9888;"} ${c.label}` +
							`</li>`
					)
					.join("");
				frappe.msgprint({
					title: __("Finish these steps to print the badge"),
					message: `<ul style="padding-left:18px; margin:0;">${items}</ul>`,
					indicator: "orange",
				});
			},
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
			let closed = false;
			let handled = false;

			function stop_scanner() {
				if (html5QrCode && html5QrCode.isScanning) {
					html5QrCode.stop().catch((err) => {
						console.warn("Failed to stop QR scanner", err);
					});
				}
			}

			// The camera starts a moment after the dialog opens. Closed before
			// then, it used to start anyway, in a dialog nobody could see, and
			// keep the camera busy until the page was reloaded.
			const start_timer = setTimeout(() => {
				if (closed || !scanner_dialog.display) return;
				try {
					html5QrCode = new Html5Qrcode(scanner_id);

					const qrCodeSuccessCallback = (decodedText) => {
						// The library reports the same code several times a second.
						if (handled) return;
						handled = true;
						const { visitor_pass, token } = parse_pass_qr(decodedText);

						// Only the pass and the token it carries go to the server,
						// which decides whether this was a real scan of this pass
						// (qr_code_scanned) — the form no longer ticks that itself.
						frm.__scanned = true;
						frm.__scanned_pass = visitor_pass;
						frm.set_value("scanned_qr_token", token || QR_SCAN_WITHOUT_TOKEN).then(
							() => frm.set_value("visitor_pass", visitor_pass)
						);
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
						.then(() => {
							// Closed while the browser was still asking for the camera.
							if (closed) stop_scanner();
						})
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

			scanner_dialog.on_hide = () => {
				closed = true;
				clearTimeout(start_timer);
				stop_scanner();
			};
		});
	},

	visitor_pass(frm) {
		// A scanned token proves the QR of the pass it was read from. Picking or
		// typing another pass afterwards must not carry it over: the server
		// would rightly call that a copied QR.
		if (frm.doc.scanned_qr_token && frm.doc.visitor_pass !== frm.__scanned_pass) {
			frm.set_value("scanned_qr_token", "");
		}

		if (!frm.doc.visitor_pass) {
			delete frm.__visitor_type;
			delete frm.__visitor_type_layout;
			show_timing_warning(frm, "");
			apply_security_log_ui(frm);
			return;
		}

		if (!frm.doc.verification_started_on) {
			frm.set_value("verification_started_on", frappe.datetime.now_datetime());
		}

		frappe.db.get_value(
			"Visitor Pass",
			frm.doc.visitor_pass,
			[
				"visitor_photo",
				"id_proof_scan",
				"visitor_full_name",
				"badge_number",
				"id_proof_type",
				"visitor_type",
				"visitor_type_layout",
				"status",
				// Already masked (last four); the full number never needs to reach the form.
				"id_proof_masked",
				"mdceo_notified",
				"conference_room",
				"protocol_notes",
				"multi_day_pass",
				"pass_valid_until",
				"visit_date",
				"expected_checkin",
				"expected_checkout",
			],
			(r) => {
				if (!r) {
					apply_security_log_ui(frm);
					return;
				}

				frm.__visitor_type = r.visitor_type;
				// The layout drives VIP-specific gate behaviour, so a site's own
				// executive type behaves like VIP without being named "VIP".
				frm.__visitor_type_layout = r.visitor_type_layout;

				if (r.visitor_photo) frm.set_value("visitor_photo", r.visitor_photo);
				if (r.id_proof_scan) frm.set_value("id_proof_scan", r.id_proof_scan);
				frm.set_value("visitor_name", r.visitor_full_name);

				// A pass with no badge yet gets one when the check-in is saved, and it
				// shows here after that save. Picking a pass must not issue it: that
				// minted a badge and moved the pass to "Items Verified" for a gate
				// event nobody had recorded — or ever did, if the form was closed.
				frm.set_value("badge_number", r.badge_number || "");

				if (r.id_proof_masked) {
					frm.set_value("id_proof_number", r.id_proof_masked);
				}

				if (r.id_proof_type && !frm.doc.id_proof_type_verified) {
					frm.set_value("id_proof_type_verified", r.id_proof_type);
				}

				if (frm.is_new()) {
					let event_type = "";
					if (["Items Verified", "Approved"].includes(r.status)) {
						event_type = "Check-In";
					} else if (r.status === "Checked-In") {
						event_type = "Check-Out";
					} else if (r.status === "Checked-Out") {
						// Checking out does not retire a pass: it stays valid for the rest of
						// its visit date (a visitor stepping out for lunch), and a multi-day
						// pass for every day through pass_valid_until. Without this carve-out
						// this dialog fired on every re-entry and cleared visitor_pass,
						// stranding the officer even though the server (security_log.py
						// before_save) would have accepted the check-in. Same window rule as
						// the server side and visitor_gate._valid_today.
						const today = frappe.datetime.get_today();
						const last_day =
							r.multi_day_pass && r.pass_valid_until
								? r.pass_valid_until
								: r.visit_date;
						// After midnight on an overnight visit (check-out time at or
						// before check-in) the night began yesterday (gate_timing).
						const now_time = time_seconds(frappe.datetime.now_time());
						const overnight =
							r.expected_checkin &&
							r.expected_checkout &&
							time_seconds(r.expected_checkout) <=
								time_seconds(r.expected_checkin) &&
							now_time < time_seconds(r.expected_checkout);
						const days = overnight
							? [today, frappe.datetime.add_days(today, -1)]
							: [today];
						const reentry_open =
							r.visit_date &&
							days.some((day) => r.visit_date <= day && day <= last_day);
						if (reentry_open) {
							event_type = "Check-In";
						} else {
							frappe.msgprint({
								title: __("Already Scanned"),
								message: __(
									"Visitor has already checked out and the pass is inactive."
								),
								indicator: "orange",
							});
							frm.set_value("visitor_pass", "");
							return;
						}
					} else {
						frappe.msgprint({
							title: __("Invalid Status"),
							message: __(
								'Visitor Pass status is "{0}". It must be "Approved" or "Items Verified" to check in.',
								[r.status]
							),
							indicator: "red",
						});
						return;
					}

					frm.set_value("event_type", event_type);
					// Also when the event was already the right one and no handler ran.
					sync_event_fields(frm);
					load_item_rows(frm);
					load_member_rows(frm);
				}

				const scanned = frm.__scanned;
				delete frm.__scanned;

				// The gate the server would assign (Visitor Type default, else the
				// first active gate), pre-selected so the guard does not pick it.
				// It used to come from a hardcoded map of type names to gate names
				// that existed on no real site.
				load_gate_policy(frm).then((policy) => {
					if (frm.is_new() && !frm.doc.gate_name && policy.default_gate) {
						frm.set_value("gate_name", policy.default_gate);
					}
					// Straight to the camera after a scan only where a photo is
					// required — and only where the browser has a camera API (a
					// gate PC on plain HTTP has none, and got an error per scan).
					if (
						scanned &&
						policy.photo_required &&
						["Check-In", "Check-Out"].includes(frm.doc.event_type) &&
						!frm.doc.photo_at_gate &&
						navigator.mediaDevices &&
						navigator.mediaDevices.getUserMedia
					) {
						setTimeout(() => frm.trigger("capture_photo"), 500);
					}
				});

				apply_security_log_ui(frm);
			}
		);
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
									message: __("Gate photo captured and saved."),
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

frappe.ui.form.on("Security Log Member", {
	present(frm) {
		render_group_summary(frm);
		apply_badge_returned_ui(frm);
	},
});

frappe.ui.form.on("Security Item Verify", {
	quantity_found(frm, cdt, cdn) {
		// Shown straight away; the server works it out again on save.
		const row = locals[cdt][cdn];
		if (!row) return;
		const discrepancy =
			row.quantity_found != null &&
			row.quantity_declared != null &&
			flt(row.quantity_found) !== flt(row.quantity_declared)
				? 1
				: 0;
		if (cint(row.discrepancy) !== discrepancy) {
			frappe.model.set_value(cdt, cdn, "discrepancy", discrepancy);
		}
		recompute_all_items_confirmed(frm);
		render_items_progress_summary(frm);
		apply_exception_reason_ui(frm);
	},

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
		apply_exception_reason_ui(frm);
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

					// Against the Security Log, as the gate photo is — and as Frappe's
					// own attach control does for a row. Nobody has write on the child
					// DocType, so an upload naming the row was refused (403) every time.
					upload_captured_image({
						file,
						doctype: frm.doctype,
						docname: frm.doc.name,
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

// On an unsaved log, keep the fields that follow from the event and the gate in
// step with them. The hidden Visited Area is the gate of the event: it was filled
// once, from the first gate chosen, so a Gate Transfer kept the visitor at the old
// gate. A check-in / check-out time that does not belong to the event is cleared.
// The times themselves are stamped by the server on save — the form used to fill
// them from the browser's clock, which a wrong PC clock got wrong for every
// visitor. The server applies the same rules on save (security_log.py).
function sync_event_fields(frm) {
	if (!frm.is_new()) return;

	const gate = frm.doc.gate_name || "";
	if ((frm.doc.visited_area || "") !== gate) {
		frm.set_value("visited_area", gate);
	}

	const times = { "Check-In": "check_in_date_time", "Check-Out": "check_out_date_time" };
	Object.entries(times).forEach(([event_type, fieldname]) => {
		if (frm.doc.event_type !== event_type && frm.doc[fieldname]) {
			frm.set_value(fieldname, null);
		}
	});
}

// What security_log.py stores in scanned_qr_token for a QR with no TOKEN part
// (QR_SCAN_WITHOUT_TOKEN there): a pass issued before passes had tokens.
const QR_SCAN_WITHOUT_TOKEN = "NO-TOKEN";

// "PASS:<name>|TOKEN:<token>|VISITOR:...|VISIT_DATE:..." — or, on the oldest
// passes, the bare pass name.
function parse_pass_qr(text) {
	const parts = {};
	if (text.includes("|") || text.includes(":")) {
		for (const part of text.split("|")) {
			const at = part.indexOf(":");
			if (at > 0) parts[part.slice(0, at).trim()] = part.slice(at + 1).trim();
		}
	}
	return { visitor_pass: parts.PASS || text.trim(), token: parts.TOKEN || "" };
}

// What this site requires at the gate, and (for the pass on the form) the gate
// the server assigns. Asked once per pass and movement; the fields' mandatory
// marks follow it, so the form asks for what the server enforces and nothing more.
function load_gate_policy(frm) {
	const visitor_pass = frm.doc.visitor_pass || null;
	const event_type = frm.doc.event_type || null;
	const key = `${visitor_pass}|${event_type}`;
	if (frm.__gate_policy && frm.__gate_policy_for === key) {
		return Promise.resolve(frm.__gate_policy);
	}
	// Picking a pass sets the movement too, and both ask: one request, one warning.
	if (frm.__gate_policy_pending && frm.__gate_policy_pending_for === key) {
		return frm.__gate_policy_pending;
	}
	frm.__gate_policy_pending_for = key;
	frm.__gate_policy_pending = frappe
		.xcall(
			"visitormanagement.visitor_management.doctype.security_log.security_log.get_gate_policy",
			{ visitor_pass, event_type }
		)
		.then((policy) => {
			frm.__gate_policy = policy || {};
			frm.__gate_policy_for = key;
			show_timing_warning(frm, frm.__gate_policy.timing_warning);
			apply_security_log_ui(frm);
			return frm.__gate_policy;
		})
		.finally(() => {
			if (frm.__gate_policy_pending_for === key) frm.__gate_policy_pending = null;
		});
	return frm.__gate_policy_pending;
}

// "9:00:00" (a Time as the server sends it) or "21:30" -> seconds since midnight.
function time_seconds(value) {
	const [h = 0, m = 0, sec = 0] = String(value)
		.split(":")
		.map((part) => parseFloat(part) || 0);
	return h * 3600 + m * 60 + sec;
}

// Outside the booked time (gate_timing): told when the pass is picked, on the
// server's clock — a short toast, never a refusal, so the queue keeps moving.
// The save records the same note on the log, worked out again by the server.
function show_timing_warning(frm, warning) {
	if (!frm.is_new()) return;
	frm.doc.timing_note = warning || "";
	frm.refresh_field("timing_note");
	if (!warning) return;
	frappe.show_alert(
		{
			message: __("Outside booked time: {0} You can still record it.", [
				frappe.utils.escape_html(warning),
			]),
			indicator: "orange",
		},
		10
	);
}

// The item rows, from the server — the same code that rebuilds them on save, so
// what the officer ticks is what gets recorded. At check-out they start from what
// was found at check-in.
function load_item_rows(frm) {
	if (!frm.is_new() || !frm.doc.visitor_pass) return;
	const visitor_pass = frm.doc.visitor_pass;
	const event_type = frm.doc.event_type;
	if (!["Check-In", "Check-Out"].includes(event_type)) return;
	frappe
		.xcall(
			"visitormanagement.visitor_management.doctype.security_log.security_log.get_item_rows",
			{ visitor_pass, event_type }
		)
		.then((rows) => {
			// The pass may have been cleared or changed while this was loading
			// (an "Already Scanned" pass is cleared straight away).
			if (frm.doc.visitor_pass !== visitor_pass || frm.doc.event_type !== event_type) return;
			frm.clear_table("items_verification");
			(rows || []).forEach((values) => {
				const row = frm.add_child("items_verification");
				Object.assign(row, values);
			});
			frm.refresh_field("items_verification");
			apply_security_log_ui(frm);
		});
}

// The group members this log moves, from the server — the code that rebuilds them
// on save. Everyone starts ticked; the guard unticks whoever is not at the gate.
function load_member_rows(frm) {
	if (!frm.is_new() || !frm.doc.visitor_pass) return;
	const visitor_pass = frm.doc.visitor_pass;
	const event_type = frm.doc.event_type;
	if (!["Check-In", "Check-Out"].includes(event_type)) {
		frm.__group = null;
		if ((frm.doc.group_attendance || []).length) {
			frm.clear_table("group_attendance");
			frm.refresh_field("group_attendance");
		}
		apply_security_log_ui(frm);
		return;
	}
	frappe
		.xcall(
			"visitormanagement.visitor_management.doctype.security_log.security_log.get_member_rows",
			{ visitor_pass, event_type }
		)
		.then((result) => {
			if (frm.doc.visitor_pass !== visitor_pass || frm.doc.event_type !== event_type) return;
			frm.__group = result || {};
			frm.clear_table("group_attendance");
			(frm.__group.rows || []).forEach((values) => {
				const row = frm.add_child("group_attendance");
				Object.assign(row, values);
			});
			frm.set_value("group_members_only", cint(frm.__group.group_members_only));
			frm.refresh_field("group_attendance");
			apply_security_log_ui(frm);
		});
}

// "4 of 5 arriving" above the member grid, with one tap to tick everyone again.
function render_group_summary(frm) {
	const field = frm.fields_dict && frm.fields_dict.group_attendance;
	if (!field || !field.$wrapper) return;
	field.$wrapper.find(".vm-group-summary").remove();
	const rows = frm.doc.group_attendance || [];
	if (!rows.length) return;

	const present = rows.filter((r) => cint(r.present)).length;
	const leaving = frm.doc.event_type === "Check-Out";
	const lead = cint(frm.doc.group_members_only) ? 0 : 1;
	const label = leaving
		? __("Leaving now: {0} of {1} group members", [present, rows.length])
		: __("Arriving now: {0} of {1} group members", [present, rows.length]);
	const people = lead
		? __("{0} people on this log, with the lead visitor", [present + lead])
		: __("Group members only — the lead visitor is not moving");
	const can_tick = frm.is_new() && present < rows.length;
	field.$wrapper.prepend(`
		<div class="vm-group-summary" style="display: flex; align-items: center; gap: 10px; padding: 8px 12px; margin: 0 0 8px 0; border-radius: 8px; background: ${
			present === rows.length ? "#d9f3e4" : "#fff4d6"
		}; color: ${
		present === rows.length ? "#0d6b3e" : "#8d5d00"
	}; font-size: 12px; font-weight: 600;">
			<span>${frappe.utils.escape_html(label)}</span>
			<span style="font-weight: 400;">${frappe.utils.escape_html(people)}</span>
			${
				can_tick
					? `<button type="button" class="btn btn-xs btn-default vm-all-present" style="margin-left: auto;">${__(
							"All present"
					  )}</button>`
					: ""
			}
		</div>
	`);
	if (can_tick) {
		field.$wrapper.find(".vm-all-present").on("click", () => {
			(frm.doc.group_attendance || []).forEach((row) => {
				if (!cint(row.present))
					frappe.model.set_value(row.doctype, row.name, "present", 1);
			});
			frm.refresh_field("group_attendance");
			render_group_summary(frm);
		});
	}
}

// "Badge Returned" is shown on a check-out that has a badge to hand back, ticked:
// handing it back is what nearly everyone does, so the guard taps only when not.
function apply_badge_returned_ui(frm) {
	const policy = frm.__gate_policy || {};
	const member_badges = (frm.doc.group_attendance || []).some(
		(r) => cint(r.present) && r.badge_number
	);
	const lead_badge = !cint(frm.doc.group_members_only) && !!frm.doc.badge_number;
	frm.toggle_display(
		"badge_returned",
		frm.doc.event_type === "Check-Out" &&
			policy.track_badge_return !== false &&
			(lead_badge || member_badges)
	);
}

function is_security_admin() {
	// Only System Manager can override the security_officer field. HR Manager
	// has no server-side perm on Security Log, so they never reach this path.
	const roles = frappe.user_roles || [];
	return roles.includes("System Manager");
}

function mask_id_proof_number(frm) {
	const val = frm.doc.id_proof_number;
	// "X" guard prevents double-masking on form refresh. Don't gate on spaces —
	// real DL ("TN05 20210001234") and spaced Aadhaar ("2345 6789 0124") have
	// real spaces that would otherwise leave the full number visible.
	if (!val || val.includes("X")) return;
	frm.set_value("id_proof_number", build_masked_id(val));
}

function build_masked_id(val) {
	if (!val) return val;
	if (val.length <= 4) return val;
	const last4 = val.slice(-4);
	const maskLen = val.length - 4;
	let masked = "X".repeat(maskLen);
	const chunks = [];
	for (let i = 0; i < masked.length; i += 4) chunks.push(masked.slice(i, i + 4));
	chunks.push(last4);
	return chunks.join(" ");
}

function apply_security_log_ui(frm) {
	[
		"badge_number",
		"visitor_name",
		"visitor_company",
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

	const has_pass = !!frm.doc.visitor_pass;
	const is_vip = frm.__visitor_type_layout === "VIP";
	const is_check_in = frm.doc.event_type === "Check-In";
	const is_check_out = frm.doc.event_type === "Check-Out";
	const has_items = !!(frm.doc.items_verification && frm.doc.items_verification.length);
	const show_items = is_check_in || has_items;
	const show_gate_photo = [
		"Check-In",
		"Check-Out",
		"Alert",
		"Gate Transfer",
		"Badge Collected",
	].includes(frm.doc.event_type);
	// Times are stamped by the server on save. Only a System Manager sees them
	// on a new log, to record a different time (with a reason); a guard has
	// nothing to type.
	const can_backdate = frm.is_new() && is_security_admin();
	frm.toggle_display(
		"check_in_date_time",
		(is_check_in && can_backdate) || (!frm.is_new() && !!frm.doc.check_in_date_time)
	);
	frm.toggle_display(
		"check_out_date_time",
		(is_check_out && can_backdate) || (!frm.is_new() && !!frm.doc.check_out_date_time)
	);
	["check_in_date_time", "check_out_date_time"].forEach((fieldname) => {
		frm.set_df_property(fieldname, "read_only", can_backdate ? 0 : 1);
		frm.set_df_property(
			fieldname,
			"description",
			can_backdate
				? __(
						"Leave empty to record the time of saving. A different time needs an Exception Reason."
				  )
				: __("Stamped by the server when the log is saved.")
		);
	});
	frm.toggle_reqd(["check_in_date_time", "check_out_date_time"], false);

	frm.toggle_display("visitor_photo", false);
	frm.toggle_display("id_proof_scan", false);
	frm.toggle_display(
		["section_break_identity_comparison", "identity_comparison_html"],
		has_pass
	);
	frm.toggle_display(["photo_at_gate", "capture_photo"], show_gate_photo);
	frm.toggle_display(
		["id_proof_match", "pass_photo_match", "verification_notes"],
		is_check_in || is_check_out
	);
	frm.toggle_display(["section_break_items", "all_items_confirmed"], show_items && has_pass);
	// Mandatory exactly where VMS Settings makes the server insist (get_gate_policy).
	// These used to be mandatory on every check-in and check-out, so a site that
	// requires no gate photo still could not save from a PC without a camera.
	const policy = frm.__gate_policy || {};
	const movement = is_check_in || is_check_out;
	frm.toggle_reqd("photo_at_gate", movement && !!policy.photo_required);
	frm.toggle_reqd(
		["id_proof_match", "pass_photo_match"],
		movement && !!policy.identity_match_required
	);
	apply_exception_reason_ui(frm);
	frm.toggle_display(
		["mdceo_notified", "vip_meeting_room", "vip_protocol_notes"],
		has_pass && is_vip
	);

	render_identity_comparison(frm);
	render_items_progress_summary(frm);
	render_group_summary(frm);
	apply_badge_returned_ui(frm);
	set_security_log_intro(frm, is_check_in, is_check_out);
	if (frm.dashboard) frm.dashboard.clear_headline();
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
	const is_check_out = frm.doc.event_type === "Check-Out";
	// Only offer the bulk action when there is something left to tick and the
	// form can actually be written to (a submitted or locked log must not).
	const can_verify_all = !all_verified && !frm.doc.docstatus && !frm.is_dirty_disabled;
	const bg = all_verified ? "#d9f3e4" : discrepancies ? "#fde2e2" : "#fff4d6";
	const fg = all_verified ? "#0d6b3e" : discrepancies ? "#9b1c1c" : "#8d5d00";
	const icon = all_verified ? "✅" : discrepancies ? "⚠️" : "🟡";

	const summary = `
		<div class="vm-items-progress" style="display: flex; align-items: center; gap: 10px; padding: 8px 12px; margin: 0 0 8px 0; border-radius: 8px; background: ${bg}; color: ${fg}; font-size: 12px; font-weight: 600;">
			<span style="font-size: 14px;">${icon}</span>
			<span>${
				is_check_out
					? __("Items: {0} / {1} going out", [verified, total])
					: __("Items: {0} / {1} verified", [verified, total])
			}</span>
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
					  };">${is_check_out ? __("All Items Going Out") : __("Verify All")}</button>`
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
			apply_exception_reason_ui(frm);
		});
	}
}

// Exception Reason is asked for only when it is needed, and then marked
// mandatory with a line saying what to write. Two cases, both enforced again on
// save: a check-out where a declared item is not going out (the visitor still
// leaves; the supervisor is alerted), and a System Manager recording a time
// other than now.
function apply_exception_reason_ui(frm) {
	if (!frm.is_new()) {
		frm.toggle_display("exception_reason", !!frm.doc.exception_reason);
		frm.toggle_reqd("exception_reason", false);
		return;
	}
	const items_optional = !!(frm.__gate_policy || {}).items_optional;
	const items_short =
		frm.doc.event_type === "Check-Out" &&
		(frm.doc.items_verification || []).some(
			(r) => r.discrepancy || (!r.item_verified && !items_optional)
		);
	const backdated =
		is_security_admin() &&
		((frm.doc.event_type === "Check-In" && !!frm.doc.check_in_date_time) ||
			(frm.doc.event_type === "Check-Out" && !!frm.doc.check_out_date_time));
	const needed = items_short || backdated;

	frm.toggle_display("exception_reason", needed || !!frm.doc.exception_reason);
	frm.toggle_reqd("exception_reason", needed);
	frm.set_df_property(
		"exception_reason",
		"description",
		items_short
			? __(
					"Some items are not going out. Write a few words on why — the visitor can still leave, and the supervisor will be alerted."
			  )
			: backdated
			? __("Say why this is being recorded at a time other than now.")
			: ""
	);
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
	const is_check_out = frm.doc.event_type === "Check-Out";
	// Same rule as the server: at check-out a short count is not confirmed.
	const expected =
		rows.length === 0 ||
		rows.every((r) => !!r.item_verified && !(is_check_out && r.discrepancy))
			? 1
			: 0;
	if (Number(frm.doc.all_items_confirmed || 0) !== expected) {
		frm.set_value("all_items_confirmed", expected);
	}
}

// Hide badge_number field on Security Log when VMS Settings → enable_badge is off.
function apply_badge_visibility_sl(frm) {
	const setHidden = (hide) => {
		frm.set_df_property("badge_number", "hidden", hide ? 1 : 0);
		frm.toggle_display("badge_number", !hide);
		frm.refresh_field("badge_number");
	};
	setHidden(true);
	frappe.db.get_value("VMS Settings", "VMS Settings", "enable_badge").then((r) => {
		// Single-doctype fields come back as strings — coerce via cint.
		const enabled = !!cint(((r && r.message) || {}).enable_badge);
		setHidden(!enabled);
	});
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

function set_security_log_intro(frm, is_check_in, is_check_out) {
	// The event's own line, with a hint line under it where one applies: the form
	// has one intro box, and a second set_intro would replace the first.
	let intro = null;
	const set_intro = frm.set_intro;
	frm.set_intro = (txt, color) => (intro = { txt, color });
	try {
		set_event_intro(frm, is_check_in, is_check_out);
	} finally {
		frm.set_intro = set_intro;
	}
	const hints = frm.is_new() ? get_gate_hints(frm) : [];
	if (!intro && !hints.length) return;
	const lines = [intro && intro.txt, ...hints.map((h) => frappe.utils.escape_html(h))].filter(
		Boolean
	);
	frm.set_intro(lines.join("<br>"), intro && !hints.length ? intro.color : "yellow");
}

function get_gate_hints(frm) {
	// One line each, only when it applies.
	const hints = [];
	if ((frm.__gate_policy || {}).no_gate_assigned) {
		hints.push(
			__(
				"You are not assigned to a gate yet, so every gate is open to you. Ask an administrator to add you to your gate."
			)
		);
	}
	const group = frm.__group || {};
	if (
		frm.doc.event_type === "Check-Out" &&
		!cint(frm.doc.group_members_only) &&
		group.members_outside
	) {
		hints.push(
			__(
				"{0} group member(s) have not arrived yet. To let them in, change Event Type to Check-In.",
				[group.members_outside]
			)
		);
	}
	if (
		frm.doc.event_type === "Check-In" &&
		!cint(frm.doc.group_members_only) &&
		group.members_inside
	) {
		hints.push(
			__(
				"{0} group member(s) are still inside. To let them out, change Event Type to Check-Out.",
				[group.members_inside]
			)
		);
	}
	return hints;
}

function set_event_intro(frm, is_check_in, is_check_out) {
	if (!frm.doc.visitor_pass) {
		frm.set_intro(
			__(
				"Scan the QR code, select a Visitor Pass, or use Approved VIP Queue for priority visitors."
			),
			"blue"
		);
		return;
	}

	if (is_check_in) {
		frm.set_intro(
			__(
				frm.__visitor_type_layout === "VIP"
					? "VIP priority check-in. Review protocol notes, confirm MD/CEO notification, capture gate photo, then save and print the badge."
					: "Compare the ID proof, pass photo, and live gate capture, complete health screening, then save the check-in and print the badge."
			),
			"green"
		);
		return;
	}

	if (is_check_out) {
		frm.set_intro(
			__(
				"Confirm the visitor identity and record the exit cleanly before saving the check-out."
			),
			"orange"
		);
		return;
	}

	if (frm.doc.event_type === "Gate Transfer") {
		frm.set_intro(
			__(
				"Record the visitor's new area so contact tracing and emergency muster stay accurate."
			),
			"blue"
		);
		return;
	}

	frm.set_intro(
		__("Record the gate event with concise notes and supporting capture if needed."),
		"blue"
	);
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
				<div><strong>${__("Host")}:</strong> ${esc(
		selected.host_name || selected.person_to_visit || "-"
	)}</div>
				<div><strong>${__("Purpose")}:</strong> ${esc(selected.purpose_of_visit || "-")}</div>
				<div><strong>${__("Meeting Room")}:</strong> ${esc(selected.conference_room || "-")}</div>
				<div><strong>${__("MD/CEO Notified")}:</strong> ${
		selected.mdceo_notified ? __("Yes") : __("No")
	}</div>
				<div><strong>${__("Meal / People")}:</strong> ${esc(selected.meal_type || "-")} / ${esc(
		selected.number_of_people || "-"
	)}</div>
				<div><strong>${__("Declared Items")}:</strong> ${
		selected.visitor_items && selected.visitor_items.length
			? selected.visitor_items
					.map(
						(i) =>
							`${esc(i.item_name || "-")}${
								i.quantity ? ` \u00D7${esc(i.quantity)}` : ""
							}`
					)
					.join(", ")
			: __("No items declared")
	}</div>
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
