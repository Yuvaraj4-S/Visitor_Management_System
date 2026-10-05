// For license information, please see license.txt

frappe.ui.form.on("Conference Room Booking", {
	setup(frm) {
		frm.set_query("conference_room", () => ({
			filters: { is_active: 1 },
		}));
		frm.set_query("booked_by", () => ({
			filters: { status: "Active" },
		}));
	},

	refresh(frm) {
		if (frm.doc.docstatus === 0) {
			frm.add_custom_button(__("Find Available Rooms"), () => {
				find_available_rooms(frm);
			});
		}
		if (frm.doc.conference_room && frm.doc.booking_date) {
			frm.add_custom_button(__("View Room Schedule"), () => {
				view_room_schedule(frm);
			});
		}
	},

	meeting_type(frm) {
		if (frm.doc.meeting_type !== "Internal") {
			frm.set_value({
				room_cleaning_required: 1,
				water_required: 1,
				coffee_tea_required: 1,
			});
		} else {
			frm.set_value({
				room_cleaning_required: 0,
				water_required: 0,
				coffee_tea_required: 0,
				snacks_required: 0,
			});
		}
	},

	start_time(frm) {
		calculate_duration(frm);
	},

	end_time(frm) {
		calculate_duration(frm);
	},

	expected_attendees(frm) {
		if (
			frm.doc.conference_room &&
			frm.doc.expected_attendees &&
			frm.doc.room_capacity
		) {
			if (frm.doc.expected_attendees > frm.doc.room_capacity) {
				frappe.msgprint({
					title: __("Capacity Warning"),
					message: __(
						"Expected attendees ({0}) exceeds room capacity ({1}). Consider a larger room.",
						[frm.doc.expected_attendees, frm.doc.room_capacity]
					),
					indicator: "orange",
				});
			}
		}
	},
});

function calculate_duration(frm) {
	if (frm.doc.start_time && frm.doc.end_time && frm.doc.booking_date) {
		let start = moment(frm.doc.booking_date + " " + frm.doc.start_time);
		let end = moment(frm.doc.booking_date + " " + frm.doc.end_time);
		if (end.isAfter(start)) {
			let hours = end.diff(start, "minutes") / 60;
			frm.set_value(
				"duration_hours",
				Math.round(hours * 100) / 100
			);
		}
	}
}

function find_available_rooms(frm) {
	let d = new frappe.ui.Dialog({
		title: __("Find Available Rooms"),
		fields: [
			{
				fieldname: "booking_date",
				fieldtype: "Date",
				label: "Date",
				default:
					frm.doc.booking_date || frappe.datetime.get_today(),
				reqd: 1,
			},
			{
				fieldname: "start_time",
				fieldtype: "Time",
				label: "Start Time",
				default: frm.doc.start_time,
				reqd: 1,
			},
			{
				fieldname: "end_time",
				fieldtype: "Time",
				label: "End Time",
				default: frm.doc.end_time,
				reqd: 1,
			},
			{
				fieldname: "min_capacity",
				fieldtype: "Int",
				label: "Minimum Capacity",
				default: frm.doc.expected_attendees || 0,
			},
			{ fieldtype: "Section Break" },
			{ fieldname: "results_html", fieldtype: "HTML" },
		],
		primary_action_label: __("Search"),
		primary_action(values) {
			frappe.call({
				method: "visitormanagement.conference_room.doctype.conference_room_booking.conference_room_booking.get_available_rooms",
				args: {
					booking_date: values.booking_date,
					start_time: values.start_time,
					end_time: values.end_time,
					min_capacity: values.min_capacity || 0,
					exclude_booking: frm.doc.name || "",
				},
				callback(r) {
					let html = "";
					if (r.message && r.message.length) {
						html =
							'<table class="table table-bordered"><thead><tr>' +
							"<th>Room</th><th>Capacity</th><th>Location</th><th>Type</th><th></th>" +
							"</tr></thead><tbody>";
						const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
						r.message.forEach((room) => {
							html +=
								"<tr>" +
								"<td>" + esc(room.room_name) + "</td>" +
								"<td>" + esc(room.capacity) + "</td>" +
								"<td>" + esc(room.location || "") + " " + esc(room.floor || "") + "</td>" +
								"<td>" + esc(room.room_type || "") + "</td>" +
								'<td><button class="btn btn-xs btn-primary select-room-btn" ' +
								'data-room="' + esc(room.name) + '" ' +
								'data-date="' + esc(values.booking_date) + '" ' +
								'data-start="' + esc(values.start_time) + '" ' +
								'data-end="' + esc(values.end_time) + '">Select</button></td>' +
								"</tr>";
						});
						html += "</tbody></table>";
					} else {
						html =
							'<p class="text-muted">No rooms available for the selected slot.</p>';
					}
					d.fields_dict.results_html.$wrapper.html(html);

					d.fields_dict.results_html.$wrapper
						.find(".select-room-btn")
						.on("click", function () {
							let btn = $(this);
							frm.set_value(
								"conference_room",
								btn.data("room")
							);
							frm.set_value(
								"booking_date",
								btn.data("date")
							);
							frm.set_value(
								"start_time",
								btn.data("start")
							);
							frm.set_value("end_time", btn.data("end"));
							d.hide();
						});
				},
			});
		},
	});
	d.show();
}

function view_room_schedule(frm) {
	frappe.call({
		method: "visitormanagement.conference_room.doctype.conference_room_booking.conference_room_booking.get_room_schedule",
		args: {
			conference_room: frm.doc.conference_room,
			booking_date: frm.doc.booking_date,
		},
		callback(r) {
			if (!r.message || !r.message.length) {
				frappe.msgprint(
					__("No other bookings for this room on this date.")
				);
				return;
			}
			const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
			let html =
				'<table class="table table-bordered"><thead><tr>' +
				"<th>Booking</th><th>Meeting</th><th>Time</th><th>Type</th><th>Status</th>" +
				"</tr></thead><tbody>";
			r.message.forEach((b) => {
				html +=
					"<tr>" +
					'<td><a href="/app/conference-room-booking/' +
					esc(b.name) +
					'">' +
					esc(b.name) +
					"</a></td>" +
					"<td>" + esc(b.meeting_title) + "</td>" +
					"<td>" + esc(b.start_time) + " - " + esc(b.end_time) + "</td>" +
					"<td>" + esc(b.meeting_type) + "</td>" +
					"<td>" + esc(b.status) + "</td></tr>";
			});
			html += "</tbody></table>";
			frappe.msgprint({
				title: __(
					esc(frm.doc.conference_room) + " - " + esc(frm.doc.booking_date)
				),
				message: html,
				wide: true,
			});
		},
	});
}

// ── Meeting purpose + smart room suggestions (Phase 8) ───────────────────────
const VMS_CRB = "visitormanagement.conference_room.doctype.conference_room_booking.conference_room_booking";
const VMS_INTERNAL_PURPOSES = ["Team Meeting", "Training", "Board Meeting"];

frappe.ui.form.on("Conference Room Booking", {
	refresh(frm) {
		vms_render_room_suggestions(frm);
	},
	meeting_purpose(frm) {
		if (frm.doc.meeting_purpose && frm.doc.meeting_type !== "Hybrid") {
			frm.set_value("meeting_type", VMS_INTERNAL_PURPOSES.includes(frm.doc.meeting_purpose) ? "Internal" : "External");
		}
		vms_render_room_suggestions(frm);
	},
	expected_attendees: vms_render_room_suggestions,
	booking_date: vms_render_room_suggestions,
	start_time: vms_render_room_suggestions,
	end_time: vms_render_room_suggestions,
});

function vms_render_room_suggestions(frm) {
	const field = frm.get_field("suggested_room");
	if (!field || frm.doc.docstatus !== 0) return;
	const d = frm.doc;
	if (!(d.meeting_purpose && d.booking_date && d.start_time && d.end_time)) {
		field.$wrapper.html(`<p class="text-muted small">${__("Pick a meeting purpose, date and time to see suggested rooms.")}</p>`);
		return;
	}
	frappe.call({
		method: `${VMS_CRB}.get_room_suggestions`,
		args: {
			meeting_purpose: d.meeting_purpose,
			booking_date: d.booking_date,
			start_time: d.start_time,
			end_time: d.end_time,
			attendee_count: d.expected_attendees || 0,
			exclude_booking: d.__islocal ? "" : d.name,
		},
		callback(r) {
			const res = r.message || {};
			const esc = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
			let html = `<p><b>${esc(res.suggestion || "")}</b></p>`;
			(res.available_rooms || []).forEach((room) => {
				const best = room.best_fit ? `<span class="indicator-pill green" style="margin-left:6px">${__("Best Fit")}</span>` : "";
				const chosen = room.name === d.conference_room ? `<span class="text-muted small"> (${__("selected")})</span>` : "";
				html += `<div style="display:flex;align-items:center;justify-content:space-between;padding:6px 0;border-bottom:1px solid var(--border-color)">
					<span>${esc(room.room_name)} — ${esc(room.capacity)} ${__("seats")} ${esc(room.location || "")} ${best}${chosen}</span>
					<button class="btn btn-xs btn-default vms-select-room" data-room="${esc(room.name)}">${__("Select")}</button>
				</div>`;
			});
			if ((res.unavailable_rooms || []).length) {
				html += `<p class="text-muted small" style="margin-top:8px">${__("Not suitable")}: ` +
					res.unavailable_rooms.map((u) => `${esc(u.room_name)} (${esc(u.reason)})`).join(", ") + "</p>";
			}
			field.$wrapper.html(html);
			field.$wrapper.find(".vms-select-room").on("click", (e) => {
				e.preventDefault();
				frm.set_value("conference_room", $(e.currentTarget).attr("data-room"));
			});
		},
	});
}
