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
						r.message.forEach((room) => {
							html +=
								"<tr>" +
								"<td>" + room.room_name + "</td>" +
								"<td>" + room.capacity + "</td>" +
								"<td>" + (room.location || "") + " " + (room.floor || "") + "</td>" +
								"<td>" + (room.room_type || "") + "</td>" +
								'<td><button class="btn btn-xs btn-primary select-room-btn" ' +
								'data-room="' + room.name + '" ' +
								'data-date="' + values.booking_date + '" ' +
								'data-start="' + values.start_time + '" ' +
								'data-end="' + values.end_time + '">Select</button></td>' +
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
			let html =
				'<table class="table table-bordered"><thead><tr>' +
				"<th>Booking</th><th>Meeting</th><th>Time</th><th>Type</th><th>Status</th>" +
				"</tr></thead><tbody>";
			r.message.forEach((b) => {
				html +=
					"<tr>" +
					'<td><a href="/app/conference-room-booking/' +
					b.name +
					'">' +
					b.name +
					"</a></td>" +
					"<td>" + b.meeting_title + "</td>" +
					"<td>" + b.start_time + " - " + b.end_time + "</td>" +
					"<td>" + b.meeting_type + "</td>" +
					"<td>" + b.status + "</td></tr>";
			});
			html += "</tbody></table>";
			frappe.msgprint({
				title: __(
					frm.doc.conference_room + " - " + frm.doc.booking_date
				),
				message: html,
				wide: true,
			});
		},
	});
}
