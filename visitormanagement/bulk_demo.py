"""
VMS Bulk Demo Data Generator — tests EVERY flow in Visitor Management.

Run: bench --site visitor.local execute visitormanagement.bulk_demo.run

Creates:
- 10 Visitor Invitations (sent to external visitors)
- 10 Pre-Registration Requests (portal-submitted, various approval states)
- 50 Visitor Passes (10 per type: Contractor, Candidate, Customer, Supplier, VIP)
- 30 Check-Ins via Security Log
- 15 Check-Outs via Security Log
- Contact Traces auto-generated
- Hospitality Requests auto-linked
"""
import base64
import random
from frappe.utils import today, add_days, now_datetime, get_datetime
import frappe

BASE_DATE = today()
EMP = {
    "ceo": "HR-EMP-00001",
    "hr": "HR-EMP-00002",
    "ops": "HR-EMP-00003",
    "mkt": "HR-EMP-00004",
    "acc": "HR-EMP-00005",
}
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg==")


def attach_img(doc, fieldname):
    f = frappe.get_doc({
        "doctype": "File", "file_name": f"{fieldname}_{frappe.generate_hash(length=4)}.png",
        "content": PNG, "is_private": 1, "attached_to_doctype": doc.doctype,
        "attached_to_name": doc.name, "attached_to_field": fieldname,
    })
    f.save(ignore_permissions=True)
    return f.file_url


# ─── VISITOR DATASETS (10 per type) ───

CONTRACTORS = [
    ("Ramesh - Manoj Electricals", "Manoj Electricals", "ramesh@manoj.com", "501050205033", "Aadhaar"),
    ("Suresh - Kumar Plumbing", "Kumar Plumbing", "suresh@kp.in", "DL-TN-05210099", "Driving License"),
    ("Anil - SecureTech CCTV", "SecureTech CCTV", "anil@st.in", "600160026003", "Aadhaar"),
    ("Lakshmi - CleanPro", "CleanPro Services", "lakshmi@cp.com", "700170027003", "Aadhaar"),
    ("Dinesh - NetWorks IT", "NetWorks IT", "dinesh@nw.in", "AABPD5678Q", "PAN Card"),
    ("Venkat - AC Care", "AC Care", "venkat@ac.com", "800180028003", "Aadhaar"),
    ("Rajan - WoodWorks", "WoodWorks Interiors", "rajan@ww.in", "AABPR1111R", "PAN Card"),
    ("Kumar - Fire Safety", "Fire Safety Ltd", "kumar@fs.co", "P1234567", "Passport"),
    ("Gopi - Landscape", "Landscape Pro", "gopi@lp.in", "DL-KA-11210022", "Driving License"),
    ("Mohan - Elevator Co", "Elevator Solutions", "mohan@el.in", "111122223333", "Aadhaar"),
]

CANDIDATES = [
    ("Sneha Reddy", None, "sneha.r@gmail.com", "200120022003", "Aadhaar", "Senior Developer", "Technical Round"),
    ("Amit Jain", None, "amit.j@outlook.com", "AABPJ1111R", "PAN Card", "Marketing Manager", "Scheduled"),
    ("Kavitha Menon", None, "kavitha@yahoo.com", "T1234567", "Passport", "Accounts Executive", "Walk-In"),
    ("Rahul Sharma", None, "rahul.s@proton.me", "DL-MH-03210078", "Driving License", "Management Trainee", "Group Discussion"),
    ("Deepa Krishnan", None, "deepa.k@gmail.com", "300130023003", "Aadhaar", "HR Executive", "Scheduled"),
    ("Mohammed Farhan", None, "farhan@gmail.com", "310131023103", "Aadhaar", "DevOps Engineer", "Technical Round"),
    ("Priya Iyer", None, "priya.i@gmail.com", "320132023203", "Aadhaar", "Business Analyst", "Scheduled"),
    ("Arjun Nair", None, "arjun.n@gmail.com", "330133023303", "Aadhaar", "Data Scientist", "Technical Round"),
    ("Nisha Patel", None, "nisha.p@gmail.com", "340134023403", "Aadhaar", "QA Engineer", "Walk-In"),
    ("Karthik Raj", None, "karthik.r@gmail.com", "350135023503", "Aadhaar", "Sales Executive", "Scheduled"),
]

CUSTOMERS = [
    ("Arvind Rao", "TechSpark Industries", "arvind@ts.io", "AABPR2345T", "PAN Card", "Product Demo"),
    ("Neha Kapoor", "Global Retail Ltd", "neha@gr.com", "400140024003", "Aadhaar", "Negotiation"),
    ("Vijay Sundaram", "ManuFlow Systems", "vijay@mf.in", "DL-AP-01210033", "Driving License", "Site Visit"),
    ("Lakshmi Iyer", "BrightPath Edu", "lakshmi@bp.edu", "410141024103", "Aadhaar", "Complaint"),
    ("Prakash Hegde", "GreenField Agro", "prakash@gf.co", "AABPH4567U", "PAN Card", "Audit"),
    ("Roshni Mehta", "CloudVault Tech", "roshni@cv.in", "M9876543", "Passport", "Product Demo"),
    ("Sanjay Gupta", "Alpha Corp", "sanjay@ac.com", "420142024203", "Aadhaar", "Negotiation"),
    ("Meera Shah", "Beta Industries", "meera@bi.in", "AABPS9999M", "PAN Card", "Site Visit"),
    ("Rakesh Bhat", "Gamma Retail", "rakesh@gr.com", "430143024303", "Aadhaar", "Product Demo"),
    ("Kavya Menon", "Delta Tech", "kavya@dt.io", "440144024403", "Aadhaar", "Negotiation"),
]

SUPPLIERS = [
    ("Ravi - OfficeMart", "OfficeMart Supplies", "ravi@om.in", "510151025103", "Aadhaar", "Delivery"),
    ("Sheela - FreshBites", "FreshBites Catering", "sheela@fb.in", "520152025203", "Aadhaar", "Meeting"),
    ("Murugan - TechParts", "TechParts India", "murugan@tp.in", "530153025303", "Aadhaar", "Delivery"),
    ("Padma - QualityFirst", "QualityFirst Auditors", "padma@qf.com", "AABPP6789V", "PAN Card", "Audit"),
    ("Bala - FurniCraft", "FurniCraft Interiors", "bala@fc.in", "540154025403", "Aadhaar", "Delivery"),
    ("Devi - SafeGuard", "SafeGuard Fire", "devi@sg.in", "550155025503", "Aadhaar", "Service"),
    ("Rohit - PaperPlus", "PaperPlus Stationery", "rohit@pp.in", "560156025603", "Aadhaar", "Delivery"),
    ("Kamala - CleanAll", "CleanAll Services", "kamala@ca.com", "570157025703", "Aadhaar", "Service"),
    ("Senthil - TransportCo", "TransportCo Ltd", "senthil@tc.in", "580158025803", "Aadhaar", "Delivery"),
    ("Jayanti - Stationers", "Jayanti Stationers", "jayanti@js.in", "590159025903", "Aadhaar", "Delivery"),
]

VIPS = [
    ("Justice K. Raghavan", "High Court of Madras", "raghavan@gov.in", "P1234567", "Passport", "Government Official"),
    ("Dr. Sunita Patel", "VentureCap Partners", "sunita@vc.com", "R8765432", "Passport", "Investor"),
    ("Mr. Hiroshi Tanaka", "Nippon Technologies", "tanaka@nt.jp", "JP12345678", "Passport", "Partner"),
    ("Ms. Priya Ramaswamy", "Economic Times", "priya.r@et.com", "AABPR0001W", "PAN Card", "Media"),
    ("Prof. Arun Shankar", "IIT Madras", "shankar@iitm.ac.in", "123456789012", "Aadhaar", "Guest Speaker"),
    ("Mr. Rajendra Shah", "Shah Group Holdings", "shah@sg.com", "S5432167", "Passport", "Board Member"),
    ("Dr. Meenakshi Iyer", "Apollo Hospitals", "meenakshi@ah.com", "900190029003", "Aadhaar", "Partner"),
    ("Mr. Vikram Singh", "Indian Armed Forces", "vikram@gov.in", "A1234567", "Passport", "Government Official"),
    ("Ms. Deepika Rao", "Forbes India", "deepika@fi.com", "AABPD0002X", "PAN Card", "Media"),
    ("Sir James Wilson", "UK Trade Office", "james@uk.gov", "GB1234567", "Passport", "Government Official"),
]


def make_vp_data(vtype, data, idx):
    """Build a Visitor Pass dict from tuple data."""
    base = {
        "visitor_type": vtype,
        "entry_type": "New",
        "request_channel": "Desk",
        "visit_date": add_days(BASE_DATE, (idx % 60) + 1),  # spread 1-60 days
        "expected_checkin": f"{9 + (idx % 6):02d}:00:00",
        "expected_checkout": f"{11 + (idx % 6):02d}:00:00",
        "purpose_of_visit": f"{vtype} visit #{idx + 1}",
        "person_to_visit": random.choice(list(EMP.values())),
    }
    if vtype == "Contractor":
        name, company, email, id_num, id_type = data
        base.update({
            "visitor_full_name": name, "company__organisation": company, "email_id": email,
            "mobile_number": f"+91 9876{idx:06d}", "id_proof_type": id_type, "id_proof_number": id_num,
            "tools_list": "Standard toolkit", "person_to_visit": EMP["ops"],
        })
    elif vtype == "Candidate":
        name, _, email, id_num, id_type, position, interview = data
        base.update({
            "visitor_full_name": name, "email_id": email,
            "mobile_number": f"+91 9876{10000 + idx:06d}", "id_proof_type": id_type, "id_proof_number": id_num,
            "position_applied": position, "candidate_interview_type": interview,
            "interview_panel": "HR Panel", "person_to_visit": EMP["hr"],
        })
    elif vtype == "Customer":
        name, company, email, id_num, id_type, cat = data
        base.update({
            "visitor_full_name": name, "company__organisation": company, "email_id": email,
            "mobile_number": f"+91 9876{20000 + idx:06d}", "id_proof_type": id_type, "id_proof_number": id_num,
            "visit_category": cat, "sales_executive": EMP["mkt"], "person_to_visit": EMP["mkt"],
        })
    elif vtype == "Supplier":
        name, company, email, id_num, id_type, mode = data
        base.update({
            "visitor_full_name": name, "company__organisation": company, "email_id": email,
            "mobile_number": f"+91 9876{30000 + idx:06d}", "id_proof_type": id_type, "id_proof_number": id_num,
            "supplier_visit_mode": mode, "person_to_visit": EMP["ops"],
        })
    elif vtype == "VIP":
        name, company, email, id_num, id_type, vip_cat = data
        base.update({
            "visitor_full_name": name, "company__organisation": company, "email_id": email,
            "mobile_number": f"+91 9876{40000 + idx:06d}", "id_proof_type": id_type, "id_proof_number": id_num,
            "vip_category": vip_cat, "mdceo_notified": 1,
            "protocol_notes": "VIP visit protocol applies.", "person_to_visit": EMP["ceo"],
        })
    return base


def insert_vp(data):
    """Insert a VP with required images attached."""
    items = data.pop("items", [])
    vp = frappe.new_doc("Visitor Pass")
    vp.update(data)
    for i in items:
        vp.append("visitor_items", i)
    vp.insert(ignore_permissions=True, ignore_mandatory=True)
    vp.db_set("id_proof_scan", attach_img(vp, "id_proof_scan"), update_modified=False)
    vp.db_set("visitor_photo", attach_img(vp, "visitor_photo"), update_modified=False)
    vp.reload()
    return vp


def approve_vp(vp):
    """Walk VP through the approval workflow."""
    from frappe.model.workflow import apply_workflow
    if vp.visitor_type == "VIP":
        apply_workflow(vp, "Submit"); vp.reload()
        apply_workflow(vp, "Approve"); vp.reload()  # Pending HOD -> Pending CEO
        apply_workflow(vp, "Approve"); vp.reload()  # Pending CEO -> Approved
    else:
        apply_workflow(vp, "Submit"); vp.reload()
        apply_workflow(vp, "Approve"); vp.reload()
    return vp


def run():
    print("=" * 70)
    print("VMS BULK DEMO DATA GENERATOR")
    print("=" * 70)

    stats = {"invitations": 0, "vps": 0, "checkins": 0, "checkouts": 0, "emergency": 0}

    # ═══════════════════════════════════════════════════════
    # STEP 1: Visitor Invitations (10)
    # ═══════════════════════════════════════════════════════
    print("\n--- STEP 1: Visitor Invitations ---")
    for i in range(10):
        inv = frappe.new_doc("Visitor Invitation")
        inv.visitor_type = random.choice(["Customer", "Supplier", "Candidate", "VIP"])
        inv.visitor_email = f"invitee{i}@example.com"
        inv.host_employee = random.choice(list(EMP.values()))
        inv.visit_date = add_days(BASE_DATE, i + 5)
        inv.expected_checkin = "10:00:00"
        inv.expected_checkout = "13:00:00"
        inv.purpose_of_visit = f"Invited visit #{i+1}"
        inv.insert(ignore_permissions=True)
        stats["invitations"] += 1
    print(f"  Created: {stats['invitations']}")

    # ═══════════════════════════════════════════════════════
    # STEP 2: Visitor Passes (50 — 10 per type)
    # ═══════════════════════════════════════════════════════
    print("\n--- STEP 3: Visitor Passes ---")
    all_vps = []
    for vtype, dataset in [
        ("Contractor", CONTRACTORS), ("Candidate", CANDIDATES),
        ("Customer", CUSTOMERS), ("Supplier", SUPPLIERS), ("VIP", VIPS),
    ]:
        for idx, data in enumerate(dataset):
            try:
                vp_data = make_vp_data(vtype, data, idx)
                vp = insert_vp(vp_data)
                all_vps.append(vp.name)
                stats["vps"] += 1
                print(f"  {vp.name}: {vtype} — {vp.visitor_full_name}")
            except Exception as e:
                print(f"  FAILED {vtype} #{idx}: {str(e)[:100]}")
    frappe.db.commit()

    # ═══════════════════════════════════════════════════════
    # STEP 4: Approve all VPs
    # ═══════════════════════════════════════════════════════
    print("\n--- STEP 4: Approving all Visitor Passes ---")
    approved = []
    for vp_name in all_vps:
        try:
            vp = frappe.get_doc("Visitor Pass", vp_name)
            approve_vp(vp)
            approved.append(vp.name)
        except Exception as e:
            print(f"  FAILED approve {vp_name}: {str(e)[:100]}")
    frappe.db.commit()
    print(f"  Approved: {len(approved)}/{len(all_vps)}")

    # ═══════════════════════════════════════════════════════
    # STEP 5: Check-In 30 visitors (for today's passes)
    # ═══════════════════════════════════════════════════════
    print("\n--- STEP 5: Check-In Visitors ---")
    # Use today's date visits for check-in
    today_vps = frappe.db.sql_list("""
        SELECT name FROM `tabVisitor Pass`
        WHERE status='Approved' AND visit_date=%s LIMIT 30
    """, (BASE_DATE,))

    # If no today visits, update some VPs' visit_date to today
    if len(today_vps) < 15:
        needed = 30 - len(today_vps)
        more = frappe.db.sql_list("""
            SELECT name FROM `tabVisitor Pass`
            WHERE status='Approved' AND visit_date > %s LIMIT %s
        """, (BASE_DATE, needed))
        for vp_name in more:
            frappe.db.set_value("Visitor Pass", vp_name, "visit_date", BASE_DATE, update_modified=False)
            today_vps.append(vp_name)
        frappe.db.commit()

    for vp_name in today_vps[:30]:
        try:
            vp = frappe.get_doc("Visitor Pass", vp_name)
            sl = frappe.new_doc("Security Log")
            sl.visitor_pass = vp_name
            sl.event_type = "Check-In"
            sl.gate_name = "Main Gate"
            sl.photo_at_gate = vp.visitor_photo
            sl.id_proof_match = 1
            sl.pass_photo_match = 1
            sl.temperature = round(random.uniform(36.0, 37.2), 1)
            sl.symptoms_flag = 0
            sl.visited_area = random.choice(["Reception", "Conference Room A", "Floor 2", "Lab"])
            sl.insert(ignore_permissions=True)
            stats["checkins"] += 1
        except Exception as e:
            print(f"  FAILED check-in {vp_name}: {str(e)[:80]}")
    frappe.db.commit()
    print(f"  Checked in: {stats['checkins']}")

    # ═══════════════════════════════════════════════════════
    # STEP 6: Check-Out 15 visitors
    # ═══════════════════════════════════════════════════════
    print("\n--- STEP 6: Check-Out Visitors ---")
    checked_in_vps = frappe.db.sql_list("""
        SELECT name FROM `tabVisitor Pass`
        WHERE status='Checked-In' LIMIT 15
    """)
    for vp_name in checked_in_vps:
        try:
            sl = frappe.new_doc("Security Log")
            sl.visitor_pass = vp_name
            sl.event_type = "Check-Out"
            sl.gate_name = "Main Gate"
            sl.insert(ignore_permissions=True)
            stats["checkouts"] += 1
        except Exception as e:
            print(f"  FAILED check-out {vp_name}: {str(e)[:80]}")
    frappe.db.commit()
    print(f"  Checked out: {stats['checkouts']}")

    # ═══════════════════════════════════════════════════════
    # FINAL REPORT
    # ═══════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print("FINAL DATABASE STATE")
    print("=" * 70)
    for dt in ["Visitor Invitation", "Visitor Pass",
               "Security Log", "Hospitality Request",
               "Contact Trace Record", "Visitor Event Log"]:
        count = frappe.db.count(dt)
        print(f"  {dt:28s}: {count}")

    print("\n  Visitor Pass by Status:")
    for r in frappe.db.sql("SELECT status, COUNT(*) c FROM `tabVisitor Pass` GROUP BY status", as_dict=1):
        print(f"    {r.status:15s}: {r.c}")

    print("\n  Visitor Pass by Type:")
    for r in frappe.db.sql("SELECT visitor_type, COUNT(*) c FROM `tabVisitor Pass` GROUP BY visitor_type", as_dict=1):
        print(f"    {r.visitor_type:15s}: {r.c}")

    print("\n  Badge Coverage (non-VIP):")
    r = frappe.db.sql("""
        SELECT COUNT(*) total,
               SUM(CASE WHEN badge_number IS NOT NULL AND badge_number != '' THEN 1 ELSE 0 END) with_badge
        FROM `tabVisitor Pass` WHERE visitor_type != 'VIP' AND docstatus = 1
    """, as_dict=1)[0]
    print(f"    {r.with_badge}/{r.total} have badge numbers")

    print("\n" + "=" * 70)
    print(f"SUMMARY: {stats}")
    print("=" * 70)
