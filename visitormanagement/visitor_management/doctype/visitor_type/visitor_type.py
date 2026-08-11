# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from visitormanagement.visitor_management.workflow_builder import (
	rebuild_on_visitor_type_change,
)


class VisitorType(Document):

	# Changing these rewrites the approval workflow's lanes, so they are an
	# access-control decision, not ordinary configuration.
	APPROVER_FIELDS = ("approver_role", "secondary_approver_role")

	def validate(self):
		self.visitor_type_name = (self.visitor_type_name or "").strip()
		if not self.visitor_type_name:
			frappe.throw(_("Visitor Type Name is required."))

		self._guard_approver_role_changes()

	def _guard_approver_role_changes(self):
		"""Only a System Manager may decide who approves a visitor type.

		Whoever sets `approver_role` picks the role that can approve every pass
		of that type — so writing this field is equivalent to granting an
		approval privilege. Doctype permissions are the first line of defence;
		this is the second, because a permission can be widened again from the
		Role Permission Manager by accident, and because it also covers scripted
		and API writes that bypass the desk UI.
		"""
		if frappe.flags.in_install or frappe.flags.in_migrate or frappe.flags.in_patch:
			return
		if frappe.session.user == "Administrator":
			return

		before = self.get_doc_before_save()
		changed = [
			field
			for field in self.APPROVER_FIELDS
			if (before.get(field) if before else None) != self.get(field)
		]
		if not changed:
			return

		if "System Manager" not in frappe.get_roles(frappe.session.user):
			frappe.throw(
				_("Only a System Manager can change who approves a visitor type ({0}).").format(
					", ".join(_(self.meta.get_label(f)) for f in changed)
				),
				frappe.PermissionError,
			)

	def on_update(self):
		# The approval workflow's lanes are generated from this table, so adding a
		# type — or repointing one at a different approver role — has to regenerate
		# it. Without this a new type would be selectable but have no
		# `Draft -> Pending <role>` transition, leaving the pass stuck in Draft.
		rebuild_on_visitor_type_change(self)

	def on_trash(self):
		rebuild_on_visitor_type_change(self)
