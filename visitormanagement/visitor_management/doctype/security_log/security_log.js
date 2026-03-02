// Copyright (c) 2026, Harthesh and contributors
// For license information, please see license.txt

// frappe.ui.form.on("Security Log", {
// 	refresh(frm) {

// 	},
// });
frappe.ui.form.on('Security Log', {
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
                        frm.set_value('visitor_pass', decodedText);
                        frm.set_value('qr_code_scanned', 1);
                        frm.trigger('visitor_pass');

                        frappe.show_alert({
                            message: __('QR Code scanned: {0}. Now taking photo...', [decodedText]),
                            indicator: 'green'
                        });

                        scanner_dialog.hide();

                        // Automatically trigger photo capture after QR scan
                        setTimeout(() => {
                            frm.trigger('capture_photo');
                        }, 600);
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

        const video = document.getElementById(video_id);

        if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
            navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } })
                .then(stream => {
                    video.srcObject = stream;
                    capture_dialog.on_hide = () => {
                        stream.getTracks().forEach(track => track.stop());
                    };
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