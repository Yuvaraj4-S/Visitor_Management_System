import json

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, today


# Rooms the booking tests reference by name. Seeded once per class run so the
# tests don't depend on whatever happens to be in the live site's data.
# Operating hours must span the time windows the tests use (08:00-18:00).
_ROOM_DEFAULTS = {
    "available_from": "08:00:00",
    "available_to": "18:00:00",
    "min_booking_minutes": 15,
    "max_booking_hours": 4,
    "location": "Test",
    "is_active": 1,
}
_REQUIRED_ROOMS = (
    {"room_name": "Boardroom A", "capacity": 12, **_ROOM_DEFAULTS},
    {"room_name": "Huddle Room", "capacity": 4, **_ROOM_DEFAULTS},
    {"room_name": "Meeting Room 1", "capacity": 8, **_ROOM_DEFAULTS},
)


class TestConferenceRoomBooking(FrappeTestCase):

    _ROOM_NAMES = tuple(spec["room_name"] for spec in _REQUIRED_ROOMS)

    @classmethod
    def _purge_test_bookings(cls):
        """Remove any bookings against our test rooms — including submitted ones
        leaked by prior test class runs that didn't roll back."""
        leaked = frappe.db.get_all(
            "Conference Room Booking",
            filters={"conference_room": ["in", cls._ROOM_NAMES]},
            fields=["name", "docstatus"],
        )
        for row in leaked:
            try:
                if row["docstatus"] == 1:
                    doc = frappe.get_doc("Conference Room Booking", row["name"])
                    doc.cancel()
                frappe.delete_doc(
                    "Conference Room Booking", row["name"],
                    force=True, ignore_permissions=True, ignore_missing=True,
                )
            except Exception:
                # Last-resort SQL delete for stuck rows
                frappe.db.sql(
                    "DELETE FROM `tabConference Room Booking` WHERE name=%s",
                    row["name"],
                )

    @classmethod
    def _purge_leaked_test_rooms(cls):
        """Remove any 'Test Valid Room *' rows leaked by a prior run of
        test_valid_room_creation. These have transient operating hours that
        can break later tests which pick 'any active Conference Room'."""
        leaked = frappe.db.get_all(
            "Conference Room",
            filters={"name": ["like", "Test Valid Room%"]},
            pluck="name",
        )
        for name in leaked:
            try:
                frappe.delete_doc(
                    "Conference Room", name,
                    force=True, ignore_permissions=True, ignore_missing=True,
                )
            except Exception:
                frappe.db.sql(
                    "DELETE FROM `tabConference Room` WHERE name=%s", name
                )

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._purge_test_bookings()
        cls._purge_leaked_test_rooms()
        # Drop any pre-existing rooms with the same names so we know they have
        # the right operating hours / capacity / max_booking_hours.
        for name in cls._ROOM_NAMES:
            if frappe.db.exists("Conference Room", name):
                frappe.delete_doc(
                    "Conference Room", name,
                    force=True, ignore_permissions=True, ignore_missing=True,
                )
        for spec in _REQUIRED_ROOMS:
            room = frappe.new_doc("Conference Room")
            room.update(spec)
            room.insert(ignore_permissions=True)
        # Test fixture: shared rooms must persist across all tests in the class.
        frappe.db.commit()  # nosemgrep: frappe-manual-commit

    @classmethod
    def tearDownClass(cls):
        cls._purge_test_bookings()
        cls._purge_leaked_test_rooms()
        for name in cls._ROOM_NAMES:
            if frappe.db.exists("Conference Room", name):
                try:
                    frappe.delete_doc(
                        "Conference Room", name,
                        force=True, ignore_permissions=True, ignore_missing=True,
                    )
                except Exception:
                    pass
        # Test fixture: persist teardown cleanup of class-level shared rows.
        frappe.db.commit()  # nosemgrep: frappe-manual-commit
        super().tearDownClass()

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
        # Explicit hours so leaked rows (if a commit slips in) don't break
        # later test runs that pick "any active Conference Room".
        room.available_from = "08:00:00"
        room.available_to = "20:00:00"
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
