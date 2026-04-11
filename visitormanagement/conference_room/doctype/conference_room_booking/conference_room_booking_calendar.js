frappe.views.calendar["Conference Room Booking"] = {
	field_map: {
		start: "start_time",
		end: "end_time",
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
