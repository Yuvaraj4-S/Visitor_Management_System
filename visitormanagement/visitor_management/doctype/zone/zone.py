# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class Zone(Document):
	@staticmethod
	def is_visitor_type_allowed(zone, visitor_type, vip_category=None):
		"""Return True if `visitor_type` (and `vip_category`, when the matching rule
		row specifies one) is authorized for `zone` per its Zone Access Rule rows.
		A rule row with a blank `vip_category` matches any vip_category. Deny by
		default: no rows at all, or no matching row, returns False."""
		if not zone or not visitor_type:
			return False

		rules = frappe.get_all(
			"Zone Access Rule",
			filters={"parent": zone, "parenttype": "Zone", "visitor_type": visitor_type},
			fields=["vip_category"],
		)
		for rule in rules:
			if not rule.vip_category or rule.vip_category == vip_category:
				return True
		return False
