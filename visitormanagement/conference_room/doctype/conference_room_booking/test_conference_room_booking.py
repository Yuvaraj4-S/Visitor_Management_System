import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, today


class TestConferenceRoomBooking(FrappeTestCase):

    # ── Helper ──

    def _make_booking(self, **kwargs):
        b = frappe.new_doc("Conference Room Booking")
        defaults = {
            "meeting_title": "Test Meeting",
            "conference_room": "Boardroom A",
            "meeting_type": "Internal",
            "booking_date": add_days(today(), 50),
            "start_time": "10:00:00",
            "end_time": "11:00:00",
            "booked_by": "HR-EMP-00001",
            "expected_attendees": 5,
        }
        defaults.update(kwargs)
        b.update(defaults)
        return b

    # ── 1. Conference Room Master Validations ──

    def test_room_available_from_must_be_before_to(self):
        room = frappe.new_doc("Conference Room")
        room.room_name = "Bad Hours Room"
        room.location = "Test"
        room.capacity = 10
        room.available_from = "18:00:00"
        room.available_to = "08:00:00"
        self.assertRaises(frappe.ValidationError, room.insert, ignore_permissions=True)

    def test_room_capacity_must_be_positive(self):
        room = frappe.new_doc("Conference Room")
        room.room_name = "Zero Cap Room"
        room.location = "Test"
        room.capacity = 0
        self.assertRaises(frappe.ValidationError, room.insert, ignore_permissions=True)

    def test_room_min_booking_must_be_at_least_15(self):
        room = frappe.new_doc("Conference Room")
        room.room_name = "Low Min Room"
        room.location = "Test"
        room.capacity = 10
        room.min_booking_minutes = 5
        self.assertRaises(frappe.ValidationError, room.insert, ignore_permissions=True)

    def test_valid_room_creation(self):
        room = frappe.new_doc("Conference Room")
        room.room_name = "Test Valid Room " + frappe.generate_hash(length=6)
        room.location = "Test"
        room.capacity = 10
        room.insert(ignore_permissions=True)
        self.assertTrue(room.name)

    # ── 2. Schedule Validations ──

    def test_past_date_blocked(self):
        b = self._make_booking(booking_date="2020-01-01")
        self.assertRaises(frappe.ValidationError, b.insert, ignore_permissions=True)

    def test_start_time_after_end_time_blocked(self):
        b = self._make_booking(start_time="14:00:00", end_time="10:00:00")
        self.assertRaises(frappe.ValidationError, b.insert, ignore_permissions=True)

    # ── 3. Duration Validations ──

    def test_below_min_duration_blocked(self):
        b = self._make_booking(
            conference_room="Huddle Room",
            booking_date=add_days(today(), 40),
            start_time="10:00:00", end_time="10:10:00",
            expected_attendees=2,
        )
        self.assertRaises(frappe.ValidationError, b.insert, ignore_permissions=True)

    def test_above_max_duration_blocked(self):
        b = self._make_booking(
            conference_room="Huddle Room",
            booking_date=add_days(today(), 40),
            start_time="09:00:00", end_time="15:00:00",
            expected_attendees=2,
        )
        self.assertRaises(frappe.ValidationError, b.insert, ignore_permissions=True)

    def test_duration_auto_calculated(self):
        b = self._make_booking(
            booking_date=add_days(today(), 100),
            start_time="10:00:00", end_time="11:30:00",
        )
        b.insert(ignore_permissions=True)
        self.assertEqual(b.duration_hours, 1.5)

    # ── 4. Capacity Validation ──

    def test_over_capacity_blocked(self):
        b = self._make_booking(
            conference_room="Huddle Room",
            booking_date=add_days(today(), 40),
            start_time="10:00:00", end_time="10:30:00",
            expected_attendees=10,
        )
        self.assertRaises(frappe.ValidationError, b.insert, ignore_permissions=True)

    def test_within_capacity_succeeds(self):
        b = self._make_booking(
            conference_room="Huddle Room",
            booking_date=add_days(today(), 101),
            start_time="10:00:00", end_time="10:30:00",
            expected_attendees=3,
        )
        b.insert(ignore_permissions=True)
        self.assertTrue(b.name)

    # ── 5. Overlap Validation ──

    def test_overlapping_booking_blocked(self):
        b1 = self._make_booking(
            booking_date=add_days(today(), 110),
            start_time="10:00:00", end_time="12:00:00",
        )
        b1.insert(ignore_permissions=True)

        b2 = self._make_booking(
            booking_date=add_days(today(), 110),
            start_time="11:00:00", end_time="13:00:00",
        )
        self.assertRaises(frappe.ValidationError, b2.insert, ignore_permissions=True)

    def test_adjacent_booking_succeeds(self):
        b1 = self._make_booking(
            booking_date=add_days(today(), 111),
            start_time="10:00:00", end_time="12:00:00",
        )
        b1.insert(ignore_permissions=True)

        b2 = self._make_booking(
            meeting_title="Adjacent",
            booking_date=add_days(today(), 111),
            start_time="12:00:00", end_time="14:00:00",
        )
        b2.insert(ignore_permissions=True)
        self.assertTrue(b2.name)

    def test_same_time_different_room_succeeds(self):
        b1 = self._make_booking(
            conference_room="Boardroom A",
            booking_date=add_days(today(), 112),
        )
        b1.insert(ignore_permissions=True)

        b2 = self._make_booking(
            conference_room="Meeting Room 1",
            booking_date=add_days(today(), 112),
        )
        b2.insert(ignore_permissions=True)
        self.assertTrue(b2.name)

    # ── 6. Operating Hours ──

    def test_start_before_operating_hours_blocked(self):
        b = self._make_booking(
            conference_room="Huddle Room",
            booking_date=add_days(today(), 40),
            start_time="07:00:00", end_time="07:30:00",
            expected_attendees=2,
        )
        self.assertRaises(frappe.ValidationError, b.insert, ignore_permissions=True)

    def test_end_after_operating_hours_blocked(self):
        b = self._make_booking(
            conference_room="Huddle Room",
            booking_date=add_days(today(), 41),
            start_time="17:00:00", end_time="18:30:00",
            expected_attendees=2,
        )
        self.assertRaises(frappe.ValidationError, b.insert, ignore_permissions=True)

    def test_within_operating_hours_succeeds(self):
        b = self._make_booking(
            conference_room="Huddle Room",
            booking_date=add_days(today(), 102),
            start_time="10:00:00", end_time="10:30:00",
            expected_attendees=2,
        )
        b.insert(ignore_permissions=True)
        self.assertTrue(b.name)

    # ── 7. Auto Service Flags ──

    def test_external_auto_sets_flags(self):
        b = self._make_booking(meeting_type="External", booking_date=add_days(today(), 120))
        b.insert(ignore_permissions=True)
        self.assertEqual(b.room_cleaning_required, 1)
        self.assertEqual(b.water_required, 1)
        self.assertEqual(b.coffee_tea_required, 1)

    def test_hybrid_auto_sets_flags(self):
        b = self._make_booking(meeting_type="Hybrid", booking_date=add_days(today(), 121))
        b.insert(ignore_permissions=True)
        self.assertEqual(b.room_cleaning_required, 1)
        self.assertEqual(b.water_required, 1)
        self.assertEqual(b.coffee_tea_required, 1)

    def test_internal_does_not_set_flags(self):
        b = self._make_booking(meeting_type="Internal", booking_date=add_days(today(), 122))
        b.insert(ignore_permissions=True)
        self.assertEqual(b.room_cleaning_required, 0)
        self.assertEqual(b.water_required, 0)
        self.assertEqual(b.coffee_tea_required, 0)

    # ── 8. Submit / Cancel ──

    def test_submit_sets_approved(self):
        b = self._make_booking(booking_date=add_days(today(), 130))
        b.insert(ignore_permissions=True)
        b.submit()
        b.reload()
        self.assertEqual(b.status, "Approved")
        self.assertEqual(b.docstatus, 1)

    def test_cancel_sets_cancelled(self):
        b = self._make_booking(booking_date=add_days(today(), 131))
        b.insert(ignore_permissions=True)
        b.submit()
        b.cancel()
        b.reload()
        self.assertEqual(b.status, "Cancelled")
        self.assertEqual(b.docstatus, 2)

    # ── 9. APIs ──

    def test_get_available_rooms(self):
        from visitormanagement.conference_room.doctype.conference_room_booking.conference_room_booking import (
            get_available_rooms,
        )
        rooms = get_available_rooms(
            booking_date=add_days(today(), 150),
            start_time="10:00:00", end_time="11:00:00",
        )
        self.assertIsInstance(rooms, list)
        self.assertGreater(len(rooms), 0)
        for key in ["name", "room_name", "capacity", "location", "room_type"]:
            self.assertIn(key, rooms[0])

    def test_get_available_rooms_excludes_booked(self):
        from visitormanagement.conference_room.doctype.conference_room_booking.conference_room_booking import (
            get_available_rooms,
        )
        b = self._make_booking(
            conference_room="Huddle Room",
            booking_date=add_days(today(), 151),
            start_time="10:00:00", end_time="11:00:00",
            expected_attendees=2,
        )
        b.insert(ignore_permissions=True)
        rooms = get_available_rooms(
            booking_date=add_days(today(), 151),
            start_time="10:00:00", end_time="11:00:00",
        )
        self.assertNotIn("Huddle Room", [r.name for r in rooms])

    def test_get_room_schedule(self):
        from visitormanagement.conference_room.doctype.conference_room_booking.conference_room_booking import (
            get_room_schedule,
        )
        schedule = get_room_schedule("Boardroom A", today())
        self.assertIsInstance(schedule, list)

    def test_get_booking_events(self):
        from visitormanagement.conference_room.doctype.conference_room_booking.conference_room_booking import (
            get_booking_events,
        )
        events = get_booking_events(start=today(), end=add_days(today(), 30))
        self.assertIsInstance(events, list)

    def test_get_booking_events_filters_by_room(self):
        from visitormanagement.conference_room.doctype.conference_room_booking.conference_room_booking import (
            get_booking_events,
        )
        events = get_booking_events(
            start=today(), end=add_days(today(), 30),
            filters=json.dumps({"conference_room": "Boardroom A"}),
        )
        for e in events:
            self.assertEqual(e.conference_room, "Boardroom A")

    # ── 10. Notification ──

    def test_notification_configured(self):
        n = frappe.db.get_value(
            "Notification", "CRB Service Alert",
            ["enabled", "event", "condition", "document_type", "channel"],
            as_dict=True,
        )
        self.assertTrue(n)
        self.assertEqual(n.enabled, 1)
        self.assertEqual(n.event, "Submit")
        self.assertEqual(n.document_type, "Conference Room Booking")
        self.assertIn("External", n.condition)
        self.assertIn("Hybrid", n.condition)

    def test_notification_targets_facility_manager(self):
        recs = frappe.get_all(
            "Notification Recipient",
            filters={"parent": "CRB Service Alert"},
            fields=["receiver_by_role"],
        )
        roles = [r.receiver_by_role for r in recs]
        self.assertIn("Facility Manager", roles)

    # ── 11. Reports ──

    def test_daily_booking_schedule_report(self):
        from visitormanagement.conference_room.report.daily_booking_schedule.daily_booking_schedule import (
            execute,
        )
        columns, data = execute({"date": today()})
        self.assertEqual(len(columns), 10)
        self.assertIsInstance(data, list)

    def test_room_utilization_report(self):
        from visitormanagement.conference_room.report.room_utilization.room_utilization import (
            execute,
        )
        columns, data = execute({"from_date": today(), "to_date": add_days(today(), 30)})
        self.assertEqual(len(columns), 6)
        self.assertIsInstance(data, list)
