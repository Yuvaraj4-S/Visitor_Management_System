# Copyright (c) 2026, Harthesh and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class SupplierVisit(Document):
	def validate(self):
		if self.visitor_pass:
			visitor_pass = frappe.get_doc("Visitor Pass", self.visitor_pass)
			self.supplier_visit_mode = self.supplier_visit_mode or visitor_pass.supplier_visit_mode or "Delivery"

			for fieldname in [
				"purchase_order",
				"delivery_note",
				"goods_description",
				"driver_id_number",
				"dock__bay_assigned",
				"store_officer",
				"goods_received_by",
				"meeting_subject",
				"meeting_start_time",
				"meeting_end_time",
				"meeting_room",
				"attendees",
				"refreshments_required",
				"refreshment_notes",
				"presentation_material",
				"nda_required",
				"documents_shared",
			]:
				if not getattr(self, fieldname, None):
					setattr(
						self,
						fieldname,
						getattr(visitor_pass, fieldname.replace("dock__bay_assigned", "dock_bay_assigned"), None),
					)
