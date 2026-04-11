# Copyright (c) 2026, Harthesh and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class ConferenceRoom(Document):

	def validate(self):
		if self.available_from and self.available_to:
			if self.available_from >= self.available_to:
				frappe.throw(_("'Available From' must be before 'Available To'."))

		if self.capacity is not None and self.capacity < 1:
			frappe.throw(_("Seating Capacity must be at least 1."))

		if self.min_booking_minutes and self.min_booking_minutes < 15:
			frappe.throw(_("Minimum booking duration must be at least 15 minutes."))
