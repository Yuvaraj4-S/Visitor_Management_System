// Copyright (c) 2026, Harthesh and contributors
// For license information, please see license.txt

frappe.ui.form.on("Security Log", {
	setup(frm) {
		frm.add_fetch("visitor_pass", "visitor_full_name", "visitor_name");
		frm.add_fetch("visitor_pass", "badge_number", "badge_number");
		frm.add_fetch("visitor_pass", "visitor_photo", "visitor_photo");
		frm.add_fetch("visitor_pass", "id_proof_scan", "id_proof_scan");
		frm.add_fetch("visitor_pass", "id_proof_number", "id_proof_number");
	},

	refresh(frm) {
		apply_security_log_ui(frm);

		if (frm.doc.visitor_pass) {
			frm.add_custom_button(
				__("Print Badge"),
				() => frm.trigger("print_visitor_badge"),
				__("Actions")
			);
		}
	},

	event_type(frm) {
		if (!frm.doc.verification_started_on) {
			frm.set_value("verification_started_on", frappe.datetime.now_datetime());
		}
		apply_security_log_ui(frm);
	},

	manual_override(frm) {
		apply_security_log_ui(frm);
	},

	alert_level(frm) {
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

		if (frm.doc.event_type === "Check-In") {
			if (!frm.doc.photo_at_gate) {
				frappe.msgprint(__("Capture the live gate photo before printing the visitor badge."));
				return;
			}

			if (!frm.doc.id_proof_match || !frm.doc.pass_photo_match) {
				frappe.msgprint(__("Complete the identity match confirmation before printing the visitor badge."));
				return;
			}

			if (frm.is_new() || frm.is_dirty()) {
				frappe.msgprint(__("Save the check-in first so the gate photo becomes the badge photo."));
				return;
			}
		}

		const url = frappe.urllib.get_full_url(
			`/printview?doctype=Visitor%20Pass&name=${encodeURIComponent(frm.doc.visitor_pass)}&format=Visitor%20Badge&no_letterhead=1`
		);
		window.open(url, "_blank");
	},

	qr_code_value(frm) {
		frappe.require("/assets/visitormanagement/js/libs/html5-qrcode.min.js", () => {
			if (typeof Html5Qrcode === "undefined") {
				frappe.msgprint({
					title: __("Error"),
					message: __("QR scanner library did not load. Refresh the page and try again."),
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
							html5QrCode.start({ facingMode: "user" }, config, qrCodeSuccessCallback)
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
		if (!frm.doc.visitor_pass) {
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
				"status",
				"id_proof_number",
			],
			(r) => {
				if (!r) {
					apply_security_log_ui(frm);
					return;
				}

				if (r.visitor_photo) frm.set_value("visitor_photo", r.visitor_photo);
				if (r.id_proof_scan) frm.set_value("id_proof_scan", r.id_proof_scan);
				frm.set_value("visitor_name", r.visitor_full_name);

				if (r.badge_number) {
					frm.set_value("badge_number", r.badge_number);
				} else {
					frappe.call({
						method: "visitormanagement.visitor_management.doctype.visitor_pass.visitor_pass.sync_badge_number",
						args: { visitor_pass: frm.doc.visitor_pass },
						callback: (res) => {
							if (res.message) {
								frm.set_value("badge_number", res.message);
							}
						},
					});
				}

				if (r.id_proof_number) {
					frm.set_value("id_proof_number", r.id_proof_number);
					frm.set_value(
						"id_last_4_digits",
						r.id_proof_number.length >= 4 ? r.id_proof_number.slice(-4) : r.id_proof_number
					);
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
						frappe.msgprint({
							title: __("Already Scanned"),
							message: __("Visitor has already checked out and the pass is inactive."),
							indicator: "orange",
						});
						frm.set_value("visitor_pass", "");
						return;
					} else {
						frappe.msgprint({
							title: __("Invalid Status"),
							message: __('Visitor Pass status is "{0}". It must be "Approved" or "Items Verified" to check in.', [r.status]),
							indicator: "red",
						});
						return;
					}

					frm.set_value("event_type", event_type);

					if (event_type === "Check-In" && !frm.doc.check_in_date_time) {
						frm.set_value("check_in_date_time", frappe.datetime.now_datetime());
					} else if (event_type === "Check-Out" && !frm.doc.check_out_date_time) {
						frm.set_value("check_out_date_time", frappe.datetime.now_datetime());
					}

					if (!frm.doc.gate_name) {
						const gate_rules = {
							VIP: "VIP Entrance",
							Supplier: "Loading Dock",
							Contractor: "Back Gate",
							Candidate: "Main Gate",
							Customer: "Main Gate",
						};
						frm.set_value("gate_name", gate_rules[r.visitor_type] || "Main Gate");
					}
				}

				if (frm.__scanned) {
					if (frm.doc.event_type === "Check-In" && !frm.doc.photo_at_gate) {
						setTimeout(() => frm.trigger("capture_photo"), 500);
					}
					delete frm.__scanned;
				}

				apply_security_log_ui(frm);
			}
		);

		if (frm.is_new() && (!frm.doc.items_verification || frm.doc.items_verification.length === 0)) {
			frappe.model.with_doc("Visitor Pass", frm.doc.visitor_pass, () => {
				const vp = frappe.model.get_doc("Visitor Pass", frm.doc.visitor_pass);
				if (vp.visitor_items && vp.visitor_items.length > 0) {
					frm.clear_table("items_verification");
					vp.visitor_items.forEach((item) => {
						const row = frm.add_child("items_verification");
						row.item_name = item.item_name;
						row.item_category = item.item_category;
						row.quantity_declared = item.quantity;
						row.uom = item.unit_of_measure;
						row.serial__asset_number = item.serial_number;
						row.quantity_found = item.quantity;
						row.item_verified = 0;
						row.visitor_item_row_name = item.name;
					});
					frm.refresh_field("items_verification");
				}
				apply_security_log_ui(frm);
			});
		}
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
				const video = document.getElementById("capture-video");
				const canvas = document.createElement("canvas");
				canvas.width = video.videoWidth;
				canvas.height = video.videoHeight;
				const context = canvas.getContext("2d");
				context.drawImage(video, 0, 0, canvas.width, canvas.height);

				canvas.toBlob((blob) => {
					const file_name = `gate_photo_${frappe.datetime.now_datetime().replace(/[: -]/g, "_")}.png`;
					const reader = new FileReader();
					reader.onload = (e) => {
						const upload_args = {
							from_form: 1,
							fieldname: "photo_at_gate",
							filedata: e.target.result.split(",")[1],
							filename: file_name,
						};

						if (frm.doc.name && !frm.doc.name.startsWith("new-")) {
							upload_args.doctype = frm.doc.doctype;
							upload_args.docname = frm.doc.name;
						}

						frappe.call({
							method: "frappe.handler.upload_file",
							args: upload_args,
							callback: (r) => {
								if (r.message && r.message.file_url) {
									frm.set_value("photo_at_gate", r.message.file_url);
									frappe.show_alert({
										message: __("Photo captured and attached."),
										indicator: "green",
									});
									capture_dialog.hide();
								}
							},
						});
					};

					reader.readAsDataURL(new File([blob], file_name, { type: "image/png" }));
				}, "image/png");
			},
		});

		capture_dialog.show();

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
				const video = document.getElementById(video_id);
				if (!video) {
					stream.getTracks().forEach((track) => track.stop());
					frappe.msgprint(__("Video element not found. Please try again."));
					return;
				}

				video.srcObject = stream;
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

frappe.ui.form.on("Security Item Verify", {
	item_verified(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row) return;

		if (row.item_verified && !row.security_remarks) {
			frappe.model.set_value(cdt, cdn, "security_remarks", __("Verified at gate"));
			return;
		}

		if (!row.item_verified && row.security_remarks === __("Verified at gate")) {
			frappe.model.set_value(cdt, cdn, "security_remarks", __("Pending security verification"));
		}
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
				const video = document.getElementById("item-capture-video");
				const canvas = document.createElement("canvas");
				canvas.width = video.videoWidth;
				canvas.height = video.videoHeight;
				const context = canvas.getContext("2d");
				context.drawImage(video, 0, 0, canvas.width, canvas.height);

				canvas.toBlob((blob) => {
					const file_name = `item_photo_${frappe.datetime.now_datetime().replace(/[: -]/g, "_")}.png`;
					const reader = new FileReader();
					reader.onload = (e) => {
						const upload_args = {
							from_form: 1,
							fieldname: "item_image",
							filedata: e.target.result.split(",")[1],
							filename: file_name,
						};

						if (cdn && !cdn.startsWith("new-")) {
							upload_args.doctype = cdt;
							upload_args.docname = cdn;
						}

						frappe.call({
							method: "frappe.handler.upload_file",
							args: upload_args,
							callback: (r) => {
								if (r.message && r.message.file_url) {
									frappe.model.set_value(cdt, cdn, "item_image", r.message.file_url);
									frappe.show_alert({
										message: __("Item photo captured and attached."),
										indicator: "green",
									});
									capture_dialog.hide();
								}
							},
							error: () => {
								frappe.msgprint(__("Could not upload item photo."));
							},
						});
					};

					reader.readAsDataURL(new File([blob], file_name, { type: "image/png" }));
				}, "image/png");
			},
		});

		capture_dialog.show();

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
				const video = document.getElementById(video_id);
				if (!video) {
					stream.getTracks().forEach((track) => track.stop());
					frappe.msgprint(__("Video element not found. Please try again."));
					return;
				}

				video.srcObject = stream;
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

function apply_security_log_ui(frm) {
	[
		"badge_number",
		"visitor_name",
		"visitor_company",
		"id_proof_number",
		"id_last_4_digits",
		"id_proof_scan",
		"visitor_photo",
		"qr_code_scanned",
		"gate_auto_assigned",
		"all_items_confirmed",
		"verification_duration",
	].forEach((fieldname) => frm.set_df_property(fieldname, "read_only", 1));

	const has_pass = !!frm.doc.visitor_pass;
	const is_check_in = frm.doc.event_type === "Check-In";
	const is_check_out = frm.doc.event_type === "Check-Out";
	const is_exception_flow = !!frm.doc.manual_override || ["High", "Critical"].includes(frm.doc.alert_level);
	const has_items = !!(frm.doc.items_verification && frm.doc.items_verification.length);
	const show_items = is_check_in || has_items;
	const show_gate_photo = ["Check-In", "Alert", "Gate Transfer", "Badge Collected"].includes(frm.doc.event_type);

	frm.toggle_display("check_in_date_time", is_check_in || (!frm.is_new() && !!frm.doc.check_in_date_time));
	frm.toggle_display("check_out_date_time", is_check_out || (!frm.is_new() && !!frm.doc.check_out_date_time));
	frm.toggle_reqd("check_in_date_time", is_check_in);
	frm.toggle_reqd("check_out_date_time", is_check_out);

	frm.toggle_display("visitor_photo", false);
	frm.toggle_display("id_proof_scan", false);
	frm.toggle_display(["section_break_identity_comparison", "identity_comparison_html"], has_pass);
	frm.toggle_display(["photo_at_gate", "capture_photo"], show_gate_photo);
	frm.toggle_display(["id_proof_match", "pass_photo_match", "verification_notes"], is_check_in);
	frm.toggle_display(["alert_level", "manual_override", "exception_reason", "verification_duration"], has_pass);
	frm.toggle_display(["section_break_items", "items_verification", "all_items_confirmed"], show_items);
	frm.toggle_reqd("photo_at_gate", is_check_in && !frm.doc.manual_override);
	frm.toggle_reqd("id_proof_match", is_check_in && !frm.doc.manual_override);
	frm.toggle_reqd("pass_photo_match", is_check_in && !frm.doc.manual_override);
	frm.toggle_reqd("exception_reason", is_exception_flow);

	render_identity_comparison(frm);
	set_security_log_intro(frm, is_check_in, is_check_out);
	set_security_log_headline(frm);
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
					? `<img src="${imageUrl}" alt="${title}" style="width: 100%; height: 148px; object-fit: cover; border-radius: 10px; border: 1px solid #dbe3ea; background: #f8fafc;">`
					: `<div style="height: 148px; border-radius: 10px; border: 1px dashed #b8c4d0; background: #f8fafc; display: flex; align-items: center; justify-content: center; color: #7b8794; font-size: 12px; text-align: center; padding: 12px;">${__("No image available")}</div>`
			}
			<div style="margin-top: 8px; font-size: 11px; line-height: 1.4; color: #52606d;">${caption}</div>
		</div>
	`;
}

function get_identity_status_html(frm) {
	if (!frm.doc.photo_at_gate) {
		return `<div style="display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; background: #fff4d6; color: #8d5d00; font-size: 11px; font-weight: 700;">${__("Awaiting gate capture")}</div>`;
	}

	if (frm.doc.id_proof_match && frm.doc.pass_photo_match) {
		return `<div style="display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; background: #d9f3e4; color: #0d6b3e; font-size: 11px; font-weight: 700;">${__("Verified for badge issue")}</div>`;
	}

	return `<div style="display: inline-flex; align-items: center; gap: 6px; padding: 4px 10px; border-radius: 999px; background: #e8f1fb; color: #1f4f82; font-size: 11px; font-weight: 700;">${__("Review and confirm both matches")}</div>`;
}

function set_security_log_intro(frm, is_check_in, is_check_out) {
	if (!frm.doc.visitor_pass) {
		frm.set_intro(
			__("Scan the QR code or select a Visitor Pass to begin gate verification."),
			"blue"
		);
		return;
	}

	if (is_check_in) {
		frm.set_intro(
			__(
				"Compare the ID proof, pass photo, and live gate capture, then confirm both matches before saving the check-in and printing the badge."
			),
			"green"
		);
		return;
	}

	if (is_check_out) {
		frm.set_intro(
			__("Confirm the visitor identity and record the exit cleanly before saving the check-out."),
			"orange"
		);
		return;
	}

	frm.set_intro(
		__("Record the gate event with concise notes and supporting capture if needed."),
		"blue"
	);
}

function set_security_log_headline(frm) {
	if (!frm.dashboard) return;

	const headline = frm.doc.visitor_name
		? __("{0} | {1}", [frm.doc.event_type || __("Gate Event"), frm.doc.visitor_name])
		: frm.doc.event_type || __("Gate Event");

	frm.dashboard.clear_headline();
	frm.dashboard.set_headline(headline, get_security_event_color(frm.doc.event_type));
}

function get_security_event_color(event_type) {
	if (event_type === "Check-In") return "green";
	if (event_type === "Check-Out") return "orange";
	if (event_type === "Alert") return "red";
	if (event_type === "Gate Transfer") return "blue";
	return "gray";
}
