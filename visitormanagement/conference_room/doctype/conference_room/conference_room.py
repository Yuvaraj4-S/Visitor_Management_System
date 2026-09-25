# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_time


class ConferenceRoom(Document):

	def validate(self):
		if self.available_from and self.available_to:
			# get_time() on both sides, never a raw comparison: a Time field can
			# reach validate() as a string, and the Desk sends a single-digit hour
			# without a leading zero. "9:00:00" >= "17:00:00" is True as strings
			# ('9' > '1'), so a room open 09:00-17:00 was rejected as "'Available
			# From' must be before 'Available To'". A room opening before 10:00
			# therefore could not be re-saved at all — even without touching the
			# time fields.
			# conference_room_booking.py:87 already compares this way; matched here.
			if get_time(self.available_from) >= get_time(self.available_to):
				frappe.throw(_("'Available From' must be before 'Available To'."))

		if self.capacity is not None and self.capacity < 1:
			frappe.throw(_("Seating Capacity must be at least 1."))

		if self.min_booking_minutes and self.min_booking_minutes < 15:
			frappe.throw(_("Minimum booking duration must be at least 15 minutes."))
