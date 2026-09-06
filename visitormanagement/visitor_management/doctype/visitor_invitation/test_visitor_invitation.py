# See license.txt

"""Visitor Invitation — the host-sent link a visitor uses to pre-register.

`get_web_form_context` (visitor_invitation.py:93, `allow_guest=True`) is one
of only 3 guest-reachable endpoints in this app (see CLAUDE.md's security
baseline), so a bug in token validation here is directly internet-facing: it
decides whether an anonymous caller holding a guessed or leaked string gets
back somebody else's pre-registration data. This DocType shipped with zero
test coverage of that path.
"""

from __future__ import annotations

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import add_days, now_datetime, nowdate

from visitormanagement.visitor_management.doctype.visitor_invitation.visitor_invitation import (
	get_valid_invitation_by_token,
	get_web_form_context,
)


def _host_employee():
	return frappe.db.get_value("Employee", {"status": "Active"}, "name")


def _active_visitor_type():
	return frappe.db.get_value("Visitor Type", {"is_active": 1}, "name")


class TestVisitorInvitation(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		# Same pre-existing, non-app issue as test_security_log.py /
		# test_hospitality_request.py: Visitor Invitation links Employee
		# directly (host_employee), so IntegrationTestCase's auto-generated
		# test-record walk pulls in Company -> Fiscal Year and collides with
		# the real 2026-2027 Fiscal Year on this shared dev site (see
		# CLAUDE.md's note on test_visitor_pass.py). Every fixture this file
		# needs is built by hand against real site data, so skip that walk
		# for this doctype only.
		frappe.local.test_objects.setdefault("Visitor Invitation", [])
		super().setUpClass()

	def setUp(self):
		self.host = _host_employee()
		self.visitor_type = _active_visitor_type()
		if not self.host or not self.visitor_type:
			self.skipTest("no Active Employee / active Visitor Type on this site")

	def _new_invitation(self, **overrides):
		payload = {
			"doctype": "Visitor Invitation",
			"visitor_type": self.visitor_type,
			"visitor_email": "invitee@example.com",
			"host_employee": self.host,
			"visit_date": add_days(nowdate(), 3),
			"expected_checkin": "10:00:00",
			"expected_checkout": "17:00:00",
			"purpose_of_visit": "Test invitation",
		}
		payload.update(overrides)
		doc = frappe.get_doc(payload)
		doc.insert()
		return doc

	# ---------------- create -> send -> token round trip ----------------

	def test_create_send_and_token_round_trip(self):
		invitation = self._new_invitation()
		result = invitation.send_invitation()
		self.assertTrue(result.get("link"), "send_invitation did not return a link")

		invitation.reload()
		self.assertTrue(invitation.invitation_token, "no token was minted by send_invitation")
		self.assertTrue(invitation.portal_submission_url)

		# The guest-reachable half of the round trip: an anonymous caller
		# holding this token should be able to load the pre-registration
		# context for it.
		ctx = get_web_form_context(invitation.invitation_token)
		self.assertTrue(ctx["valid"], f"a freshly sent token was reported invalid: {ctx}")
		self.assertEqual(ctx["invitation"], invitation.name)
		self.assertEqual(ctx["values"]["visitor_type"], self.visitor_type)
		self.assertEqual(ctx["values"]["person_to_visit"], self.host)

	def test_invalid_token_refused(self):
		self.assertIsNone(get_valid_invitation_by_token("this-token-was-never-issued-12345"))
		ctx = get_web_form_context("this-token-was-never-issued-12345")
		self.assertFalse(ctx["valid"])

	def test_blank_token_refused(self):
		self.assertIsNone(get_valid_invitation_by_token(""))
		self.assertIsNone(get_valid_invitation_by_token(None))

	def test_expired_token_refused(self):
		invitation = self._new_invitation()
		invitation.send_invitation()
		invitation.reload()
		token = invitation.invitation_token
		self.assertTrue(token)

		# validate() refuses to set an expiry in the past directly, so force it
		# the same way the app's own overstay/retention tests do — db_set past
		# the point where normal validation would ever allow this state, to
		# reach the "already expired" branch get_valid_invitation_by_token has
		# to handle for a link nobody used before its natural expiry.
		frappe.db.set_value(
			"Visitor Invitation", invitation.name,
			"invitation_expires_on", add_days(now_datetime(), -1),
			update_modified=False,
		)

		self.assertIsNone(get_valid_invitation_by_token(token))
		self.assertEqual(
			frappe.db.get_value("Visitor Invitation", invitation.name, "invitation_status"),
			"Expired",
			"looking up an expired token should flip invitation_status to Expired",
		)

	def test_already_used_token_refused(self):
		invitation = self._new_invitation()
		invitation.send_invitation()
		invitation.reload()
		token = invitation.invitation_token

		# "Submitted" is what the app sets once the visitor's pre-registration
		# form has gone through — a used-up link, not a revoked one.
		frappe.db.set_value(
			"Visitor Invitation", invitation.name, "invitation_status", "Submitted", update_modified=False
		)

		self.assertIsNone(get_valid_invitation_by_token(token))

	def test_cancelled_token_refused(self):
		invitation = self._new_invitation()
		invitation.send_invitation()
		invitation.reload()
		token = invitation.invitation_token

		frappe.db.set_value(
			"Visitor Invitation", invitation.name, "invitation_status", "Cancelled", update_modified=False
		)

		self.assertIsNone(
			get_valid_invitation_by_token(token),
			"a host-cancelled invitation link must stop resolving immediately",
		)

	# ---------------- the collation regression ----------------

	def test_token_lookup_is_case_sensitive(self):
		"""The column moved to utf8mb4_bin this session specifically so a
		case-flipped guess doesn't resolve to a real invitation. A random
		token from `secrets.token_urlsafe` is base64url, so it is virtually
		certain to contain letters — but this sets the token explicitly to
		remove any doubt about that."""
		invitation = self._new_invitation()
		frappe.db.set_value(
			"Visitor Invitation", invitation.name, "invitation_token", "AbCdEf123456", update_modified=False
		)

		self.assertIsNotNone(
			get_valid_invitation_by_token("AbCdEf123456"),
			"the exact-case token should resolve",
		)
		self.assertIsNone(
			get_valid_invitation_by_token("abcdef123456"),
			"a case-flipped token must NOT resolve to the same invitation",
		)
		self.assertIsNone(
			get_valid_invitation_by_token("ABCDEF123456"),
			"a case-flipped token must NOT resolve to the same invitation",
		)
