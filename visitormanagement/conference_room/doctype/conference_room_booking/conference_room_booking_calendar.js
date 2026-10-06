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
		// "title" is the room and the meeting ("Board Room · Busy"), built by
		// get_booking_events: `meeting_title` alone did not say which room an
		// event was in, so the calendar could not answer "is this room free?".
		title: "title",
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
	// FullCalendar options, merged over the Frappe calendar's own.
	//
	// A booking the viewer may not read arrives as the room, the time and "Busy"
	// — without its name (get_booking_events). There is no record for this viewer
	// behind such an event, so it must not behave like one: the stock click
	// handler opened the booking's form, which answered with a permission error,
	// and a "Busy" event could be picked up and dropped on another time.
	options: {
		eventClick(info) {
			if (!frappe.model.can_read("Conference Room Booking")) {
				return;
			}
			const name = booking_name(info.event);
			if (name) {
				frappe.set_route("Form", "Conference Room Booking", name);
				return;
			}
			frappe.show_alert({
				message: __(
					"This slot is taken by someone else's booking. Its details are not shown to you."
				),
				indicator: "blue",
			});
		},
		// Asked for every drag and resize; false keeps the event where it is.
		eventAllow(span, event) {
			return Boolean(booking_name(event));
		},
	},
};

// The booking behind a calendar event, or null for one shown only as "Busy".
// Read from the row itself (FullCalendar keeps its columns in `extendedProps`),
// not from `event.id`: that is always a string, and reads "null" for a row
// that has no name.
function booking_name(event) {
	return (event && event.extendedProps && event.extendedProps.name) || null;
}
