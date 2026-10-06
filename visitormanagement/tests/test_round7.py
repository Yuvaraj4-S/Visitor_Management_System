# See license.txt
"""Round 7: what the realistic demo-data build found (D1-D6).

Locks in:
  D1  an approved pass with a room produces its booking in the Facility
      Manager's queue whoever approved it (a Sales Manager with or without the
      Employee role), says nothing about a clash with itself, and leaves no Draft
      bookings behind on reject / reapply / cancel / amend;
  D2  a request created from a pass carries no "now" Frappe stamped into its
      empty Time fields, so a pass with a factory tour can be sent at any hour;
  D3  the badge names the organisation in VMS Settings, else the host's company,
      else the default company;
  D4  a customer visit's outcome can be recorded on the approved pass by the
      people the meeting belongs to, and only by them;
  D5  the hospitality team keeps its service record on an approved request;
  D6  an approved pass shows the gate's status, not "Approved", once the gate
      has moved it on.

Run (never without the two skip flags on a working site):

	bench --site <site> run-tests --module visitormanagement.tests.test_round7 \\
		--skip-before-tests --skip-test-records
"""

import json
import os
import re
from unittest.mock import patch

import frappe
from frappe.model.workflow import apply_workflow
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, cint, get_time, now_datetime, nowdate, nowtime

from visitormanagement.conference_room.doctype.conference_room_booking import (
	conference_room_booking as booking_module,
)
from visitormanagement.tests.test_fix_a import _make_employee, _make_user
from visitormanagement.tests.test_regression import _file, _fresh_pan
from visitormanagement.visitor_management import lifecycle, workflow_builder
from visitormanagement.visitor_management.doctype.hospitality_request import (
	hospitality_request as request_module,
)
from visitormanagement.visitor_management.doctype.visitor_pass import visitor_pass as pass_module

# The runner walks Link dependencies before setUpClass; every fixture is built here.
test_ignore = ["Employee", "ID Proof Type", "Visitor Type", "Visitor Pass", "User", "Conference Room"]

VISITOR_TYPE = "Customer"
# Refused by a validation or by a permission check, whichever comes first.
REFUSED = (frappe.ValidationError, frappe.PermissionError)


def _staff(tag, *roles):
	"""A desk user holding exactly `roles` (rolled back with the class)."""
	user = _make_user(tag, *roles)
	# Inserted without a role the user was typed "Website User".
	frappe.db.set_value("User", user, "user_type", "System User", update_modified=False)
	frappe.clear_cache(user=user)
	return user


def _as(user, fn, *args, **kwargs):
	frappe.set_user(user)
	try:
		return fn(*args, **kwargs)
	finally:
		frappe.set_user("Administrator")


def _messages():
	return " ".join(str(m) for m in frappe.local.message_log)


class _Round7Case(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		# Registered before FrappeTestCase adds its rollback, so it runs after it.
		cls.addClassCleanup(frappe.clear_cache)
		super().setUpClass()
		frappe.set_user("Administrator")
		routing = frappe.db.get_value(
			"Visitor Type", VISITOR_TYPE, ["approver_role", "secondary_approver_role"], as_dict=True
		)
		cls.lane_roles = [r for r in (routing.approver_role, routing.secondary_approver_role) if r]
		cls.host_user = _staff("r7-host", "Employee")
		cls.host = _make_employee(cls.host_user)
		# As on the demo site: an approver who is also staff, and one who is only an approver.
		cls.approver_staff = {role: _staff("r7-appr-emp", "Employee", role) for role in cls.lane_roles}
		cls.approver_only = {role: _staff("r7-appr-only", role) for role in cls.lane_roles}
		cls.facility_manager = _staff("r7-fm", "Employee", "Facility Manager")
		cls.hospitality_manager = _staff("r7-hm", "Employee", "Hospitality Manager")
		cls.outsider = _staff("r7-outsider", "Employee")
		cls.guard = _staff("r7-guard", "Security")
		cls.room = frappe.get_doc(
			{
				"doctype": "Conference Room",
				"room_name": f"ZZ R7 Room {frappe.generate_hash(length=5)}",
				"capacity": 10,
				"location": "R7 Block",
				"available_from": "08:00:00",
				"available_to": "20:00:00",
			}
		).insert(ignore_permissions=True)

	def setUp(self):
		frappe.set_user("Administrator")
		frappe.clear_messages()
		self._day = getattr(self, "_day", 0)

	def tearDown(self):
		frappe.set_user("Administrator")
		frappe.clear_messages()

	# ── passes ────────────────────────────────────────────────
	def new_pass(self, window=("10:00:00", "11:00:00"), **values):
		type(self)._day = getattr(type(self), "_day", 0) + 1
		label = frappe.generate_hash(length=8)
		vp = frappe.new_doc("Visitor Pass")
		vp.update(
			{
				"visitor_type": VISITOR_TYPE,
				"visitor_full_name": f"Round Seven {label}",
				"mobile_number": "+91 9876543210",
				"email_id": f"r7-{label}@example.com",
				"company__organisation": "R7 Customer Ltd",
				"id_proof_type": "PAN Card",
				"id_proof_number": _fresh_pan(),
				"id_proof_scan": _file(f"r7_id_{label}"),
				"visitor_photo": _file(f"r7_photo_{label}"),
				"person_to_visit": self.host,
				"visit_date": add_days(nowdate(), 3 + type(self)._day),
				"expected_checkin": window[0],
				"expected_checkout": window[1],
				"purpose_of_visit": "Round 7 test",
				**values,
			}
		)
		vp.flags.ignore_mandatory = True
		_as(self.host_user, vp.insert)
		return vp.name

	def act(self, user, name, action):
		return _as(user, apply_workflow, frappe.get_doc("Visitor Pass", name), action)

	def approve(self, name, approvers):
		"""Walk the pass through every approval lane, as the given role holders."""
		for _lane in range(3):
			state = frappe.db.get_value("Visitor Pass", name, "workflow_state")
			role = next((r for r in self.lane_roles if workflow_builder.lane_for_role(r) == state), None)
			if not role:
				break
			self.act(approvers[role], name, "Approve")
		self.assertEqual(frappe.db.get_value("Visitor Pass", name, "docstatus"), 1)

	def bookings(self, name):
		return frappe.get_all(
			"Conference Room Booking",
			filters={"visitor_pass": name},
			fields=["name", "workflow_state", "status", "docstatus"],
			order_by="creation",
		)

	def approved_customer_pass(self, **values):
		name = self.new_pass(**values)
		self.act(self.host_user, name, "Submit")
		self.approve(name, self.approver_staff)
		return name


# ─── D1: the room booking of an approved pass ─────────────────────────────
class TestRoomBookingFollowsApproval(_Round7Case):
	def _crb_errors(self):
		return frappe.db.count("Error Log", {"method": "VMS CRB Auto-Create"})

	def test_approver_without_access_to_the_booking_still_sends_it_for_approval(self):
		for approvers in (self.approver_staff, self.approver_only):
			with self.subTest(approver_roles=sorted(frappe.get_roles(next(iter(approvers.values()))))):
				errors = self._crb_errors()
				name = self.new_pass(conference_room=self.room.name)
				self.act(self.host_user, name, "Submit")
				# Sent for approval: the pass's own booking holds the room as a Draft.
				self.assertEqual([b.workflow_state for b in self.bookings(name)], ["Draft"])
				approver = approvers[self.lane_roles[-1]]
				self.assertFalse(
					frappe.has_permission(
						"Conference Room Booking", "read", self.bookings(name)[0].name, user=approver
					),
					"the approver could read the host's booking; this test would prove nothing",
				)
				frappe.clear_messages()
				self.approve(name, approvers)

				booking = self.bookings(name)
				self.assertEqual(len(booking), 1)
				self.assertEqual((booking[0].workflow_state, booking[0].status), ("Pending Approval",) * 2)
				self.assertNotIn("Room Not Reserved", _messages())
				self.assertNotIn("already booked", _messages())
				self.assertEqual(self._crb_errors(), errors)
				# The timeline says what happened, like any workflow step.
				self.assertTrue(
					frappe.db.exists(
						"Comment",
						{
							"reference_doctype": "Conference Room Booking",
							"reference_name": booking[0].name,
							"comment_type": "Workflow",
							"content": "Pending Approval",
						},
					)
				)
				# The hospitality request of the same pass went to the Hospitality Manager too.
				request = frappe.db.get_value("Visitor Pass", name, "hospitality_request")
				self.assertEqual(
					frappe.db.get_value("Hospitality Request", request, "workflow_state"), "Pending Approval"
				)

	def test_the_cause_frappe_checks_the_step_against_the_approver(self):
		"""Without the controller's exception the old failure comes back, and is reported honestly."""
		name = self.new_pass(conference_room=self.room.name)
		self.act(self.host_user, name, "Submit")
		# (log_failure is replaced: Error Log is MyISAM and would outlive the rollback.)
		with (
			patch.object(booking_module, "step_taken_with_visit", return_value=False),
			patch.object(lifecycle, "log_failure") as logged,
		):
			self.approve(name, self.approver_only)
		self.assertEqual([b.workflow_state for b in self.bookings(name)], ["Draft"])
		# The room is "not reserved", but no longer "already booked" by the pass itself.
		self.assertIn("Room Not Reserved", _messages())
		self.assertNotIn("already booked", _messages())
		# What was logged: Frappe's read check on the stored booking, inside get_transitions.
		titles = [c.args[0] for c in logged.call_args_list]
		self.assertIn("VMS CRB Auto-Create", titles)
		crb = logged.call_args_list[titles.index("VMS CRB Auto-Create")].args[1]
		self.assertIn("get_transitions", crb)
		self.assertIn("PermissionError", crb)

	def test_approval_of_the_booking_stays_with_the_facility_manager(self):
		name = self.new_pass(conference_room=self.room.name)
		self.act(self.host_user, name, "Submit")
		self.approve(name, self.approver_staff)
		booking = self.bookings(name)[0].name
		approver = self.approver_staff[self.lane_roles[-1]]
		with self.assertRaises(REFUSED):
			_as(approver, apply_workflow, frappe.get_doc("Conference Room Booking", booking), "Approve")
		# Setting the state by hand with the flag a user cannot send is refused as well.
		doc = frappe.get_doc("Conference Room Booking", booking)
		doc.workflow_state = "Approved"
		with self.assertRaises(REFUSED):
			_as(approver, doc.save, ignore_permissions=True)
		_as(
			self.facility_manager,
			apply_workflow,
			frappe.get_doc("Conference Room Booking", booking),
			"Approve",
		)
		self.assertEqual(
			frappe.db.get_value("Conference Room Booking", booking, ["workflow_state", "docstatus"]),
			("Approved", 1),
		)
		# A later save of the pass leaves the approved booking alone.
		frappe.clear_messages()
		pass_doc = frappe.get_doc("Visitor Pass", name)
		pass_doc.hospitality_notes = "<p>Window seat</p>"
		_as(approver, pass_doc.save)
		self.assertEqual(len(self.bookings(name)), 1)
		self.assertNotIn("Room Not Reserved", _messages())

	def test_the_flag_only_allows_the_requesters_step_on_a_pass_that_is_approved(self):
		name = self.new_pass(conference_room=self.room.name)
		self.act(self.host_user, name, "Submit")
		booking = frappe.get_doc("Conference Room Booking", self.bookings(name)[0].name)
		# The pass is not approved yet: the step is refused.
		with self.assertRaises(REFUSED):
			_as(self.outsider, lifecycle.send_for_approval_with_visit, booking)
		booking.reload()
		self.assertEqual(booking.workflow_state, "Draft")
		# A step the list does not hold is refused before anything is saved.
		with self.assertRaises(frappe.ValidationError):
			lifecycle._take_step_with_visit(booking, "Approved")

	def test_reject_reapply_and_amend_leave_no_draft_holding_the_room(self):
		name = self.new_pass(conference_room=self.room.name)
		self.act(self.host_user, name, "Submit")
		reject_as = self.approver_staff[self.lane_roles[0]]
		self.act(reject_as, name, "Reject")
		# A rejected pass does not hold its room.
		self.assertEqual(
			[(b.workflow_state, b.status) for b in self.bookings(name)], [("Rejected", "Rejected")]
		)
		self.assertFalse(
			booking_module.find_conflicting_booking(
				self.room.name,
				frappe.db.get_value("Visitor Pass", name, "visit_date"),
				"10:00:00",
				"11:00:00",
			)
		)
		# Reapplied and sent again: the same booking is asked for again, then approved with the pass.
		self.act(self.host_user, name, "Reapply")
		self.act(self.host_user, name, "Submit")
		self.assertEqual([b.workflow_state for b in self.bookings(name)], ["Draft"])
		self.approve(name, self.approver_only)
		self.assertEqual([b.workflow_state for b in self.bookings(name)], ["Pending Approval"])

		# Cancelled and amended: the old booking is called off, the amendment gets one of its own.
		cancel_as = self.approver_staff[self.lane_roles[-1]]
		self.act(cancel_as, name, "Cancel")
		self.assertEqual([b.workflow_state for b in self.bookings(name)], ["Rejected"])
		amended = frappe.copy_doc(frappe.get_doc("Visitor Pass", name))
		amended.amended_from = name
		amended.workflow_state = "Draft"
		amended.status = "Draft"
		amended.docstatus = 0
		amended.id_proof_number = frappe.db.get_value("Visitor Pass", name, "id_proof_number")
		amended.flags.ignore_mandatory = True
		_as(self.host_user, amended.insert)
		self.act(self.host_user, amended.name, "Submit")
		self.approve(amended.name, self.approver_only)
		self.assertEqual([b.workflow_state for b in self.bookings(amended.name)], ["Pending Approval"])
		# Through all of it, the visit never had more than one booking at a time, and none is a Draft.
		drafts = frappe.get_all(
			"Conference Room Booking",
			filters={"visitor_pass": ("in", [name, amended.name]), "workflow_state": "Draft"},
		)
		self.assertEqual(drafts, [])

	def test_a_real_clash_is_still_reported_with_the_other_booking(self):
		first = self.new_pass(conference_room=self.room.name)
		self.act(self.host_user, first, "Submit")
		visit_date = frappe.db.get_value("Visitor Pass", first, "visit_date")
		second = self.new_pass(conference_room=self.room.name)
		frappe.db.set_value("Visitor Pass", second, "visit_date", visit_date)
		frappe.clear_messages()
		with patch.object(lifecycle, "log_failure"):
			self.act(self.host_user, second, "Submit")
		self.assertEqual(self.bookings(second), [])
		self.assertIn("already booked", _messages())


# ─── D2: Time fields Frappe stamps with "now" ─────────────────────────────
class TestNoTimeNobodyEntered(_Round7Case):
	def _window_without_now(self):
		return ("14:00:00", "15:00:00") if now_datetime().hour < 12 else ("08:00:00", "09:00:00")

	def test_new_documents_get_the_current_time_in_every_time_field(self):
		"""The Frappe 15 behaviour this guards against (frappe/model/create_new.py)."""
		request = frappe.new_doc("Hospitality Request")
		self.assertTrue(request.tour_start_time and request.tour_end_time)
		self.assertTrue(get_time(request.tour_start_time).microsecond)

	def test_a_pass_with_a_factory_tour_is_sent_outside_the_visit_hours(self):
		window = self._window_without_now()
		name = self.new_pass(window=window, factory_tour_required=1)
		self.act(self.host_user, name, "Submit")
		request = frappe.db.get_value(
			"Hospitality Request",
			{"visitor_pass": name},
			["tour_date", "tour_start_time", "tour_end_time"],
			as_dict=True,
		)
		self.assertEqual(get_time(request.tour_start_time), get_time(window[0]))
		self.assertIsNone(request.tour_end_time)
		self.assertEqual(str(request.tour_date), str(frappe.db.get_value("Visitor Pass", name, "visit_date")))

	def test_the_cause_without_the_fix_the_pass_cannot_be_sent(self):
		name = self.new_pass(window=self._window_without_now(), factory_tour_required=1)
		with patch.object(
			request_module.HospitalityRequest, "_forget_times_nobody_entered", lambda self: None
		):
			with self.assertRaises(frappe.ValidationError) as caught:
				self.act(self.host_user, name, "Submit")
		self.assertIn("outside the visit window", str(caught.exception))

	def test_new_tour_rows_and_typed_times(self):
		name = self.new_pass(window=("10:00:00", "12:00:00"), factory_tour_required=1)
		self.act(self.host_user, name, "Submit")
		request = frappe.get_doc("Hospitality Request", {"visitor_pass": name})
		request.tour_end_time = "11:30:00"
		request.append("tour_areas", {"area_name": "Moulding shop"})
		request.append(
			"tour_areas", {"area_name": "Assembly", "from_time": "10:30:00", "to_time": "11:00:00"}
		)
		request.save(ignore_permissions=True)
		request.reload()
		self.assertEqual(get_time(request.tour_end_time), get_time("11:30:00"))
		untimed, timed = request.tour_areas
		self.assertIsNone(untimed.from_time)
		self.assertIsNone(untimed.to_time)
		self.assertEqual(get_time(timed.from_time), get_time("10:30:00"))
		# A time somebody typed is never taken for Frappe's stamp, even if typed this second.
		self.assertFalse(request_module.is_framework_now(nowtime().split(".")[0]))


# ─── D3: whose name is on the badge ───────────────────────────────────────
class TestBadgeOrganisation(_Round7Case):
	def footer(self, name):
		"""The organisation line at the foot of the badge."""
		frappe.db.value_cache = {}
		html = frappe.get_print("Visitor Pass", name, print_format="Visitor Badge")
		found = re.search(r'class="vb-footer">\s*([^<]*?)\s*<', html)
		self.assertTrue(found, "no footer on the badge")
		return found.group(1)

	def test_settings_then_host_company_then_default_company(self):
		name = self.approved_customer_pass()
		companies = frappe.get_all("Company", pluck="name", order_by="creation")
		default = frappe.db.get_single_value("Global Defaults", "default_company")
		host_company = next((c for c in companies if c != default), None) or default

		frappe.db.set_single_value("VMS Settings", "portal_organisation_name", "R7 Precision Works")
		frappe.db.set_value("Employee", self.host, "company", host_company, update_modified=False)
		self.assertEqual(self.footer(name), "R7 Precision Works")

		frappe.db.set_single_value("VMS Settings", "portal_organisation_name", "  ")
		self.assertEqual(self.footer(name), frappe.utils.escape_html(host_company))

		frappe.db.set_value("Employee", self.host, "company", None, update_modified=False)
		self.assertEqual(self.footer(name), frappe.utils.escape_html(default or "Visitor Management"))

	def test_the_itinerary_prints_no_company_of_the_site(self):
		path = os.path.join(
			frappe.get_app_path("visitormanagement"),
			"visitor_management",
			"print_format",
			"visitor_itinerary",
			"visitor_itinerary.html",
		)
		with open(path) as handle:
			source = handle.read()
		self.assertNotIn("default_company", source)
		self.assertNotIn("Global Defaults", source)


# ─── D4: the outcome of a customer visit ──────────────────────────────────
class TestMeetingOutcome(_Round7Case):
	def record(self, user, name, **values):
		return _as(user, pass_module.record_meeting_outcome, name, **values)

	def test_fields_are_allow_on_submit_and_nothing_identity_related_is(self):
		meta = frappe.get_meta("Visitor Pass")
		for fieldname in pass_module.VisitorPass.MEETING_OUTCOME_FIELDS:
			self.assertTrue(meta.get_field(fieldname).allow_on_submit, fieldname)
		for fieldname in (
			"visitor_full_name",
			"id_proof_type",
			"visit_date",
			"person_to_visit",
			"visitor_type",
		):
			self.assertFalse(meta.get_field(fieldname).allow_on_submit, fieldname)

	def test_the_host_and_an_approver_record_it_after_approval(self):
		name = self.approved_customer_pass()
		visit_date = frappe.db.get_value("Visitor Pass", name, "visit_date")
		self.record(
			self.host_user,
			name,
			meeting_outcome="Follow-Up Needed",
			followup_date=str(add_days(visit_date, 7)),
			meeting_minutes='<p>Asked for a <b>quote</b></p><script>alert(1)</script><img src=x onerror="y()">',
		)
		stored = frappe.db.get_value(
			"Visitor Pass",
			name,
			["meeting_outcome", "followup_date", "meeting_minutes", "docstatus"],
			as_dict=True,
		)
		self.assertEqual(stored.meeting_outcome, "Follow-Up Needed")
		self.assertEqual(str(stored.followup_date), str(add_days(visit_date, 7)))
		self.assertEqual(stored.docstatus, 1)
		self.assertIn("<b>quote</b>", stored.meeting_minutes)
		for dangerous in ("<script", "onerror"):
			self.assertNotIn(dangerous, stored.meeting_minutes)

		self.record(self.approver_only[self.lane_roles[0]], name, meeting_outcome="Deal Closed")
		self.assertEqual(frappe.db.get_value("Visitor Pass", name, "meeting_outcome"), "Deal Closed")
		# The form offers it to them and not to others.
		for user, offered in ((self.host_user, True), (self.outsider, False), (self.guard, False)):
			doc = _as(user, frappe.get_doc, "Visitor Pass", name)
			_as(user, doc.run_method, "onload")
			self.assertEqual(bool(doc.get_onload().get("may_record_outcome")), offered, user)

	def test_others_cannot_record_it_and_identity_stays_frozen(self):
		name = self.approved_customer_pass()
		for user in (self.outsider, self.guard):
			with self.assertRaises(frappe.PermissionError):
				self.record(user, name, meeting_outcome="No Interest")
		# The same rule on any other way in.
		doc = frappe.get_doc("Visitor Pass", name)
		doc.meeting_outcome = "No Interest"
		with self.assertRaises(frappe.PermissionError):
			_as(self.outsider, doc.save, ignore_permissions=True)
		# Fields that are not about the outcome cannot change after approval.
		doc = frappe.get_doc("Visitor Pass", name)
		doc.visitor_full_name = "Somebody Else"
		with self.assertRaises(frappe.UpdateAfterSubmitError):
			doc.save()  # Administrator: past every permission, still frozen
		self.assertEqual(frappe.db.get_value("Visitor Pass", name, "meeting_outcome"), "Pending")

	def test_follow_up_date_and_visit_type(self):
		name = self.approved_customer_pass()
		visit_date = frappe.db.get_value("Visitor Pass", name, "visit_date")
		with self.assertRaises(frappe.ValidationError):
			self.record(
				self.host_user,
				name,
				meeting_outcome="Follow-Up Needed",
				followup_date=str(add_days(visit_date, -1)),
			)
		draft = self.new_pass()
		with self.assertRaises(frappe.ValidationError):
			self.record(self.host_user, draft, meeting_outcome="Deal Closed")
		other_type = self.approved_customer_pass()
		frappe.db.set_value(
			"Visitor Pass", other_type, "visitor_type_layout", "Supplier", update_modified=False
		)
		with self.assertRaises(frappe.ValidationError):
			self.record(self.host_user, other_type, meeting_outcome="Deal Closed")


# ─── D5: the hospitality team's service record ────────────────────────────
class TestServiceRecord(_Round7Case):
	def approved_request(self):
		name = self.approved_customer_pass(hotel_required=1)
		request = frappe.db.get_value("Visitor Pass", name, "hospitality_request")
		_as(
			self.hospitality_manager,
			apply_workflow,
			frappe.get_doc("Hospitality Request", request),
			"Approve",
		)
		self.assertEqual(frappe.db.get_value("Hospitality Request", request, "docstatus"), 1)
		return name, request

	def test_hospitality_manager_records_service_and_costs_after_approval(self):
		name, request = self.approved_request()
		doc = frappe.get_doc("Hospitality Request", request)
		doc.service_notes = "<p>Lunch for 4 served at <b>13:10</b></p><script>alert(1)</script>"
		doc.hotel_cost = 6400
		_as(self.hospitality_manager, doc.save)
		stored = frappe.db.get_value(
			"Hospitality Request", request, ["service_notes", "hotel_cost"], as_dict=True
		)
		self.assertIn("<b>13:10</b>", stored.service_notes)
		self.assertNotIn("<script", stored.service_notes)
		self.assertEqual(stored.hotel_cost, 6400)
		# A later save of the approved pass leaves the service record alone.
		pass_doc = frappe.get_doc("Visitor Pass", name)
		pass_doc.hospitality_notes = "<p>Guest arrives at 10:20</p>"
		# An approver: updating a submitted pass needs "submit" (Document.check_docstatus_transition).
		_as(self.approver_staff[self.lane_roles[-1]], pass_doc.save)
		self.assertIn("13:10", frappe.db.get_value("Hospitality Request", request, "service_notes"))
		# `notes` (the copy of the pass's notes) is not the team's to write after approval.
		doc = frappe.get_doc("Hospitality Request", request)
		doc.notes = "<p>written over</p>"
		with self.assertRaises(frappe.UpdateAfterSubmitError):
			_as(self.hospitality_manager, doc.save)

	def test_others_cannot_and_the_rest_stays_frozen(self):
		_name, request = self.approved_request()
		doc = frappe.get_doc("Hospitality Request", request)
		doc.service_notes = "<p>nothing served</p>"
		with self.assertRaises(frappe.PermissionError):
			_as(self.host_user, doc.save, ignore_permissions=True)
		doc = frappe.get_doc("Hospitality Request", request)
		doc.greeting_cost = -5
		with self.assertRaises(frappe.ValidationError):
			_as(self.hospitality_manager, doc.save)
		doc = frappe.get_doc("Hospitality Request", request)
		doc.dietary_allergies = "changed after approval"
		with self.assertRaises(frappe.UpdateAfterSubmitError):
			_as(self.hospitality_manager, doc.save)

	def test_service_notes_are_purged_with_the_rest(self):
		from visitormanagement.visitor_management import tasks

		self.assertIn("service_notes", tasks._HOSPITALITY_PURGE_VALUES)
		meta = frappe.get_meta("Hospitality Request")
		for fieldname in request_module.HospitalityRequest.SERVICE_RECORD_FIELDS:
			self.assertTrue(meta.get_field(fieldname).allow_on_submit, fieldname)
		self.assertFalse(meta.get_field("service_notes").hidden)


# ─── D6: the gate's status on an approved pass ────────────────────────────
class TestGateStatusIndicator(_Round7Case):
	def test_approved_state_leaves_the_indicator_to_the_document(self):
		workflow = frappe.get_doc("Workflow", workflow_builder.WORKFLOW_NAME)
		approved = [s for s in workflow.states if s.state == workflow_builder.APPROVED]
		self.assertTrue(approved)
		self.assertTrue(all(cint(s.avoid_status_override) for s in approved))
		# Every other state is still shown by the workflow (the approval lanes, Rejected, Cancelled).
		self.assertFalse(
			[s.state for s in workflow.states if s.state != "Approved" and s.avoid_status_override]
		)

	def test_list_and_form_scripts_follow_the_gate(self):
		folder = os.path.join(
			frappe.get_app_path("visitormanagement"), "visitor_management", "doctype", "visitor_pass"
		)
		with open(os.path.join(folder, "visitor_pass_list.js")) as handle:
			list_js = handle.read()
		for status in ("Items Verified", "Checked-In", "Checked-Out", "No-Show"):
			self.assertIn(status, list_js)
		self.assertIn("get_indicator", list_js)
		with open(os.path.join(folder, "visitor_pass.js")) as handle:
			form_js = handle.read()
		self.assertIn("PASS_GATE_STAGES", form_js)
		self.assertIn('return "No-Show"', form_js)

	def test_a_checked_in_pass_keeps_its_workflow_state_and_reports_its_status(self):
		name = self.approved_customer_pass()
		frappe.db.set_value("Visitor Pass", name, "status", "Checked-In", update_modified=False)
		doc = frappe.get_doc("Visitor Pass", name)
		self.assertEqual((doc.workflow_state, doc.status), ("Approved", "Checked-In"))
		# What the list and form receive to decide the indicator.
		row = frappe.get_list(
			"Visitor Pass",
			filters={"name": name},
			fields=["status", "no_show", "workflow_state", "docstatus"],
		)[0]
		self.assertEqual(json.loads(json.dumps(row, default=str))["status"], "Checked-In")
