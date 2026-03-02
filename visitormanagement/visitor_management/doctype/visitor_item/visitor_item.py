# Copyright (c) 2026, Harthesh and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class VisitorItem(Document):
    @property
    def qty(self):
        return self.quantity

    @property
    def uom(self):
        return self.unit_of_measure
