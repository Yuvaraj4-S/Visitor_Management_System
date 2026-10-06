# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from visitormanagement.visitor_management.workflow_builder import (
	rebuild_on_visitor_type_change,
	types_with_pending_passes,
)


class VisitorType(Document):
	# Changing these rewrites the approval workflow's lanes, so they are an
	# access-control decision, not ordinary configuration.
	APPROVER_FIELDS = ("approver_role", "secondary_approver_role")

	def validate(self):
		self.visitor_type_name = (self.visitor_type_name or "").strip()
		if not self.visitor_type_name:
			frappe.throw(_("Visitor Type Name is required."))

		self._require_approver_role()
		self._guard_approver_role_changes()
		self._refuse_stranding_pending_passes()

	def _require_approver_role(self):
		"""A type with no approver role builds no workflow lane, so its passes
		can never be submitted.

		The workflow is generated from this table: each active type contributes a
		"Pending <approver_role>" state and the transitions into it. A type saved
		with `approver_role` empty contributes nothing, so a pass of that type
		saves happily as Draft and then dies at Submit with Frappe's generic
		"Not a valid Workflow Action" — a message that says nothing about the real
		cause, which is an incomplete master somebody configured days earlier.

		Catching it here means the person who can actually fix it is told at the
		moment they cause it, instead of a receptionist meeting a dead end later.
		A secondary approver without a primary is the same fault: the second stage
		has no first stage to follow.
		"""
		if not self.approver_role:
			if self.secondary_approver_role:
				frappe.throw(
					_(
						"{0} has a Secondary Approver Role but no Approver Role. "
						"The second approval stage has no first stage to follow."
					).format(self.visitor_type_name),
					title=_("Approver Role Required"),
				)
			frappe.throw(
				_(
					"{0} needs an Approver Role — it decides who approves passes of this type. "
					"Without one no approval lane is created and its passes cannot be submitted."
				).format(self.visitor_type_name),
				title=_("Approver Role Required"),
			)

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

	def _refuse_stranding_pending_passes(self):
		"""Passes awaiting approval must be decided before their type changes under them.

		A pending pass sits in the lane of the role that was to approve it, and the
		workflow's conditions read this record live. Point the type at another
		approver and the pass is in a lane whose Approve no longer applies to it —
		or in a lane that is gone, if no other type uses that role. Switching the
		type off takes it out of the picker while its passes still wait. Either way
		the administrator is told how many passes are in the way, before the change
		and not by a stuck pass afterwards.
		"""
		before = self.get_doc_before_save()
		if not before:
			return
		deactivated = before.is_active and not self.is_active
		rerouted = any(before.get(field) != self.get(field) for field in self.APPROVER_FIELDS)
		if not (deactivated or rerouted) or not types_with_pending_passes([self.name]):
			return

		pending = frappe.db.count(
			"Visitor Pass",
			{"visitor_type": self.name, "docstatus": 0, "workflow_state": ("like", "Pending %")},
		)
		frappe.throw(
			_(
				"{0} pass(es) of type {1} are awaiting approval. Approve or reject them first, "
				"then deactivate the type or change its approver roles."
			).format(pending, frappe.bold(self.name)),
			title=_("Passes Awaiting Approval"),
		)

	def on_update(self):
		# The approval workflow's lanes are generated from this table, so adding a
		# type — or repointing one at a different approver role — has to regenerate
		# it. Without this a new type would be selectable but have no
		# `Draft -> Pending <role>` transition, leaving the pass stuck in Draft.
		rebuild_on_visitor_type_change(self)
		_sync_approver_wiring()

	def on_trash(self):
		rebuild_on_visitor_type_change(self)
		_sync_approver_wiring()


def _sync_approver_wiring():
	"""Bring the rest of the approver wiring with the workflow lanes.

	Three things have to move together when a Visitor Type names an approver
	role: the workflow lane, the `submit` permission that lane's Approve
	transition needs (it targets a doc_status of 1), and the recipients of the
	approval-request email. Only the first was regenerated on save; the other two
	ran exclusively from `setup_visitor_management()`, i.e. on install and
	migrate.

	So an administrator who added a Visitor Type through the Desk got a lane that
	routed passes correctly and then refused the Approve click, and mailed nobody
	— repairable only by a developer running `bench migrate`, which makes the
	whole data-driven design theoretical from the admin's point of view.

	Imported here rather than at module level: `setup` imports from across the
	app, and a top-level import would make this controller a participant in that
	graph. Failures are logged rather than raised for the same reason the rebuild
	is — a notification-recipient sync must never block saving a master record.
	"""
	from visitormanagement.setup import (
		_align_visitor_pass_submit,
		_grant_visitor_pass_cancel,
		_sync_approval_notification_recipients,
	)

	for step in (
		_align_visitor_pass_submit,
		_sync_approval_notification_recipients,
		_grant_visitor_pass_cancel,
	):
		try:
			step()
		except Exception:
			frappe.log_error(
				title=f"VMS approver wiring: {step.__name__} failed",
				message=frappe.get_traceback(with_context=True),
			)
