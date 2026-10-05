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

		self.allowed_purposes = ", ".join(allowed_purposes(self)) or _("None")


# Meeting purposes each room usage flag allows (shared with Conference Room Booking).
PURPOSES_BY_USAGE = {
	"is_for_internal": ("Team Meeting", "Training", "Board Meeting"),
	"is_for_visitor": ("Visitor - Customer", "Visitor - Supplier", "Visitor - General"),
	"is_for_interview": ("Interview",),
}


def allowed_purposes(room):
	purposes = []
	for flag, names in PURPOSES_BY_USAGE.items():
		# rooms saved before these flags existed have them unset: treat as allowed
		if room.get(flag) is None or frappe.utils.cint(room.get(flag)):
			purposes.extend(names)
	return purposes
