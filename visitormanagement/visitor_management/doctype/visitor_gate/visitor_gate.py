# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class VisitorGate(Document):
	def validate(self):
		self.gate_name = (self.gate_name or "").strip()
		if not self.gate_name:
			frappe.throw(_("Gate Name is required."))
		self._dedupe_assigned_users()

	def _dedupe_assigned_users(self):
		"""One row per user. A user listed twice is still one guard at this gate."""
		seen = set()
		rows = []
		for row in self.get("assigned_security_users") or []:
			if not row.user or row.user in seen:
				continue
			seen.add(row.user)
			rows.append(row)
		self.set("assigned_security_users", rows)


def gates_assigned_to(user=None):
	"""The active gates `user` (default: the session user) is assigned to, by name."""
	user = user or frappe.session.user
	officer = frappe.qb.DocType("Visitor Gate Officer")
	gate = frappe.qb.DocType("Visitor Gate")
	return (
		frappe.qb.from_(officer)
		.inner_join(gate)
		.on(gate.name == officer.parent)
		.select(gate.name)
		.distinct()
		.where(officer.parenttype == "Visitor Gate")
		.where(officer.parentfield == "assigned_security_users")
		.where(officer.user == user)
		.where(gate.is_active == 1)
		.orderby(gate.name)
		.run(pluck=True)
	)
