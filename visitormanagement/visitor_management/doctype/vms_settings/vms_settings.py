# Copyright (c) 2026, Harthesh and contributors
# For license information, please see license.txt

import re

import frappe
from frappe import _
from frappe.model.document import Document


EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class VMSSettings(Document):
	def validate(self):
		# Email format checks
		for field in ("admin_email", "food_dept_email"):
			value = (getattr(self, field, None) or "").strip()
			if value and not EMAIL_RE.match(value):
				frappe.throw(
					_("'{0}' is not a valid email address.").format(value),
					title=_("Invalid Email"),
				)

		# max_visit_duration_hrs must be positive if set.
		max_hours = self.max_visit_duration_hrs
		if max_hours is not None and int(max_hours) < 0:
			frappe.throw(
				_("Max Visit Duration (hrs) cannot be negative."),
				title=_("Invalid Duration"),
			)
