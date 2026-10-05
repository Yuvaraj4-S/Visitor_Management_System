frappe.ui.form.on("Walk In Visitor Request", {
	refresh(frm) {
		// Mask ID proof number on saved forms
		if (!frm.is_new() && frm.doc.id_proof_number && !frm.doc.id_proof_number.includes("X")) {
			const val = frm.doc.id_proof_number;
			const chars = [];
			for (let i = 0; i < val.length; i++) {
				if (/[A-Za-z0-9]/.test(val[i])) chars.push(i);
			}
			if (chars.length > 4) {
				const visible = new Set(chars.slice(-4));
				let masked = "";
				for (let i = 0; i < val.length; i++) {
					if (/[A-Za-z0-9]/.test(val[i])) {
						masked += visible.has(i) ? val[i] : "X";
					} else {
						masked += val[i];
					}
				}
				frm.doc.id_proof_number = masked;
				frm.refresh_field("id_proof_number");
			}
		}

		// Hide the default attach widget for visitor_photo — force camera-only
		_setup_camera_photo(frm);

		// Show Approve / Reject buttons for the host or System Manager
		if (frm.doc.status === "Pending Approval" && !frm.is_new()) {
			const can_approve = _can_current_user_approve(frm);
			if (can_approve) {
				frm.add_custom_button(
					__("Approve"),
					() => _approve_request(frm),
					__("Action")
				);
				frm.add_custom_button(
					__("Reject"),
					() => _reject_request(frm),
					__("Action")
				);
			}
		}

		// Show link to the generated Visitor Pass
		if (frm.doc.visitor_pass) {
			frm.set_intro(
				__("Visitor Pass {0} has been created.", [
					`<a href="/app/visitor-pass/${frm.doc.visitor_pass}">${frm.doc.visitor_pass}</a>`,
				]),
				"green"
			);
		}

		// Status indicators
		if (frm.doc.status === "Pending Approval") {
			frm.set_intro(
				__("Waiting for approval from the host ({0}).", [frm.doc.person_to_visit]),
				"orange"
			);
		} else if (frm.doc.status === "Rejected") {
			frm.set_intro(
				__("This request was rejected. Reason: {0}", [frm.doc.rejection_reason || "Not specified"]),
				"red"
			);
		}
	},

	person_to_visit(frm) {
		if (frm.doc.person_to_visit) {
			frappe.db.get_value("Employee", frm.doc.person_to_visit, "department", (r) => {
				if (r) frm.set_value("host_department", r.department);
			});
		}
	},
});


// ─────────────────────────────────────────────────────────
// CAMERA CAPTURE FOR VISITOR PHOTO
// ─────────────────────────────────────────────────────────

function _setup_camera_photo(frm) {
	const editable = frm.doc.status === "Pending Approval" || frm.is_new();

	if (editable && !frm.doc.visitor_photo) {
		// Add "Capture Photo" button
		frm.add_custom_button(__("Capture Visitor Photo"), () => _open_camera(frm));
	}

	// Show preview + retake if photo already captured
	if (frm.doc.visitor_photo) {
		const wrapper = frm.get_field("visitor_photo").$wrapper;
		// Add a retake button below the image if still editable
		if (editable && !wrapper.find(".btn-retake-photo").length) {
			wrapper.append(
				`<button class="btn btn-xs btn-default btn-retake-photo" style="margin-top:5px;">
					${__("Retake Photo")}
				</button>`
			);
			wrapper.find(".btn-retake-photo").on("click", () => _open_camera(frm));
		}
	}
}

function _open_camera(frm) {
	// Document must be saved before we can attach files to it
	if (frm.is_new()) {
		frappe.msgprint(__("Please save the form first, then capture the photo."));
		return;
	}

	const video_id = "wvr-capture-video";
	const capture_dialog = new frappe.ui.Dialog({
		title: __("Capture Visitor Photo"),
		fields: [
			{
				fieldname: "camera_html",
				fieldtype: "HTML",
			},
		],
		primary_action_label: __("Capture"),
		primary_action() {
			const video = document.getElementById(video_id);
			if (!video || !video.videoWidth || !video.videoHeight) {
				frappe.msgprint(__("Camera is still loading. Wait a moment and try again."));
				return;
			}

			const canvas = document.createElement("canvas");
			canvas.width = video.videoWidth;
			canvas.height = video.videoHeight;
			const ctx = canvas.getContext("2d");
			ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

			canvas.toBlob((blob) => {
				if (!blob) {
					frappe.msgprint(__("Could not capture photo. Please try again."));
					return;
				}

				const ts = frappe.datetime.now_datetime().replace(/[: -]/g, "_");
				const file_name = `visitor_photo_${ts}.png`;
				const file = new File([blob], file_name, { type: "image/png" });

				_upload_captured_image({
					file,
					doctype: frm.doctype,
					docname: frm.doc.name,
					fieldname: "visitor_photo",
				})
					.then((file_doc) => {
						frm.set_value("visitor_photo", file_doc.file_url).then(() => {
							frm.refresh_field("visitor_photo");
							frappe.show_alert({
								message: __("Visitor photo captured."),
								indicator: "green",
							});
							capture_dialog.hide();
						});
					})
					.catch(() => {
						frappe.msgprint(__("Could not upload photo. Please try again."));
					});
			}, "image/png");
		},
	});

	capture_dialog.show();
	capture_dialog.get_primary_btn().prop("disabled", true);

	capture_dialog.get_field("camera_html").$wrapper.html(`
		<div style="width:100%; background:#000; border-radius:8px; overflow:hidden;">
			<video id="${video_id}" width="100%" autoplay playsinline></video>
		</div>
	`);

	if (!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia)) {
		frappe.msgprint(__("Camera is not supported on this browser."));
		capture_dialog.hide();
		return;
	}

	// Use front camera (user-facing) for visitor face photo
	navigator.mediaDevices
		.getUserMedia({ video: { facingMode: "user" } })
		.then((stream) => {
			const video = document.getElementById(video_id);
			if (!video) {
				stream.getTracks().forEach((t) => t.stop());
				return;
			}
			video.srcObject = stream;
			video.onloadedmetadata = () => {
				capture_dialog.get_primary_btn().prop("disabled", false);
			};
			capture_dialog.on_hide = () => {
				stream.getTracks().forEach((t) => t.stop());
			};
		})
		.catch((err) => {
			frappe.msgprint(__("Error accessing camera: {0}", [err]));
			capture_dialog.hide();
		});
}

function _upload_captured_image({ file, doctype, docname, fieldname }) {
	return new Promise((resolve, reject) => {
		const xhr = new XMLHttpRequest();
		const form_data = new FormData();

		form_data.append("file", file, file.name);
		form_data.append("is_private", 0);
		form_data.append("doctype", doctype);
		form_data.append("docname", docname);
		form_data.append("fieldname", fieldname);

		xhr.open("POST", "/api/method/upload_file", true);
		xhr.setRequestHeader("Accept", "application/json");
		xhr.setRequestHeader("X-Frappe-CSRF-Token", frappe.csrf_token);

		xhr.onreadystatechange = () => {
			if (xhr.readyState !== XMLHttpRequest.DONE) return;
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
				console.error("Failed to parse upload response", e);
			}
			reject(xhr.responseText);
		};

		xhr.onerror = () => reject(xhr.responseText);
		xhr.send(form_data);
	});
}


// ─────────────────────────────────────────────────────────
// APPROVAL / REJECTION
// ─────────────────────────────────────────────────────────

function _can_current_user_approve(frm) {
	// System Manager can always approve
	if (frappe.user_roles.includes("System Manager")) return true;

	// Check if current user is the host employee
	if (!frm.doc.person_to_visit) return false;

	// We check by fetching the employee linked to current user
	// This is done synchronously via the already-loaded user info
	return frappe.boot.employee_id === frm.doc.person_to_visit;
}

function _approve_request(frm) {
	frappe.confirm(
		__("Are you sure you want to approve this walk-in visitor request?"),
		() => {
			frappe.call({
				method:
					"visitormanagement.visitor_management.doctype.walk_in_visitor_request.walk_in_visitor_request.approve_request",
				args: { request_name: frm.doc.name },
				freeze: true,
				freeze_message: __("Approving and generating Visitor Pass..."),
				callback(r) {
					if (r.message) {
						frm.reload_doc();
					}
				},
			});
		}
	);
}

function _reject_request(frm) {
	frappe.prompt(
		{
			fieldname: "rejection_reason",
			fieldtype: "Small Text",
			label: __("Rejection Reason"),
			reqd: 1,
		},
		(values) => {
			frappe.call({
				method:
					"visitormanagement.visitor_management.doctype.walk_in_visitor_request.walk_in_visitor_request.reject_request",
				args: {
					request_name: frm.doc.name,
					rejection_reason: values.rejection_reason,
				},
				freeze: true,
				freeze_message: __("Rejecting request..."),
				callback(r) {
					if (r.message) {
						frm.reload_doc();
					}
				},
			});
		},
		__("Reject Walk-In Request"),
		__("Reject")
	);
}
