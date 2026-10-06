frappe.views.calendar["Conference Room Booking"] = {
	field_map: {
		// "start"/"end", not "start_time"/"end_time". get_booking_events returns
		// TIMESTAMP(booking_date, start_time) AS `start` — a full datetime, because
		// a Time on its own has no day to place it on. The map asked for
		// `start_time`, which is not in the result set at all, so every booking
		// arrived with no date and the calendar dropped them all on today at the
		// moment the view was opened. Reported as "every booking rendered on
		// today's square at 4:14 PM".
		start: "start",
		end: "end",
		id: "name",
		title: "meeting_title",
		allDay: "allDay",
		status: "status",
	},
	get_events_method:
		"visitormanagement.conference_room.doctype.conference_room_booking.conference_room_booking.get_booking_events",
	filters: [
		{
			fieldtype: "Link",
			fieldname: "conference_room",
			options: "Conference Room",
			label: __("Conference Room"),
		},
	],
};
