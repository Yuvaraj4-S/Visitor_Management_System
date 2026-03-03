// Copyright (c) 2026, Harthesh and contributors
// For license information, please see license.txt

// frappe.ui.form.on("Security Log", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on('Security Log', {
    setup: function (frm) {
        frm.add_fetch('visitor_pass', 'visitor_full_name', 'visitor_name');
        frm.add_fetch('visitor_pass', 'badge_number', 'badge_number');
        frm.add_fetch('visitor_pass', 'visitor_photo', 'visitor_photo');
    },

    qr_code_value: function (frm) {
        frappe.require('/assets/visitormanagement/js/libs/html5-qrcode.min.js', function () {
            if (typeof Html5Qrcode === "undefined") {
                frappe.msgprint({
                    title: __('Error'),
                    message: __('QR Scanner library not loaded correctly. Please refresh and try again.'),
                    indicator: 'red'
                });
                return;
            }

            const scanner_dialog = new frappe.ui.Dialog({
                title: __('Scan QR Code'),
                fields: [
                    {
                        fieldname: 'qr_scanner_html',
                        fieldtype: 'HTML'
                    }
                ],
                primary_action_label: __('Stop Scanner'),
                primary_action() {
                    scanner_dialog.hide();
                }
            });

            scanner_dialog.show();

            const scanner_id = 'qr-reader';
            const $container = scanner_dialog.get_field('qr_scanner_html').$wrapper;
            $container.html(`
                <div id="${scanner_id}" style="width: 100%; min-height: 300px; border: 1px solid #ddd; border-radius: 8px; background: #000; position: relative;">
                    <div style="position: absolute; top: 50%; left: 50%; transform: translate(-50%, -50%); color: white; font-family: sans-serif;">
                        ${__('Initializing Camera...')}
                    </div>
                </div>
                <div id="qr-reader-results" style="margin-top: 10px; text-align: center; font-weight: bold; color: #555;"></div>
            `);

            let html5QrCode;

            function stop_scanner() {
                if (html5QrCode && html5QrCode.isScanning) {
                    html5QrCode.stop().then(() => {
                        console.log("QR Scanner stopped.");
                    }).catch((err) => {
                        console.warn("Failed to stop QR Scanner", err);
                    });
                }
            }

            // Small delay to ensure the dialog DOM is fully ready
            setTimeout(() => {
                try {
                    html5QrCode = new Html5Qrcode(scanner_id);

                    const qrCodeSuccessCallback = (decodedText, decodedResult) => {
                        let vp_id = decodedText;
                        if (decodedText.includes('|') && decodedText.includes(':')) {
                            // Parse "PASS:VP-2026-00001|VISITOR:John..." format
                            const parts = decodedText.split('|');
                            for (let part of parts) {
                                if (part.startsWith('PASS:')) {
                                    vp_id = part.split(':')[1].trim();
                                    break;
                                }
                            }
                        }

                        frm.set_value('visitor_pass', vp_id);
                        frm.set_value('qr_code_scanned', 1);
                        frm.refresh_field('visitor_pass');

                        // Small delay to allow the Link field to process the change
                        setTimeout(() => {
                            frm.trigger('visitor_pass');
                        }, 300);

                        frappe.show_alert({
                            message: __('QR Code scanned: {0}', [vp_id]),
                            indicator: 'green'
                        });

                        scanner_dialog.hide();

                        // Automatically trigger photo capture after QR scan
                        setTimeout(() => {
                            if (!frm.doc.photo_at_gate) {
                                frm.trigger('capture_photo');
                            }
                        }, 1000);
                    };

                    const config = {
                        fps: 10,
                        qrbox: { width: 250, height: 250 },
                        aspectRatio: 1.0
                    };

                    // Try to start with environment camera (back camera)
                    html5QrCode.start(
                        { facingMode: "environment" },
                        config,
                        qrCodeSuccessCallback
                    ).catch(err => {
                        console.error("Error starting QR Scanner", err);
                        // Fallback to any available camera if environment fails
                        html5QrCode.start(
                            { facingMode: "user" },
                            config,
                            qrCodeSuccessCallback
                        ).catch(err2 => {
                            frappe.msgprint({
                                title: __('Camera Error'),
                                message: __('Could not start camera. Error: {0}', [err2]),
                                indicator: 'red'
                            });
                            scanner_dialog.hide();
                        });
                    });
                } catch (e) {
                    console.error("Scanner initialization failed", e);
                    frappe.msgprint(__('Failed to initialize scanner: {0}', [e]));
                }
            }, 500);

            scanner_dialog.on_hide = () => {
                stop_scanner();
            };
        });
    },

    visitor_pass: function (frm) {
        if (frm.doc.visitor_pass) {
            // First, fetch basic details
            frappe.db.get_value('Visitor Pass', frm.doc.visitor_pass,
                ['visitor_photo', 'visitor_full_name', 'badge_number', 'id_proof_type', 'visitor_type'], (r) => {
                    if (r) {
                        if (r.visitor_photo) frm.set_value('visitor_photo', r.visitor_photo);
                        frm.set_value('visitor_name', r.visitor_full_name);
                        frm.set_value('badge_number', r.badge_number);
                        if (r.id_proof_type && !frm.doc.id_proof_type_verified) {
                            frm.set_value('id_proof_type_verified', r.id_proof_type);
                        }

                        // Set default gate and check-in time if it's a new log
                        if (frm.is_new()) {
                            if (!frm.doc.event_type) frm.set_value('event_type', 'Check-In');
                            if (frm.doc.event_type === 'Check-In' && !frm.doc.check_in_date_time) {
                                frm.set_value('check_in_date_time', frappe.datetime.now_datetime());
                            }

                            if (!frm.doc.gate_name) {
                                const gate_rules = {
                                    'VIP': 'VIP Entrance',
                                    'Supplier': 'Loading Dock',
                                    'Contractor': 'Back Gate',
                                    'Candidate': 'Main Gate',
                                    'Customer': 'Main Gate',
                                };
                                let gate = gate_rules[r.visitor_type] || 'Main Gate';
                                frm.set_value('gate_name', gate);
                            }
                        }
                    }
                });

            // Second, fetch and populate visitor items if it's a new log
            if (frm.is_new() && (!frm.doc.items_verification || frm.doc.items_verification.length === 0)) {
                frappe.model.with_doc('Visitor Pass', frm.doc.visitor_pass, function () {
                    let vp = frappe.model.get_doc('Visitor Pass', frm.doc.visitor_pass);
                    if (vp.visitor_items && vp.visitor_items.length > 0) {
                        frm.clear_table('items_verification');
                        vp.visitor_items.forEach(item => {
                            let row = frm.add_child('items_verification');
                            row.item_name = item.item_name;
                            row.item_category = item.item_category;
                            row.quantity_declared = item.quantity;
                            row.uom = item.unit_of_measure;
                            row.serial__asset_number = item.serial_number;
                            row.quantity_found = item.quantity; // Default to declared
                            row.item_verified = 0;
                            row.visitor_item_row_name = item.name;
                        });
                        frm.refresh_field('items_verification');
                    }
                });
            }
        }
    },

    capture_photo: function (frm) {
        let capture_dialog = new frappe.ui.Dialog({
            title: __('Capture Photo'),
            fields: [
                {
                    fieldname: 'camera_html',
                    fieldtype: 'HTML'
                }
            ],
            primary_action_label: __('Capture'),
            primary_action() {
                const video = document.getElementById('capture-video');
                const canvas = document.createElement('canvas');
                canvas.width = video.videoWidth;
                canvas.height = video.videoHeight;
                const context = canvas.getContext('2d');
                context.drawImage(video, 0, 0, canvas.width, canvas.height);

                canvas.toBlob((blob) => {
                    const file_name = `gate_photo_${frappe.datetime.now_datetime().replace(/[: -]/g, '_')}.png`;
                    const file = new File([blob], file_name, { type: "image/png" });

                    // Upload to Frappe
                    const upload = new frappe.upload.Uploader({
                        args: {
                            from_form: 1,
                            doctype: frm.doc.doctype,
                            docname: frm.doc.name,
                            fieldname: 'photo_at_gate'
                        },
                        files: [file],
                        callback: (attachment) => {
                            frm.set_value('photo_at_gate', attachment.file_url);
                            frappe.show_alert({
                                message: __('Photo captured and attached.'),
                                indicator: 'green'
                            });
                            capture_dialog.hide();
                        }
                    });
                }, 'image/png');
            }
        });

        capture_dialog.show();

        const video_id = 'capture-video';
        capture_dialog.get_field('camera_html').$wrapper.html(`
            <div style="width: 100%; background: #000; border-radius: 8px; overflow: hidden;">
                <video id="${video_id}" width="100%" autoplay playsinline></video>
            </div>
        `);

        if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
            navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } })
                .then(stream => {
                    const video = document.getElementById(video_id);
                    if (video) {
                        video.srcObject = stream;
                        capture_dialog.on_hide = () => {
                            stream.getTracks().forEach(track => track.stop());
                        };
                    } else {
                        stream.getTracks().forEach(track => track.stop());
                        frappe.msgprint(__('Video element not found. Please try again.'));
                    }
                })
                .catch(err => {
                    frappe.msgprint(__('Error accessing camera: {0}', [err]));
                    capture_dialog.hide();
                });
        } else {
            frappe.msgprint(__('Camera not supported on this browser.'));
            capture_dialog.hide();
        }
    }
});

frappe.ui.form.on('Security Item Verify', {
    capture_item_image: function (frm, cdt, cdn) {
        const row = locals[cdt][cdn];

        let capture_dialog = new frappe.ui.Dialog({
            title: __('Capture Item Photo'),
            fields: [
                {
                    fieldname: 'camera_html',
                    fieldtype: 'HTML'
                }
            ],
            primary_action_label: __('Capture'),
            primary_action() {
                const video = document.getElementById('item-capture-video');
                const canvas = document.createElement('canvas');
                canvas.width = video.videoWidth;
                canvas.height = video.videoHeight;
                const context = canvas.getContext('2d');
                context.drawImage(video, 0, 0, canvas.width, canvas.height);

                canvas.toBlob((blob) => {
                    const file_name = `item_photo_${frappe.datetime.now_datetime().replace(/[: -]/g, '_')}.png`;
                    const file = new File([blob], file_name, { type: "image/png" });

                    // Upload to Frappe
                    const upload = new frappe.upload.Uploader({
                        args: {
                            from_form: 1,
                            doctype: cdt,
                            docname: cdn,
                            fieldname: 'item_image'
                        },
                        files: [file],
                        callback: (attachment) => {
                            frappe.model.set_value(cdt, cdn, 'item_image', attachment.file_url);
                            frappe.show_alert({
                                message: __('Item photo captured and attached.'),
                                indicator: 'green'
                            });
                            capture_dialog.hide();
                        }
                    });
                }, 'image/png');
            }
        });

        capture_dialog.show();

        const video_id = 'item-capture-video';
        capture_dialog.get_field('camera_html').$wrapper.html(`
            <div style="width: 100%; background: #000; border-radius: 8px; overflow: hidden;">
                <video id="${video_id}" width="100%" autoplay playsinline></video>
            </div>
        `);

        if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
            navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } })
                .then(stream => {
                    const video = document.getElementById(video_id);
                    if (video) {
                        video.srcObject = stream;
                        capture_dialog.on_hide = () => {
                            stream.getTracks().forEach(track => track.stop());
                        };
                    } else {
                        stream.getTracks().forEach(track => track.stop());
                        frappe.msgprint(__('Video element not found. Please try again.'));
                    }
                })
                .catch(err => {
                    frappe.msgprint(__('Error accessing camera: {0}', [err]));
                    capture_dialog.hide();
                });
        } else {
            frappe.msgprint(__('Camera not supported on this browser.'));
            capture_dialog.hide();
        }
    }
});