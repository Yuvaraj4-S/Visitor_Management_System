# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import getdate, nowdate

from visitormanagement.visitor_management import approval_tasks


class VMSApprovalDelegation(Document):
	"""An approver's visitor pass approvals, handed to a colleague for a date range.

	While it is in force the delegate may approve or reject whatever the approver
	could (approval_tasks.resolve_approvers). Nothing is copied to the delegate's
	user: from the day after `to_date`, or once it is unticked or deleted, the
	delegate is no longer named on any pass and can no longer act on one.
	"""

	def validate(self):
		self._validate_dates()
		self._validate_people()
		self._validate_own_delegation()

	def _validate_dates(self):
		if self.from_date and self.to_date and getdate(self.from_date) > getdate(self.to_date):
			frappe.throw(_("To Date cannot be before From Date."), title=_("Invalid Dates"))

	def _validate_people(self):
		if self.approver == self.delegate:
			frappe.throw(_("Pick someone other than the approver as the delegate."), title=_("Same Person"))
		delegate = frappe.db.get_value("User", self.delegate, ["enabled", "user_type"], as_dict=True)
		if not delegate or not delegate.enabled:
			frappe.throw(
				_("{0} is not an active user, so they could not approve anything.").format(self.delegate),
				title=_("Inactive Delegate"),
			)
		if delegate.user_type != "System User":
			frappe.throw(
				_("{0} cannot use the desk, where passes are approved.").format(self.delegate),
				title=_("Delegate Cannot Approve"),
			)

	def _validate_own_delegation(self):
		"""An approver hands over their own approvals; a System Manager anyone's."""
		user = frappe.session.user
		if user == "Administrator" or "System Manager" in frappe.get_roles(user):
			return
		if self.approver != user:
			frappe.throw(
				_("You can only hand over your own approvals."),
				frappe.PermissionError,
				title=_("Not Your Approvals"),
			)
		if not approval_tasks.is_approver(user):
			frappe.throw(
				_("You do not approve visitor passes, so there is nothing to hand over."),
				title=_("Not an Approver"),
			)

	def on_update(self):
		# The delegate needs the role named approvers act through. On its own it
		# opens nothing (see workflow_builder.ASSIGNED_APPROVER_ROLE), and the
		# hourly clean-up takes it back once nothing names them.
		if self.is_active and getdate(self.to_date) >= getdate(nowdate()):
			approval_tasks.grant_assigned_role([self.delegate])
		approval_tasks.refresh_pending_assignments()

	def after_delete(self):
		approval_tasks.refresh_pending_assignments()
