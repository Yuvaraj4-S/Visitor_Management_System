"""
Visitor Management demo data generator.

Run:
    bench --site vms.local execute visitormanagement.demo.create_demo_data.run

Idempotent: re-running skips records that already exist by natural key.
"""

import base64
import frappe
from frappe.utils import add_days, add_to_date, getdate, now_datetime, today


# ─────────────────────────────────────────────────────────
# Placeholder image (1x1 transparent PNG, reused for all photo/ID fields)
# ─────────────────────────────────────────────────────────
PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)


def _log(msg):
    print(f"[DEMO] {msg}")


def ensure_placeholder_file():
    name = frappe.db.exists("File", {"file_name": "vms_demo_placeholder.png"})
    if name:
        return frappe.db.get_value("File", name, "file_url")
    f = frappe.get_doc(
        {
            "doctype": "File",
            "file_name": "vms_demo_placeholder.png",
            "is_private": 0,
            "content": base64.b64decode(PNG_B64),
            "decode": False,
        }
    ).insert(ignore_permissions=True)
    return f.file_url


# ─────────────────────────────────────────────────────────
# MASTERS
# ─────────────────────────────────────────────────────────
COMPANY = "Yuvi"


def ensure_employee(first, last, email, designation, dept_short):
    emp_name = frappe.db.exists("Employee", {"user_id": email}) or frappe.db.exists(
        "Employee", {"personal_email": email}
    )
    if emp_name:
        return emp_name

    # Ensure user exists
    if not frappe.db.exists("User", email):
        frappe.get_doc(
            {
                "doctype": "User",
                "email": email,
                "first_name": first,
                "last_name": last,
                "send_welcome_email": 0,
                "enabled": 1,
                "user_type": "System User",
                "roles": [{"role": "Employee"}],
            }
        ).insert(ignore_permissions=True)

    dept = frappe.db.get_value("Department", {"department_name": dept_short, "company": COMPANY}, "name")
    emp = frappe.get_doc(
        {
            "doctype": "Employee",
            "first_name": first,
            "last_name": last,
            "employee_name": f"{first} {last}",
            "gender": "Male" if first in {"Aarav", "Rahul", "Vikram", "Arjun"} else "Female",
            "date_of_birth": "1990-01-15",
            "date_of_joining": "2022-04-01",
            "status": "Active",
            "company": COMPANY,
            "department": dept,
            "designation": designation,
            "user_id": email,
            "personal_email": email,
            "cell_number": "9000000000",
        }
    ).insert(ignore_permissions=True, ignore_mandatory=True)
    return emp.name


def create_designations():
    for d in [
        "System Manager",
        "HR Manager",
        "Sales Manager",
        "HOD - Engineering",
        "Chief Executive Officer",
        "Hospitality Manager",
    ]:
        if not frappe.db.exists("Designation", d):
            frappe.get_doc({"doctype": "Designation", "designation_name": d}).insert(
                ignore_permissions=True
            )


def create_employees():
    create_designations()
    return {
        "sys_mgr": ensure_employee(
            "Aarav", "Shah", "aarav.shah@demo.local", "System Manager", "Management"
        ),
        "hr_mgr": ensure_employee(
            "Priya", "Rao", "priya.rao@demo.local", "HR Manager", "Human Resources"
        ),
        "sales_mgr": ensure_employee(
            "Rahul", "Mehta", "rahul.mehta@demo.local", "Sales Manager", "Sales"
        ),
        "hod": ensure_employee(
            "Neha", "Iyer", "neha.iyer@demo.local", "HOD - Engineering", "Research & Development"
        ),
        "ceo": ensure_employee(
            "Vikram", "Singh", "vikram.singh@demo.local", "Chief Executive Officer", "Management"
        ),
        "hosp_mgr": ensure_employee(
            "Deepa", "Menon", "deepa.menon@demo.local", "Hospitality Manager", "Operations"
        ),
    }


def create_conference_rooms():
    # Enterprise facility codes: HQ-{Block}{Floor}-{RoomType}{NN}
    # CR = Conference Room, BR = Boardroom, MR = Meeting Room
    rooms = [
        ("HQ-A1-CR01", "Medium Conference Room", "Block A, Floor 1", 12,
         "Projector, Whiteboard, VC, Coffee Machine"),
        ("HQ-A2-CR02", "Medium Conference Room", "Block A, Floor 2", 8,
         "Projector, Whiteboard, VC"),
        ("HQ-B3-BR01", "Large Boardroom", "Block B, Floor 3", 25,
         "Dual Screens, VC, Catering Pantry"),
        ("HQ-C1-MR01", "Small Meeting Room", "Block C, Floor 1", 6,
         "Whiteboard, VC"),
    ]
    created = []
    for name, room_type, location, capacity, amenities in rooms:
        existing = frappe.db.exists("Conference Room", name)
        if existing:
            # Repair any rooms that got the "time-of-creation" bug from an earlier run.
            frappe.db.set_value(
                "Conference Room",
                name,
                {
                    "available_from": "08:00:00",
                    "available_to": "20:00:00",
                    "room_type": room_type,
                },
            )
            created.append(name)
            continue
        doc = frappe.get_doc(
            {
                "doctype": "Conference Room",
                "room_name": name,
                "room_type": room_type,
                "location": location,
                "capacity": capacity,
                "amenities": amenities,
                "is_active": 1,
                "min_booking_minutes": 30,
                "max_booking_hours": 6,
                "available_from": "08:00:00",
                "available_to": "20:00:00",
            }
        ).insert(ignore_permissions=True, ignore_mandatory=True)
        created.append(doc.name)
    frappe.db.commit()
    return created


def create_factory_tour_areas():
    areas = [
        ("Assembly Line A", "Main assembly line - PPE required"),
        ("Packaging Bay", "Final packaging area - safety shoes mandatory"),
        ("R&D Lab", "Restricted zone - NDA + escort required"),
        ("Loading Dock", "Outbound shipments area"),
    ]
    created = []
    for name, desc in areas:
        if frappe.db.exists("Factory Tour Area", name):
            created.append(name)
            continue
        doc = frappe.get_doc(
            {
                "doctype": "Factory Tour Area",
                "area_name": name,
                "description": desc,
            }
        ).insert(ignore_permissions=True, ignore_mandatory=True)
        created.append(doc.name)
    return created


def create_blacklist():
    entries = [
        {
            "visitor_name": "Marcus Black",
            "id_proof_type": "Aadhaar",
            "id_proof_number": "999988887777",
            "reason": "Prior security incident — tailgating through gate, 2025-11-02.",
            "is_active": 1,
        },
        {
            "visitor_name": "Rogue Logistics Pvt Ltd",
            "id_proof_type": "PAN Card",
            "id_proof_number": "AAAPL1234C",
            "reason": "Unpaid dues & attempted unauthorised area access.",
            "is_active": 1,
        },
    ]
    for e in entries:
        if frappe.db.exists(
            "Visitor Blacklist", {"id_proof_number": e["id_proof_number"]}
        ):
            continue
        frappe.get_doc({"doctype": "Visitor Blacklist", **e}).insert(
            ignore_permissions=True, ignore_mandatory=True
        )


def configure_vms_settings():
    s = frappe.get_single("VMS Settings")
    s.admin_email = "vms-admin@demo.local"
    s.food_dept_email = "kitchen@demo.local"
    s.default_checkout_time = "18:00:00"
    s.max_visit_duration_hrs = 12
    s.require_visitor_photo = 1
    s.qr_scan_required_at_gate = 1
    s.require_item_declaration = 1
    s.block_check_in_without_verification = 0
    s.blacklist_action = "Block Entry"
    s.enable_badge = 1
    s.save(ignore_permissions=True)


# ─────────────────────────────────────────────────────────
# TRANSACTIONAL — Invitations
# ─────────────────────────────────────────────────────────
def create_invitations(hosts):
    invites = [
        {
            "visitor_type": "Customer",
            "visitor_email": "arjun.customer@acme.example",
            "visitor_full_name": "Arjun Kapoor",
            "visitor_mobile": "+91 9812300001",
            "host_employee": hosts["sales_mgr"],
            "visit_date": add_days(today(), 2),
            "expected_checkin": "10:00:00",
            "expected_checkout": "13:00:00",
            "purpose_of_visit": "Quarterly business review meeting with Sales team.",
            "meal_required": 1,
            "invitation_status": "Sent",
        },
        {
            "visitor_type": "Candidate",
            "visitor_email": "meera.candidate@gmail.example",
            "visitor_full_name": "Meera Krishnan",
            "visitor_mobile": "+91 9812300002",
            "host_employee": hosts["hr_mgr"],
            "visit_date": add_days(today(), 3),
            "expected_checkin": "11:00:00",
            "expected_checkout": "15:00:00",
            "purpose_of_visit": "Interview for Senior Backend Engineer role.",
            "invitation_status": "Opened",
        },
        {
            "visitor_type": "VIP",
            "visitor_email": "hon.guest@ministry.example",
            "visitor_full_name": "Hon. Ravi Sharma",
            "visitor_mobile": "+91 9812300003",
            "host_employee": hosts["ceo"],
            "visit_date": add_days(today(), 5),
            "expected_checkin": "14:00:00",
            "expected_checkout": "17:00:00",
            "purpose_of_visit": "Dignitary visit and facility tour.",
            "meal_required": 1,
            "invitation_status": "Sent",
        },
        {
            "visitor_type": "Supplier",
            "visitor_email": "logistics@vendor.example",
            "visitor_full_name": "Kumar Logistics",
            "visitor_mobile": "+91 9812300004",
            "host_employee": hosts["sys_mgr"],
            "visit_date": add_days(today(), 7),
            "expected_checkin": "09:00:00",
            "expected_checkout": "11:00:00",
            "purpose_of_visit": "Material delivery for Project Falcon.",
            "invitation_status": "Sent",
        },
    ]
    created = []
    for inv in invites:
        existing = frappe.db.exists(
            "Visitor Invitation",
            {"visitor_email": inv["visitor_email"], "visit_date": inv["visit_date"]},
        )
        if existing:
            created.append(existing)
            continue
        doc = frappe.get_doc({"doctype": "Visitor Invitation", **inv}).insert(
            ignore_permissions=True, ignore_mandatory=True
        )
        created.append(doc.name)
    return created


# ─────────────────────────────────────────────────────────
# TRANSACTIONAL — Visitor Passes
# ─────────────────────────────────────────────────────────
def create_visitor_passes(hosts, photo_url):
    passes = [
        {
            "visitor_type": "Contractor",
            "visitor_full_name": "Suresh Patil",
            "mobile_number": "+91 9812300010",
            "email_id": "suresh.patil@contractor.example",
            "company__organisation": "Patil Electricals Pvt Ltd",
            "id_proof_type": "Aadhaar",
            "id_proof_number": "111122223333",
            "id_proof_scan": photo_url,
            "visitor_photo": photo_url,
            "purpose_of_visit": "Electrical maintenance on Block A chillers.",
            "person_to_visit": hosts["sys_mgr"],
            "visit_date": today(),
            "expected_checkin": "09:30:00",
            "expected_checkout": "16:30:00",
            "vehicle_number": "MH12AB1234",
            "meal_required": 1,
        },
        {
            "visitor_type": "Candidate",
            "visitor_full_name": "Meera Krishnan",
            "mobile_number": "+91 9812300002",
            "email_id": "meera.candidate@gmail.example",
            "company__organisation": "Self (Candidate)",
            "id_proof_type": "PAN Card",
            "id_proof_number": "ABCPK1234F",
            "id_proof_scan": photo_url,
            "visitor_photo": photo_url,
            "purpose_of_visit": "Interview for Senior Backend Engineer role.",
            "person_to_visit": hosts["hr_mgr"],
            "visit_date": add_days(today(), 3),
            "expected_checkin": "11:00:00",
            "expected_checkout": "15:00:00",
            "interview_round": "Technical Round 2",
            "position_applied": "Senior Backend Engineer",
        },
        {
            "visitor_type": "Customer",
            "visitor_full_name": "Arjun Kapoor",
            "mobile_number": "+91 9812300001",
            "email_id": "arjun.customer@acme.example",
            "company__organisation": "Acme Retail Pvt Ltd",
            "id_proof_type": "Driving License",
            "id_proof_number": "MH1420200012345",
            "id_proof_scan": photo_url,
            "visitor_photo": photo_url,
            "purpose_of_visit": "Quarterly business review & contract renewal.",
            "person_to_visit": hosts["sales_mgr"],
            "visit_date": today(),
            "expected_checkin": "10:00:00",
            "expected_checkout": "13:00:00",
            "meal_required": 1,
            "conference_room": "HQ-A1-CR01",
        },
        {
            "visitor_type": "Supplier",
            "visitor_full_name": "Rakesh Gupta",
            "mobile_number": "+91 9812300020",
            "email_id": "rakesh@supplier.example",
            "company__organisation": "Gupta Metals & Alloys",
            "id_proof_type": "Aadhaar",
            "id_proof_number": "444455556666",
            "id_proof_scan": photo_url,
            "visitor_photo": photo_url,
            "purpose_of_visit": "On-site service & maintenance of shop-floor machinery.",
            "person_to_visit": hosts["sys_mgr"],
            "visit_date": today(),
            "expected_checkin": "08:00:00",
            "expected_checkout": "10:00:00",
            "vehicle_number": "MH04CD5678",
            "supplier_visit_mode": "Service",
        },
        {
            "visitor_type": "VIP",
            "visitor_full_name": "Hon. Ravi Sharma",
            "mobile_number": "+91 9812300003",
            "email_id": "hon.guest@ministry.example",
            "company__organisation": "Ministry of Commerce",
            "id_proof_type": "Passport",
            "id_proof_number": "A1234567",
            "id_proof_scan": photo_url,
            "visitor_photo": photo_url,
            "purpose_of_visit": "Dignitary facility tour and executive meeting.",
            "person_to_visit": hosts["ceo"],
            "visit_date": add_days(today(), 5),
            "expected_checkin": "14:00:00",
            "expected_checkout": "17:00:00",
            "meal_required": 1,
            "factory_tour_required": 1,
            "greeting_required": 1,
            "cab_required": 1,
            "mdceo_notified": 1,
        },
    ]
    created = []
    for p in passes:
        existing = frappe.db.exists(
            "Visitor Pass",
            {
                "id_proof_number": p["id_proof_number"],
                "visit_date": p["visit_date"],
            },
        )
        if existing:
            created.append(existing)
            continue
        doc = frappe.get_doc({"doctype": "Visitor Pass", **p})
        doc.flags.ignore_permissions = True
        doc.insert()
        created.append(doc.name)
    return created


def backfill_pass_requirements(pass_names):
    """Ensure Contractor safety fields & VIP MD/CEO notification are set before workflow."""
    for name in pass_names:
        doc = frappe.get_doc("Visitor Pass", name)
        if doc.docstatus != 0:
            continue  # can't edit submitted docs
        changed = False
        if doc.visitor_type == "Supplier" and not doc.get("supplier_visit_mode"):
            doc.supplier_visit_mode = "Service"
            changed = True
        if doc.visitor_type == "VIP" and not doc.get("mdceo_notified"):
            doc.mdceo_notified = 1
            changed = True
        # Hotel cascade creates a Hospitality Request with identical check-in/out
        # times which then fails validation. Unset hotel_required for demo data.
        if doc.get("hotel_required"):
            doc.hotel_required = 0
            changed = True
        if changed:
            doc.flags.ignore_permissions = True
            doc.save()


def walk_passes_to_approved(pass_names):
    """Walk each Visitor Pass through its workflow: Submit -> Approve (VIP needs two Approves)."""
    from frappe.model.workflow import apply_workflow

    # actions needed after Submit to reach Approved
    post_submit_actions = {
        "Contractor": ["Approve"],
        "Supplier": ["Approve"],
        "Customer": ["Approve"],
        "Candidate": ["Approve"],
        "VIP": ["Approve", "Approve"],  # HOD then CEO
    }
    for name in pass_names:
        doc = frappe.get_doc("Visitor Pass", name)
        if doc.workflow_state and doc.workflow_state != "Draft":
            continue
        try:
            apply_workflow(doc, "Submit")
        except Exception as exc:
            _log(f"  [Submit] failed for {name}: {exc}")
            continue
        doc.reload()
        for action in post_submit_actions.get(doc.visitor_type, []):
            try:
                apply_workflow(doc, action)
                doc.reload()
            except Exception as exc:
                _log(f"  [{action}] failed for {name} at state {doc.workflow_state}: {exc}")
                break


# ─────────────────────────────────────────────────────────
# TRANSACTIONAL — Security Logs (Check-In / Check-Out)
# ─────────────────────────────────────────────────────────
def create_security_logs(pass_names, photo_url):
    created = []
    for name in pass_names:
        doc = frappe.get_doc("Visitor Pass", name)
        if doc.status not in {"Approved", "Items Verified"}:
            continue
        if doc.visit_date and str(doc.visit_date) != today():
            # Only check-in passes scheduled for today
            continue
        gate_map = {
            "VIP": "VIP Entrance",
            "Supplier": "Loading Dock",
            "Contractor": "Back Gate",
        }
        gate = gate_map.get(doc.visitor_type, "Main Gate")

        checkin_dt = now_datetime()

        log = frappe.get_doc(
            {
                "doctype": "Security Log",
                "visitor_pass": name,
                "event_type": "Check-In",
                "gate_name": gate,
                "check_in_date_time": checkin_dt,
                "photo_at_gate": photo_url,
                "id_proof_match": 1,
                "pass_photo_match": 1,
                "temperature": 36.7,
                "symptoms_flag": 0,
                "remarks": "Verified at gate — photo + ID match; PPE issued if required.",
            }
        )
        log.flags.ignore_permissions = True
        log.insert()
        log.submit()
        created.append(log.name)

        # For Contractor & Supplier → also check them out
        if doc.visitor_type in {"Contractor", "Supplier"}:
            checkout = frappe.get_doc(
                {
                    "doctype": "Security Log",
                    "visitor_pass": name,
                    "event_type": "Check-Out",
                    "gate_name": gate,
                    "check_out_date_time": add_to_date(checkin_dt, hours=3),
                    "photo_at_gate": photo_url,
                    "id_proof_match": 1,
                    "pass_photo_match": 1,
                    "remarks": "Visit completed. Badge returned. Area cleared.",
                }
            )
            checkout.flags.ignore_permissions = True
            checkout.insert()
            checkout.submit()
            created.append(checkout.name)
    return created


# ─────────────────────────────────────────────────────────
# TRANSACTIONAL — Conference Room Bookings
# ─────────────────────────────────────────────────────────
def create_room_bookings(hosts):
    bookings = [
        {
            "meeting_title": "Acme QBR - Retail vertical",
            "conference_room": "HQ-A1-CR01",
            "meeting_type": "External",
            "booking_date": add_days(today(), 1),
            "start_time": "10:00:00",
            "end_time": "12:00:00",
            "booked_by": hosts["sales_mgr"],
            "expected_attendees": 8,
            "notes": "Projector + VC required. External guests from Acme Retail.",
        },
        {
            "meeting_title": "Engineering sprint planning",
            "conference_room": "HQ-A2-CR02",
            "meeting_type": "Internal",
            "booking_date": add_days(today(), 1),
            "start_time": "14:00:00",
            "end_time": "15:30:00",
            "booked_by": hosts["hod"],
            "expected_attendees": 6,
        },
        {
            "meeting_title": "VIP briefing — Ministry delegation",
            "conference_room": "HQ-B3-BR01",
            "meeting_type": "Hybrid",
            "booking_date": add_days(today(), 5),
            "start_time": "14:30:00",
            "end_time": "16:30:00",
            "booked_by": hosts["ceo"],
            "expected_attendees": 20,
            "notes": "Dignitary visit — dual screens, refreshments, security sweep.",
        },
    ]
    created = []
    for b in bookings:
        existing = frappe.db.exists(
            "Conference Room Booking",
            {
                "conference_room": b["conference_room"],
                "booking_date": b["booking_date"],
                "start_time": b["start_time"],
            },
        )
        if existing:
            created.append(existing)
            continue
        doc = frappe.get_doc({"doctype": "Conference Room Booking", **b})
        doc.flags.ignore_permissions = True
        doc.insert()
        try:
            doc.submit()
        except Exception as exc:
            _log(f"  booking submit failed: {exc}")
        created.append(doc.name)
    return created


# ─────────────────────────────────────────────────────────
# TRANSACTIONAL — Job Applicant (triggers Candidate invitation flow)
# ─────────────────────────────────────────────────────────
def create_job_applicant(hosts):
    email = "arun.jobseeker@gmail.example"
    if frappe.db.exists("Job Applicant", {"email_id": email}):
        return frappe.db.get_value("Job Applicant", {"email_id": email}, "name")
    doc = frappe.get_doc(
        {
            "doctype": "Job Applicant",
            "applicant_name": "Arun Desai",
            "email_id": email,
            "phone_number": "+91 9812300040",
            "status": "Open",
            "country": "India",
            "interview_mode": "Offline",
            "interview_host": hosts["hr_mgr"],
            "interview_visit_date": add_days(today(), 4),
            "interview_checkin_time": "10:30:00",
            "interview_checkout_time": "13:30:00",
        }
    )
    doc.flags.ignore_permissions = True
    doc.insert(ignore_mandatory=True)
    return doc.name


# ─────────────────────────────────────────────────────────
# MAIN ENTRYPOINT
# ─────────────────────────────────────────────────────────
def run():
    _log("Starting demo data generation")
    photo_url = ensure_placeholder_file()
    _log(f"Placeholder file: {photo_url}")

    hosts = create_employees()
    _log(f"Employees ready: {list(hosts.values())}")

    rooms = create_conference_rooms()
    _log(f"Conference rooms: {rooms}")

    areas = create_factory_tour_areas()
    _log(f"Factory tour areas: {areas}")

    create_blacklist()
    _log("Blacklist entries ready")

    configure_vms_settings()
    _log("VMS Settings configured")

    frappe.db.commit()

    invites = create_invitations(hosts)
    _log(f"Invitations: {invites}")

    passes = create_visitor_passes(hosts, photo_url)
    _log(f"Passes created: {passes}")

    # Walk passes through workflow (Submit -> Approve...) to reach Approved
    backfill_pass_requirements(passes)
    walk_passes_to_approved(passes)

    bookings = create_room_bookings(hosts)
    _log(f"Room bookings: {bookings}")

    applicant = create_job_applicant(hosts)
    _log(f"Job applicant: {applicant}")

    security_logs = create_security_logs(passes, photo_url)
    _log(f"Security logs: {security_logs}")

    frappe.db.commit()
    _log("Done.")


# ═════════════════════════════════════════════════════════════
# BULK SEEDING — 50 records per visitor type
# Run:
#     bench --site vms.local execute visitormanagement.demo.create_demo_data.run_bulk
# ═════════════════════════════════════════════════════════════

import random
from itertools import cycle

_FIRST_NAMES = [
    "Aarav", "Aditi", "Amit", "Anika", "Arjun", "Bhavna", "Chetan", "Deepak",
    "Divya", "Esha", "Farhan", "Gauri", "Hemant", "Indira", "Jayesh", "Karan",
    "Lata", "Manish", "Neha", "Omkar", "Pooja", "Rahul", "Sneha", "Tanvi", "Uday",
]
_LAST_NAMES = [
    "Agarwal", "Bhat", "Chandra", "Desai", "Elango", "Fernandes", "Gupta",
    "Hegde", "Iyer", "Joshi", "Kumar", "Luthra", "Mehta", "Nair", "Oberoi",
    "Patel", "Qureshi", "Rao", "Sharma", "Trivedi", "Verma", "Wadhwa",
    "Xavier", "Yadav", "Zaidi",
]

_COMPANIES_BY_TYPE = {
    "Contractor": ["Patil Electricals", "Sharma Constructions", "Kumar Plumbing",
                   "Reliable HVAC", "BuildRight Pvt"],
    "Candidate":  ["Self (Candidate)"],
    "Customer":   ["Acme Retail", "Globex Ltd", "Initech", "Umbrella Corp",
                   "Stark Industries", "Wayne Enterprises"],
    "Supplier":   ["Gupta Metals", "SteelFlex Ltd", "LogiCarrier",
                   "PrimeVendor Inc", "Kumar Logistics"],
    "VIP":        ["Ministry of Commerce", "ISRO", "SEBI", "NITI Aayog", "RBI"],
}

_PURPOSE_TEMPLATE = {
    "Contractor": "Scheduled maintenance — {subject}.",
    "Candidate":  "Interview for {subject} role.",
    "Customer":   "Business meeting — {subject}.",
    "Supplier":   "On-site service — {subject}.",
    "VIP":        "Dignitary visit — {subject}.",
}
_SUBJECTS = {
    "Contractor": ["chiller repair", "UPS replacement", "conveyor overhaul", "DG servicing"],
    "Candidate":  ["Senior Backend Engineer", "Data Scientist", "Product Manager", "QA Lead"],
    "Customer":   ["quarterly review", "contract renewal", "product demo", "tech walkthrough"],
    "Supplier":   ["shop-floor audit", "equipment calibration", "bearing service"],
    "VIP":        ["facility tour", "executive briefing", "inauguration ceremony"],
}

_HOST_BY_TYPE = {"Contractor": "sys_mgr", "Candidate": "hr_mgr", "Customer": "sales_mgr",
                 "Supplier": "sys_mgr", "VIP": "ceo"}
_PREFIX_BY_TYPE = {"Contractor": "CON", "Candidate": "CAN", "Customer": "CUS",
                   "Supplier": "SUP", "VIP": "VIP"}
_PHONE_RANGE = {"Contractor": "9810", "Candidate": "9820", "Customer": "9830",
                "Supplier": "9840", "VIP": "9850"}
_ID_TYPES = ["Aadhaar", "PAN Card", "Passport", "Driving License"]
_GATE_BY_TYPE = {"VIP": "VIP Entrance", "Supplier": "Loading Dock",
                 "Contractor": "Back Gate"}


def _fake_visitor(vtype, idx):
    """Deterministic visitor identity for (vtype, idx). Idempotent."""
    rng = random.Random(f"{vtype}-{idx}")
    first = _FIRST_NAMES[idx % len(_FIRST_NAMES)]
    last = _LAST_NAMES[(idx // len(_FIRST_NAMES)) % len(_LAST_NAMES)]
    lower = vtype.lower()
    email = f"{lower}{idx:05d}@demo.local"
    mobile = f"+91 {_PHONE_RANGE[vtype]}{idx:06d}"
    id_proof_type = _ID_TYPES[idx % len(_ID_TYPES)]
    tp = _PREFIX_BY_TYPE[vtype]
    # 2-letter suffix per vtype keeps PANs unique across visitor types
    pan_type_suffix = {"Contractor": "CN", "Candidate": "CA", "Customer": "CS",
                       "Supplier": "SP", "VIP": "VP"}[vtype]
    if id_proof_type == "Aadhaar":
        id_proof_number = f"8{idx:011d}"                       # 12-digit
    elif id_proof_type == "PAN Card":
        id_proof_number = f"DEM{pan_type_suffix}{idx:04d}F"    # ABCDE1234F shape
    elif id_proof_type == "Passport":
        id_proof_number = f"{tp[0]}{idx:07d}"
    else:
        id_proof_number = f"MH14{tp}{idx:07d}"
    return {
        "full_name": f"{first} {last}",
        "email": email,
        "mobile": mobile,
        "id_proof_type": id_proof_type,
        "id_proof_number": id_proof_number,
        "company": rng.choice(_COMPANIES_BY_TYPE[vtype]),
        "purpose": _PURPOSE_TEMPLATE[vtype].format(subject=rng.choice(_SUBJECTS[vtype])),
    }


def _target_state_for(idx):
    """50-slot distribution → workflow state target."""
    if idx < 5:   return "draft"
    if idx < 15:  return "pending"
    if idx < 25:  return "approved_future"
    if idx < 30:  return "approved_today"
    if idx < 40:  return "checked_in"
    return "checked_out"


def _visit_date_for(state, idx):
    if state == "draft":           return add_days(today(), idx % 15)
    if state == "pending":         return add_days(today(), 1 + (idx % 14))
    if state == "approved_future": return add_days(today(), 2 + (idx % 13))
    return today()  # approved_today / checked_in / checked_out


def _service_flags(vtype, idx):
    """
    Random service flags. hotel_required is skipped because the lifecycle auto-creates
    a Hospitality Request with identical default check-in/check-out times, which then
    fails "Hotel check-out must be after check-in" on save.
    """
    rng = random.Random(f"svc-{vtype}-{idx}")
    return {
        "meal_required":          1 if rng.random() < 0.40 else 0,
        "cab_required":           1 if rng.random() < 0.20 else 0,
        "factory_tour_required":  1 if rng.random() < 0.15 else 0,
        "greeting_required":      1 if rng.random() < (0.80 if vtype == "VIP" else 0.25) else 0,
    }


def bulk_create_passes(per_type, hosts, photo_url):
    """Insert `per_type` passes per visitor type. Return [(pass_name, target_state), ...]."""
    created = []
    for vtype in ("Contractor", "Candidate", "Customer", "Supplier", "VIP"):
        host_name = hosts[_HOST_BY_TYPE[vtype]]
        inserted = 0
        for idx in range(per_type):
            v = _fake_visitor(vtype, idx)
            state = _target_state_for(idx)
            vdate = _visit_date_for(state, idx)

            existing = frappe.db.exists(
                "Visitor Pass",
                {"id_proof_number": v["id_proof_number"], "visit_date": vdate},
            )
            if existing:
                created.append((existing, state))
                continue

            data = {
                "doctype": "Visitor Pass",
                "visitor_type": vtype,
                "visitor_full_name": v["full_name"],
                "mobile_number": v["mobile"],
                "email_id": v["email"],
                "company__organisation": v["company"],
                "id_proof_type": v["id_proof_type"],
                "id_proof_number": v["id_proof_number"],
                "id_proof_scan": photo_url,
                "visitor_photo": photo_url,
                "purpose_of_visit": v["purpose"],
                "person_to_visit": host_name,
                "visit_date": vdate,
                "expected_checkin": "10:00:00",
                "expected_checkout": "16:00:00",
                **_service_flags(vtype, idx),
            }
            if vtype == "Supplier":
                data["supplier_visit_mode"] = "Service"
            elif vtype == "VIP":
                data["mdceo_notified"] = 1

            try:
                doc = frappe.get_doc(data)
                doc.flags.ignore_permissions = True
                doc.insert()
                created.append((doc.name, state))
                inserted += 1
                if inserted % 20 == 0:
                    frappe.db.commit()
            except Exception as exc:
                _log(f"  [insert {vtype} idx={idx}] {exc}")
        _log(f"  {vtype}: inserted {inserted} new (seen {per_type} total)")
    frappe.db.commit()
    return created


def bulk_drive_to_state(passes_with_targets, photo_url):
    """For each (name, target), run the workflow + create Security Log(s) as needed."""
    from frappe.model.workflow import apply_workflow

    post_submit = {
        "Contractor": ["Approve"], "Supplier": ["Approve"],
        "Customer": ["Approve"], "Candidate": ["Approve"],
        "VIP": ["Approve", "Approve"],
    }

    processed = 0
    for name, target in passes_with_targets:
        doc = frappe.get_doc("Visitor Pass", name)
        state_now = doc.workflow_state or "Draft"

        try:
            if target == "draft":
                pass

            elif target == "pending":
                if state_now == "Draft":
                    apply_workflow(doc, "Submit")

            elif target in ("approved_future", "approved_today", "checked_in", "checked_out"):
                if state_now == "Draft":
                    apply_workflow(doc, "Submit")
                    doc.reload()
                for action in post_submit.get(doc.visitor_type, []):
                    if doc.workflow_state == "Approved":
                        break
                    apply_workflow(doc, action)
                    doc.reload()

                if target in ("checked_in", "checked_out") and doc.status in {"Approved", "Items Verified"}:
                    gate = _GATE_BY_TYPE.get(doc.visitor_type, "Main Gate")
                    checkin_dt = now_datetime()
                    ci = frappe.get_doc({
                        "doctype": "Security Log",
                        "visitor_pass": name,
                        "event_type": "Check-In",
                        "gate_name": gate,
                        "check_in_date_time": checkin_dt,
                        "photo_at_gate": photo_url,
                        "id_proof_match": 1,
                        "pass_photo_match": 1,
                        "temperature": 36.5 + (processed % 10) * 0.1,
                        "symptoms_flag": 0,
                        "remarks": "Gate verification OK.",
                    })
                    ci.flags.ignore_permissions = True
                    ci.insert()
                    ci.submit()

                    if target == "checked_out":
                        co = frappe.get_doc({
                            "doctype": "Security Log",
                            "visitor_pass": name,
                            "event_type": "Check-Out",
                            "gate_name": gate,
                            "check_out_date_time": add_to_date(checkin_dt, hours=2),
                            "photo_at_gate": photo_url,
                            "id_proof_match": 1,
                            "pass_photo_match": 1,
                            "remarks": "Visit completed. Badge returned.",
                        })
                        co.flags.ignore_permissions = True
                        co.insert()
                        co.submit()
        except Exception as exc:
            _log(f"  [walk {name} -> {target}] {exc}")

        processed += 1
        if processed % 20 == 0:
            frappe.db.commit()
    frappe.db.commit()


def bulk_create_room_bookings(hosts, count=30):
    rooms = ["HQ-A1-CR01", "HQ-A2-CR02", "HQ-B3-BR01", "HQ-C1-MR01"]
    slots = [("09:00:00", "10:00:00"), ("10:30:00", "12:00:00"),
             ("13:00:00", "14:30:00"), ("15:00:00", "16:30:00"),
             ("16:45:00", "17:45:00")]
    types_iter = cycle(["Internal", "External", "Hybrid"])
    host_keys = ["sales_mgr", "hod", "ceo", "hr_mgr", "hosp_mgr"]
    created = 0
    for i in range(count):
        room = rooms[i % len(rooms)]
        slot_start, slot_end = slots[i % len(slots)]
        date = add_days(today(), 1 + (i % 14))
        mtg_type = next(types_iter)
        booker = hosts[host_keys[i % len(host_keys)]]
        if frappe.db.exists("Conference Room Booking", {
            "conference_room": room, "booking_date": date, "start_time": slot_start,
        }):
            continue
        try:
            doc = frappe.get_doc({
                "doctype": "Conference Room Booking",
                "meeting_title": f"Meeting #{i+1:03d} — {mtg_type}",
                "conference_room": room,
                "meeting_type": mtg_type,
                "booking_date": date,
                "start_time": slot_start,
                "end_time": slot_end,
                "booked_by": booker,
                "expected_attendees": 4 + (i % 6),
                "notes": f"Auto-generated bulk booking #{i+1}.",
            })
            doc.flags.ignore_permissions = True
            doc.insert()
            doc.submit()
            created += 1
        except Exception as exc:
            _log(f"  [booking #{i+1}] {exc}")
    frappe.db.commit()
    return created


def bulk_create_invitations(hosts, count=20):
    types_iter = cycle(["Customer", "Candidate", "VIP", "Supplier", "Contractor"])
    status_iter = cycle(["Sent", "Opened", "Saved", "Submitted"])
    host_map = {"Customer": "sales_mgr", "Candidate": "hr_mgr", "VIP": "ceo",
                "Supplier": "sys_mgr", "Contractor": "sys_mgr"}
    created = 0
    for i in range(count):
        vtype = next(types_iter)
        status = next(status_iter)
        visit_date = add_days(today(), 1 + (i % 14))
        email = f"bulk{i:03d}.{vtype.lower()}@invite.demo"
        if frappe.db.exists("Visitor Invitation",
                            {"visitor_email": email, "visit_date": visit_date}):
            continue
        try:
            doc = frappe.get_doc({
                "doctype": "Visitor Invitation",
                "visitor_type": vtype,
                "visitor_email": email,
                "visitor_full_name": f"Bulk Invitee {i+1:03d}",
                "visitor_mobile": f"+91 990{i:07d}",
                "host_employee": hosts[host_map[vtype]],
                "visit_date": visit_date,
                "expected_checkin": "10:00:00",
                "expected_checkout": "13:00:00",
                "purpose_of_visit": f"Bulk pre-registration #{i+1}.",
                "invitation_status": status,
            })
            doc.flags.ignore_permissions = True
            doc.insert(ignore_mandatory=True)
            created += 1
        except Exception as exc:
            _log(f"  [invite #{i+1}] {exc}")
    frappe.db.commit()
    return created


def bulk_create_job_applicants(hosts, count=5):
    roles = ["Backend Engineer", "Data Scientist", "Product Designer", "QA Lead", "DevOps Engineer"]
    created = 0
    for i in range(count):
        email = f"applicant{i+1:03d}@careers.demo"
        if frappe.db.exists("Job Applicant", {"email_id": email}):
            continue
        try:
            doc = frappe.get_doc({
                "doctype": "Job Applicant",
                "applicant_name": f"Applicant {i+1:03d} - {roles[i % len(roles)]}",
                "email_id": email,
                "phone_number": f"+91 99300{i:05d}",
                "status": "Open",
                "country": "India",
                "interview_mode": "Offline",
                "interview_host": hosts["hr_mgr"],
                "interview_visit_date": add_days(today(), 3 + i),
                "interview_checkin_time": "11:00:00",
                "interview_checkout_time": "13:00:00",
            })
            doc.flags.ignore_permissions = True
            doc.insert(ignore_mandatory=True)
            created += 1
        except Exception as exc:
            _log(f"  [applicant #{i+1}] {exc}")
    frappe.db.commit()
    return created


def run_bulk(per_type=50):
    """Seed ~50 visitor passes per type plus varied transactional records."""
    per_type = int(per_type)
    _log(f"Starting BULK demo data generation: {per_type} per type")
    frappe.flags.in_import = True  # suppress notification emails

    photo_url = ensure_placeholder_file()
    hosts = create_employees()
    create_conference_rooms()
    create_factory_tour_areas()
    create_blacklist()
    configure_vms_settings()
    frappe.db.commit()
    _log("Masters ready")

    passes = bulk_create_passes(per_type, hosts, photo_url)
    _log(f"Passes resolved: {len(passes)} (new + pre-existing)")

    backfill_pass_requirements([p for p, _ in passes])
    bulk_drive_to_state(passes, photo_url)
    _log("Workflow + Security Logs complete")

    n_bookings = bulk_create_room_bookings(hosts)
    n_invs = bulk_create_invitations(hosts)
    n_apps = bulk_create_job_applicants(hosts)
    _log(f"Bookings={n_bookings}, Invitations={n_invs}, Job Applicants={n_apps}")

    frappe.db.commit()
    _log("BULK demo data complete.")


# ═════════════════════════════════════════════════════════════
# BUSINESS MASTER SEEDING — for Visitor Pass auto-fetch
# Seeds Supplier / Contractor (as Supplier) / Customer / Lead / Job Applicant
# plus primary Contact + Address per org, so auto-fetch populates
# visitor_full_name / mobile_number / email_id / company__organisation.
# Run:
#     bench --site vms.local execute visitormanagement.demo.create_demo_data.seed_business_masters
# ═════════════════════════════════════════════════════════════

_COMPANY = "Yuvi"

_CITIES = [
    ("Mumbai",       "Maharashtra", "400001"),
    ("Pune",         "Maharashtra", "411001"),
    ("Nagpur",       "Maharashtra", "440001"),
    ("Chennai",      "Tamil Nadu",  "600001"),
    ("Coimbatore",   "Tamil Nadu",  "641001"),
    ("Bangalore",    "Karnataka",   "560001"),
    ("Mysore",       "Karnataka",   "570001"),
    ("Hyderabad",    "Telangana",   "500001"),
    ("Delhi",        "Delhi",       "110001"),
    ("Gurgaon",      "Haryana",     "122001"),
    ("Noida",        "Uttar Pradesh","201301"),
    ("Kolkata",      "West Bengal", "700001"),
    ("Ahmedabad",    "Gujarat",     "380001"),
    ("Surat",        "Gujarat",     "395001"),
    ("Jaipur",       "Rajasthan",   "302001"),
    ("Kochi",        "Kerala",      "682001"),
    ("Thiruvananthapuram","Kerala", "695001"),
    ("Bhubaneswar",  "Odisha",      "751001"),
    ("Indore",       "Madhya Pradesh","452001"),
    ("Chandigarh",   "Chandigarh",  "160001"),
]

# ── 20 material-vendor companies ─────────────────────────────
_SUPPLIER_SEED = [
    ("Acme Raw Materials Pvt Ltd",     "Raw Material",  "Manufacturing",  "Priya",   "Sharma"),
    ("Prime Steel Industries",         "Raw Material",  "Manufacturing",  "Rahul",   "Mehta"),
    ("Summit Metals & Alloys",         "Raw Material",  "Manufacturing",  "Anita",   "Rao"),
    ("Orion Chemicals Ltd",            "Raw Material",  "Chemicals",      "Deepak",  "Singh"),
    ("Zenith Polymer Co",              "Raw Material",  "Chemicals",      "Neha",    "Iyer"),
    ("Apex Copper Works",              "Raw Material",  "Manufacturing",  "Vikas",   "Patel"),
    ("Bright Aluminum Pvt Ltd",        "Raw Material",  "Manufacturing",  "Kavita",  "Nair"),
    ("Core Ceramics Ltd",              "Raw Material",  "Manufacturing",  "Arjun",   "Kumar"),
    ("Nova Rubber Industries",         "Raw Material",  "Manufacturing",  "Pooja",   "Gupta"),
    ("Delta Wire & Cable",             "Electrical",    "Electrical",     "Sneha",   "Verma"),
    ("Fusion Electronic Components",   "Electrical",    "Electronics",    "Karan",   "Joshi"),
    ("Granite Hardware Corp",          "Hardware",      "Manufacturing",  "Manish",  "Desai"),
    ("Harbor Logistics Pvt Ltd",       "Distributor",   "Transportation", "Divya",   "Bhat"),
    ("Ivy Paints & Coatings",          "Raw Material",  "Chemicals",      "Farhan",  "Qureshi"),
    ("Jade Packaging Solutions",       "Raw Material",  "Manufacturing",  "Esha",    "Trivedi"),
    ("Kismet Pharma Distributors",     "Pharmaceutical","Pharmaceuticals","Uday",    "Fernandes"),
    ("Lumina LED Lighting Co",         "Electrical",    "Electrical",     "Indira",  "Chandra"),
    ("Monarch Fasteners Industries",   "Hardware",      "Manufacturing",  "Bhavna",  "Agarwal"),
    ("Nimbus Cooling Systems",         "Electrical",    "Manufacturing",  "Hemant",  "Luthra"),
    ("Quantum Bearings Pvt Ltd",       "Hardware",      "Manufacturing",  "Gauri",   "Wadhwa"),
]

# ── 20 service-contractor companies ──────────────────────────
_CONTRACTOR_SEED = [
    ("BuildRight Construction Pvt Ltd",  "Services",    "Construction",   "Sanjay",  "Patil"),
    ("Reliable HVAC Services",           "Services",    "Services",       "Meena",   "Dubey"),
    ("Crystal Facility Management",      "Services",    "Services",       "Ramesh",  "Choudhary"),
    ("Dynamic Electrical Works",         "Services",    "Services",       "Swati",   "Bansal"),
    ("Elite Security Services",          "Services",    "Services",       "Yogesh",  "Kulkarni"),
    ("Prime Plumbing Solutions",         "Services",    "Services",       "Lalita",  "Pillai",),
    ("Nexus Network Cabling",            "Services",    "Technology",     "Tarun",   "Reddy"),
    ("Summit Civil Contractors",         "Services",    "Construction",   "Aparna",  "Shetty"),
    ("Orion Landscaping Pvt Ltd",        "Services",    "Services",       "Naveen",  "Krishnan"),
    ("Zenith Cleaning Services",         "Services",    "Services",       "Shruti",  "Roy"),
    ("Bright Painting Contractors",      "Services",    "Construction",   "Vivek",   "Menon"),
    ("Core IT Infrastructure Services",  "Services",    "Technology",     "Priyanka","Jain"),
    ("Delta Mechanical Repairs",         "Services",    "Manufacturing",  "Ganesh",  "Kamat"),
    ("Fusion Fire Safety Systems",       "Services",    "Services",       "Kiran",   "Saxena"),
    ("Granite Interior Fit-Outs",        "Services",    "Construction",   "Neeta",   "Thakur"),
    ("Harbor Waste Management",          "Services",    "Services",       "Mohan",   "Bhat"),
    ("Ivy Pest Control Pvt Ltd",         "Services",    "Services",       "Sunita",  "Chauhan"),
    ("Jade Catering Services",           "Services",    "Services",       "Rakesh",  "Sinha"),
    ("Kismet Elevator Maintenance",      "Services",    "Services",       "Leela",   "D'Souza"),
    ("Lumina Audio-Visual Installations","Services",    "Services",       "Abhijit", "Kar"),
]

# ── 20 B2B customers ─────────────────────────────────────────
_CUSTOMER_SEED = [
    ("Acme Retail Pvt Ltd",          "Commercial", "Retail",            "Kunal",   "Kapoor"),
    ("Globex Industries Ltd",        "Commercial", "Manufacturing",     "Ritu",    "Malhotra"),
    ("Initech Solutions",            "Commercial", "Technology",        "Vishal",  "Singh"),
    ("Umbrella Corporation",         "Commercial", "Pharmaceuticals",   "Simran",  "Kaur"),
    ("Stark Industries Pvt Ltd",     "Commercial", "Manufacturing",     "Anand",   "Iyer"),
    ("Wayne Enterprises Ltd",        "Commercial", "Technology",        "Mitali",  "Gandhi"),
    ("Oscorp Technologies",          "Commercial", "Technology",        "Rohit",   "Varma"),
    ("Cyberdyne Systems",            "Commercial", "Technology",        "Jahnavi", "Reddy"),
    ("Massive Dynamic Corp",         "Commercial", "Manufacturing",     "Aditi",   "Bose"),
    ("Tyrell Industries",            "Commercial", "Technology",        "Nikhil",  "Saxena"),
    ("Soylent Corp",                 "Commercial", "Food",              "Shalini", "Menon"),
    ("Weyland-Yutani Ltd",           "Commercial", "Manufacturing",     "Rajesh",  "Pandey"),
    ("InGen Biotech",                "Commercial", "Pharmaceuticals",   "Aparna",  "Chatterjee"),
    ("Pied Piper Networks",          "Commercial", "Technology",        "Harsh",   "Vyas"),
    ("Hooli Digital India",          "Commercial", "Technology",        "Tanvi",   "Joshi"),
    ("Government Procurement Office","Government", "Other",             "Ashok",   "Gupta"),
    ("Federal Defense Services",     "Government", "Other",             "Ravi",    "Shankar"),
    ("State Health Department",      "Government", "Pharmaceuticals",   "Lakshmi", "Pillai"),
    ("Helping Hands NGO",            "Non Profit", "Other",             "Farah",   "Khan"),
    ("Green Earth Foundation",       "Non Profit", "Other",             "Darshan", "Nair"),
]

# ── 20 leads ─────────────────────────────────────────────────
_LEAD_SEED = [
    ("Sameer Tandon",      "Rainforest Retail",   "Commercial"),
    ("Aanya Kapoor",       "Sunrise Solutions",   "Commercial"),
    ("Vikrant Desai",      "Mountain Peaks Ltd",  "Commercial"),
    ("Shivani Nair",       "Ocean Breeze Corp",   "Commercial"),
    ("Rohan Batra",        "Desert Rose Pvt Ltd", "Commercial"),
    ("Pallavi Rao",        "Urban Nest Homes",    "Commercial"),
    ("Karthik Subramanian","TechWave Systems",    "Commercial"),
    ("Ishita Verma",       "SkyHigh Aviation",    "Commercial"),
    ("Dev Malhotra",       "GreenGrow Agri",      "Commercial"),
    ("Zara Siddiqui",      "PureFlow Water Tech", "Commercial"),
    ("Abhishek Ghosh",     "Flameline Energy",    "Commercial"),
    ("Mehak Arora",        "Starfield Research",  "Commercial"),
    ("Arvind Gokhale",     "CityScape Builders",  "Commercial"),
    ("Natasha D'Costa",    "Ripple Fintech",      "Commercial"),
    ("Suraj Balan",        "Orbital Logistics",   "Commercial"),
    ("Preeti Mathur",      "Harmony Healthcare",  "Commercial"),
    ("Aryan Thakkar",      "Peak Performance Co", "Commercial"),
    ("Kashvi Bhandari",    "Polar Cold Storage",  "Commercial"),
    ("Nihal Aiyar",        "Rapid Couriers",      "Commercial"),
    ("Ananya Dutta",       "Evergreen Nursery",   "Commercial"),
]

# ── 20 job applicants (for Candidate visitor flow) ───────────
_JOB_APPLICANT_SEED = [
    ("Kabir Sethi",       "kabir.sethi@jobmail.demo",      "+91 98765 40001", "Senior Backend Engineer"),
    ("Riya Banerjee",     "riya.banerjee@jobmail.demo",    "+91 98765 40002", "Data Scientist"),
    ("Aaryan Khanna",     "aaryan.khanna@jobmail.demo",    "+91 98765 40003", "Product Manager"),
    ("Saanvi Ahuja",      "saanvi.ahuja@jobmail.demo",     "+91 98765 40004", "QA Lead"),
    ("Dhruv Chauhan",     "dhruv.chauhan@jobmail.demo",    "+91 98765 40005", "DevOps Engineer"),
    ("Anvi Mishra",       "anvi.mishra@jobmail.demo",      "+91 98765 40006", "Frontend Engineer"),
    ("Ayush Bhattacharya","ayush.b@jobmail.demo",          "+91 98765 40007", "UX Designer"),
    ("Mira Jayaraman",    "mira.jayaraman@jobmail.demo",   "+91 98765 40008", "Data Engineer"),
    ("Vedant Shenoy",     "vedant.shenoy@jobmail.demo",    "+91 98765 40009", "Machine Learning Engineer"),
    ("Kiara Pandit",      "kiara.pandit@jobmail.demo",     "+91 98765 40010", "HR Business Partner"),
    ("Ronak Gill",        "ronak.gill@jobmail.demo",       "+91 98765 40011", "Financial Analyst"),
    ("Aisha Kapadia",     "aisha.kapadia@jobmail.demo",    "+91 98765 40012", "Marketing Manager"),
    ("Shourya Rastogi",   "shourya.r@jobmail.demo",        "+91 98765 40013", "Security Engineer"),
    ("Diya Chopra",       "diya.chopra@jobmail.demo",      "+91 98765 40014", "Business Analyst"),
    ("Ishaan Khurana",    "ishaan.khurana@jobmail.demo",   "+91 98765 40015", "Technical Writer"),
    ("Navya Iyer",        "navya.iyer@jobmail.demo",       "+91 98765 40016", "Sales Development Rep"),
    ("Yuvraj Goyal",      "yuvraj.goyal@jobmail.demo",     "+91 98765 40017", "Platform Engineer"),
    ("Myra Saxena",       "myra.saxena@jobmail.demo",      "+91 98765 40018", "Customer Success Manager"),
    ("Advait Mukherjee",  "advait.m@jobmail.demo",         "+91 98765 40019", "Solution Architect"),
    ("Sara Fernandes",    "sara.fernandes@jobmail.demo",   "+91 98765 40020", "Site Reliability Engineer"),
]


# ── helpers ──────────────────────────────────────────────────
def _ensure_contact_for(link_doctype, link_name, first_name, last_name,
                        email, mobile, designation=None):
    """Create a primary Contact linked to the given master doc."""
    existing = frappe.db.exists("Contact", {"email_id": email})
    if existing:
        return existing
    try:
        doc = frappe.get_doc({
            "doctype": "Contact",
            "first_name": first_name,
            "last_name": last_name,
            "full_name": f"{first_name} {last_name}",
            "designation": designation or "",
            "is_primary_contact": 1,
            "email_ids": [{
                "email_id": email,
                "is_primary": 1,
            }],
            "phone_nos": [{
                "phone": mobile,
                "is_primary_mobile_no": 1,
                "is_primary_phone": 1,
            }],
            "links": [{
                "link_doctype": link_doctype,
                "link_name": link_name,
            }],
        })
        doc.flags.ignore_permissions = True
        doc.insert()
        return doc.name
    except Exception as exc:
        _log(f"  [contact for {link_name}] {exc}")
        return None


def _ensure_address_for(link_doctype, link_name, title, address_line1,
                        city, state, pincode, country="India",
                        address_type="Office"):
    """Create a primary Address linked to the given master doc."""
    existing = frappe.db.exists("Address", {
        "address_title": title, "address_line1": address_line1, "city": city,
    })
    if existing:
        return existing
    try:
        doc = frappe.get_doc({
            "doctype": "Address",
            "address_title": title[:140],
            "address_type": address_type,
            "address_line1": address_line1,
            "address_line2": f"Plot {sum(ord(c) for c in title) % 500 + 1}, Industrial Area",
            "city": city,
            "state": state,
            "country": country,
            "pincode": pincode,
            "is_primary_address": 1,
            "is_shipping_address": 1,
            "links": [{
                "link_doctype": link_doctype,
                "link_name": link_name,
            }],
        })
        doc.flags.ignore_permissions = True
        doc.insert()
        return doc.name
    except Exception as exc:
        _log(f"  [address for {link_name}] {exc}")
        return None


def _push_primary_to_master(doctype, name, email, mobile):
    """Supplier / Customer read mobile_no & email_id from primary Contact.
    Write them directly to the master as a fallback so auto-fetch works even
    if the Contact-sync hook didn't fire in a bulk run."""
    try:
        frappe.db.set_value(doctype, name, {
            "mobile_no": mobile,
            "email_id": email,
        }, update_modified=False)
    except Exception:
        pass


# ── Supplier (material vendors) ──────────────────────────────
def seed_suppliers():
    created = 0
    for idx, (org, sg, industry, fname, lname) in enumerate(_SUPPLIER_SEED):
        if frappe.db.exists("Supplier", {"supplier_name": org}):
            continue
        city, state, pincode = _CITIES[idx % len(_CITIES)]
        email = f"contact@{org.lower().replace(' ','').replace('&','and').replace('.','')}.demo"
        mobile = f"+91 981{idx:02d}0 {idx:05d}"
        try:
            doc = frappe.get_doc({
                "doctype": "Supplier",
                "supplier_name": org,
                "supplier_group": sg,
                "supplier_type": "Company",
                "country": "India",
                "default_currency": "INR",
                "language": "en",
                "disabled": 0,
                "is_frozen": 0,
                "is_transporter": 1 if "Logistics" in org else 0,
            })
            doc.flags.ignore_permissions = True
            doc.insert(ignore_mandatory=True)
            name = doc.name

            _ensure_contact_for("Supplier", name, fname, lname, email, mobile,
                                designation="Account Manager")
            _ensure_address_for("Supplier", name, org, f"{idx+1}, Corporate Park",
                                city, state, pincode)
            _push_primary_to_master("Supplier", name, email, mobile)
            created += 1
        except Exception as exc:
            _log(f"  [supplier {org}] {exc}")
    frappe.db.commit()
    return created


# ── Contractor (same Supplier doctype, Services group) ───────
def seed_contractors():
    created = 0
    for idx, (org, sg, industry, fname, lname) in enumerate(_CONTRACTOR_SEED):
        if frappe.db.exists("Supplier", {"supplier_name": org}):
            continue
        city, state, pincode = _CITIES[idx % len(_CITIES)]
        email = f"office@{org.lower().replace(' ','').replace('&','and').replace('.','').replace('-','')}.demo"
        mobile = f"+91 982{idx:02d}0 {idx:05d}"
        try:
            doc = frappe.get_doc({
                "doctype": "Supplier",
                "supplier_name": org,
                "supplier_group": sg,                    # "Services"
                "supplier_type": "Company",
                "country": "India",
                "default_currency": "INR",
                "language": "en",
                "disabled": 0,
                "is_frozen": 0,
                "is_transporter": 0,
            })
            doc.flags.ignore_permissions = True
            doc.insert(ignore_mandatory=True)
            name = doc.name

            _ensure_contact_for("Supplier", name, fname, lname, email, mobile,
                                designation="Operations Head")
            _ensure_address_for("Supplier", name, org, f"{idx+1}, Service Park",
                                city, state, pincode)
            _push_primary_to_master("Supplier", name, email, mobile)
            created += 1
        except Exception as exc:
            _log(f"  [contractor {org}] {exc}")
    frappe.db.commit()
    return created


# ── Customer ─────────────────────────────────────────────────
def seed_customers():
    created = 0
    for idx, (org, cg, industry, fname, lname) in enumerate(_CUSTOMER_SEED):
        if frappe.db.exists("Customer", {"customer_name": org}):
            continue
        city, state, pincode = _CITIES[idx % len(_CITIES)]
        email = f"buyer@{org.lower().replace(' ','').replace('&','and').replace('.','').replace('-','')}.demo"
        mobile = f"+91 983{idx:02d}0 {idx:05d}"
        try:
            doc = frappe.get_doc({
                "doctype": "Customer",
                "customer_name": org,
                "customer_group": cg,
                "customer_type": "Company",
                "territory": "India",
                "default_currency": "INR",
                "language": "en",
                "industry": industry if frappe.db.exists("Industry Type", industry) else None,
                "disabled": 0,
                "is_frozen": 0,
            })
            doc.flags.ignore_permissions = True
            doc.insert(ignore_mandatory=True)
            name = doc.name

            _ensure_contact_for("Customer", name, fname, lname, email, mobile,
                                designation="Procurement Lead")
            _ensure_address_for("Customer", name, org, f"{idx+1}, Business Centre",
                                city, state, pincode, address_type="Billing")
            _push_primary_to_master("Customer", name, email, mobile)
            created += 1
        except Exception as exc:
            _log(f"  [customer {org}] {exc}")
    frappe.db.commit()
    return created


# ── Lead ─────────────────────────────────────────────────────
def seed_leads():
    created = 0
    for idx, (full_name, company, source_group) in enumerate(_LEAD_SEED):
        email = f"{full_name.lower().replace(' ','.').replace(chr(39),'')}@{company.lower().replace(' ','').replace(chr(39),'')}.demo"
        if frappe.db.exists("Lead", {"email_id": email}):
            continue
        city, state, pincode = _CITIES[idx % len(_CITIES)]
        mobile = f"+91 984{idx:02d}0 {idx:05d}"
        try:
            first, *rest = full_name.split(" ", 1)
            last = rest[0] if rest else ""
            doc = frappe.get_doc({
                "doctype": "Lead",
                "lead_name": full_name,
                "first_name": first,
                "last_name": last,
                "company_name": company,
                "email_id": email,
                "mobile_no": mobile,
                "phone": mobile,
                "source": "Walk In",
                "status": "Lead",
                "territory": "India",
                "country": "India",
                "city": city,
                "state": state,
                "no_of_employees": "11-50",
                "language": "en",
            })
            doc.flags.ignore_permissions = True
            doc.insert(ignore_mandatory=True)
            created += 1
        except Exception as exc:
            _log(f"  [lead {full_name}] {exc}")
    frappe.db.commit()
    return created


def _ensure_designations(designations):
    for d in designations:
        if not frappe.db.exists("Designation", d):
            try:
                frappe.get_doc({"doctype": "Designation",
                                "designation_name": d}).insert(ignore_permissions=True)
            except Exception:
                pass


# ── Job Applicant (adds 20 new candidates) ───────────────────
def seed_job_applicants_bulk(hosts):
    _ensure_designations({d for _, _, _, d in _JOB_APPLICANT_SEED})
    created = 0
    for idx, (full_name, email, phone, designation) in enumerate(_JOB_APPLICANT_SEED):
        if frappe.db.exists("Job Applicant", {"email_id": email}):
            continue
        try:
            doc = frappe.get_doc({
                "doctype": "Job Applicant",
                "applicant_name": full_name,
                "email_id": email,
                "phone_number": phone,
                "status": "Open",
                "country": "India",
                "designation": designation,
                "cover_letter": f"Interested in the {designation} role. Available for on-site interview.",
                "interview_mode": "Offline",
                "interview_host": hosts["hr_mgr"],
                "interview_visit_date": add_days(today(), 2 + (idx % 14)),
                "interview_checkin_time": "10:00:00",
                "interview_checkout_time": "13:00:00",
            })
            doc.flags.ignore_permissions = True
            doc.insert(ignore_mandatory=True)
            created += 1
        except Exception as exc:
            _log(f"  [applicant {full_name}] {exc}")
    frappe.db.commit()
    return created


# ── Orchestrator ─────────────────────────────────────────────
def seed_business_masters():
    """Seed 20 records each of Supplier / Contractor / Customer / Lead / Job Applicant,
    with primary Contact + Address for Supplier & Customer. Idempotent."""
    _log("Starting business master seeding")
    frappe.flags.in_import = True
    hosts = create_employees()          # ensure HR manager exists

    n_sup = seed_suppliers()
    n_con = seed_contractors()
    n_cus = seed_customers()
    n_led = seed_leads()
    n_app = seed_job_applicants_bulk(hosts)

    _log(f"Suppliers  new: {n_sup}")
    _log(f"Contractors new: {n_con}")
    _log(f"Customers  new: {n_cus}")
    _log(f"Leads      new: {n_led}")
    _log(f"Applicants new: {n_app}")
    _log("Business master seeding complete.")

