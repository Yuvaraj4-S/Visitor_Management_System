"""
VMS Full Demo Data — run via: bench --site visitor.local execute visitormanagement.create_demo_data.run
"""
import base64
import frappe
from frappe.utils import today, add_days, now_datetime


BASE_DATE = today()
EMP = {"ceo": "HR-EMP-00001", "hr": "HR-EMP-00002", "ops": "HR-EMP-00003", "mkt": "HR-EMP-00004", "acc": "HR-EMP-00005"}

# Minimal 1x1 PNG
PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg=="

def attach_image(doc, fieldname, label):
    png_bytes = base64.b64decode(PNG_B64)
    fname = f"{label}_{frappe.generate_hash(length=4)}.png"
    f = frappe.get_doc({"doctype": "File", "file_name": fname, "content": png_bytes,
                        "is_private": 1, "attached_to_doctype": doc.doctype,
                        "attached_to_name": doc.name, "attached_to_field": fieldname})
    f.save(ignore_permissions=True)
    return f.file_url

def get_pending_state(vtype):
    return {"Contractor": "Pending System Manager", "Supplier": "Pending System Manager",
            "Candidate": "Pending HR Manager", "Customer": "Pending Sales Manager",
            "VIP": "Pending HOD"}.get(vtype, "Pending System Manager")

VISITORS = [
    # CONTRACTORS (6)
    {"visitor_type": "Contractor", "visitor_full_name": "Ramesh - Manoj Electricals", "mobile_number": "+91 9876000001", "email_id": "ramesh@manojelectricals.com", "company__organisation": "Manoj Electricals", "id_proof_type": "Aadhaar", "id_proof_number": "5001-5002-5003", "purpose_of_visit": "Annual electrical maintenance and safety audit", "person_to_visit": EMP["ops"], "visit_date": add_days(BASE_DATE, 1), "expected_checkin": "08:00:00", "expected_checkout": "17:00:00", "vehicle_number": "TN-01-AB-1234", "tools_list": "Multimeter, Wire stripper, Safety gloves", "multi_day_pass": 1, "pass_valid_until": add_days(BASE_DATE, 5), "meal_required": 1, "items": [{"item_name": "Electrical Tool Kit", "item_category": "Tool", "quantity": 1}, {"item_name": "Multimeter", "item_category": "Electronics", "quantity": 1, "serial_number": "MM-2024-001"}]},
    {"visitor_type": "Contractor", "visitor_full_name": "Suresh - Kumar Plumbing", "mobile_number": "+91 9876000002", "email_id": "suresh@plumbing.in", "company__organisation": "Kumar Plumbing", "id_proof_type": "Driving License", "id_proof_number": "DL-TN-05210099", "purpose_of_visit": "Emergency plumbing repair 2nd floor", "person_to_visit": EMP["ops"], "visit_date": BASE_DATE, "expected_checkin": "09:00:00", "expected_checkout": "13:00:00", "tools_list": "Pipe wrench, Plunger, PVC cement", "items": [{"item_name": "Plumbing Tool Box", "item_category": "Tool", "quantity": 1}]},
    {"visitor_type": "Contractor", "visitor_full_name": "Anil - SecureTech CCTV", "mobile_number": "+91 9876000003", "email_id": "anil@securetech.in", "company__organisation": "SecureTech CCTV", "id_proof_type": "Aadhaar", "id_proof_number": "6001-6002-6003", "purpose_of_visit": "CCTV installation in conference rooms", "person_to_visit": EMP["ops"], "visit_date": add_days(BASE_DATE, 2), "expected_checkin": "10:00:00", "expected_checkout": "18:00:00", "tools_list": "Drill, Cable crimper, DVR", "multi_day_pass": 1, "pass_valid_until": add_days(BASE_DATE, 4), "meal_required": 1, "items": [{"item_name": "CCTV Camera", "item_category": "Electronics", "quantity": 4, "serial_number": "CAM-2026-A1"}]},
    {"visitor_type": "Contractor", "visitor_full_name": "Lakshmi - CleanPro", "mobile_number": "+91 9876000004", "email_id": "lakshmi@cleanpro.com", "company__organisation": "CleanPro Services", "id_proof_type": "Aadhaar", "id_proof_number": "7001-7002-7003", "purpose_of_visit": "Deep cleaning — quarterly", "person_to_visit": EMP["ops"], "visit_date": add_days(BASE_DATE, 3), "expected_checkin": "06:00:00", "expected_checkout": "14:00:00", "tools_list": "Vacuum, Scrubber, Chemicals", "meal_required": 1, "items": [{"item_name": "Cleaning Equipment", "item_category": "Tool", "quantity": 1}]},
    {"visitor_type": "Contractor", "visitor_full_name": "Dinesh - NetWorks IT", "mobile_number": "+91 9876000005", "email_id": "dinesh@networks.in", "company__organisation": "NetWorks IT", "id_proof_type": "PAN Card", "id_proof_number": "AABPD5678Q", "purpose_of_visit": "Server room network cabling", "person_to_visit": EMP["ops"], "visit_date": add_days(BASE_DATE, 1), "expected_checkin": "09:00:00", "expected_checkout": "18:00:00", "tools_list": "Network cables, Crimping tool, Tester", "meal_required": 1, "items": [{"item_name": "Network Kit", "item_category": "Electronics", "quantity": 1, "serial_number": "NTK-2026"}]},
    {"visitor_type": "Contractor", "visitor_full_name": "Venkat - AC Care", "mobile_number": "+91 9876000006", "email_id": "venkat@accare.com", "company__organisation": "AC Care Solutions", "id_proof_type": "Aadhaar", "id_proof_number": "8001-8002-8003", "purpose_of_visit": "AC servicing and gas refill", "person_to_visit": EMP["ops"], "visit_date": add_days(BASE_DATE, 2), "expected_checkin": "08:30:00", "expected_checkout": "16:00:00", "tools_list": "Refrigerant, Pressure gauge", "items": [{"item_name": "Refrigerant Cylinder", "item_category": "Tool", "quantity": 2}]},

    # CANDIDATES (6)
    {"visitor_type": "Candidate", "visitor_full_name": "Sneha Reddy", "mobile_number": "+91 9876100001", "email_id": "sneha.r@gmail.com", "id_proof_type": "Aadhaar", "id_proof_number": "2001-2002-2003", "purpose_of_visit": "Interview — Senior Software Developer", "person_to_visit": EMP["hr"], "visit_date": add_days(BASE_DATE, 1), "expected_checkin": "10:00:00", "expected_checkout": "12:00:00", "position_applied": "Senior Software Developer", "candidate_interview_type": "Technical Round", "interview_panel": "Rajesh Kumar, Arun Patel", "items": [{"item_name": "Laptop", "item_category": "Electronics", "quantity": 1, "serial_number": "SNH-001"}]},
    {"visitor_type": "Candidate", "visitor_full_name": "Amit Jain", "mobile_number": "+91 9876100002", "email_id": "amit.j@outlook.com", "id_proof_type": "PAN Card", "id_proof_number": "AABPJ1111R", "purpose_of_visit": "Interview — Marketing Manager", "person_to_visit": EMP["hr"], "visit_date": add_days(BASE_DATE, 1), "expected_checkin": "14:00:00", "expected_checkout": "16:00:00", "position_applied": "Marketing Manager", "candidate_interview_type": "Scheduled", "interview_panel": "Divya Nair, Priya Sharma"},
    {"visitor_type": "Candidate", "visitor_full_name": "Kavitha Menon", "mobile_number": "+91 9876100003", "email_id": "kavitha@yahoo.com", "id_proof_type": "Passport", "id_proof_number": "T1234567", "purpose_of_visit": "Walk-in for Accounts Executive", "person_to_visit": EMP["hr"], "visit_date": BASE_DATE, "expected_checkin": "09:30:00", "expected_checkout": "11:30:00", "position_applied": "Accounts Executive", "candidate_interview_type": "Walk-In", "interview_panel": "Vikram Singh"},
    {"visitor_type": "Candidate", "visitor_full_name": "Rahul Sharma", "mobile_number": "+91 9876100004", "email_id": "rahul.s@proton.me", "id_proof_type": "Driving License", "id_proof_number": "DL-MH-03210078", "purpose_of_visit": "GD round — Management Trainee", "person_to_visit": EMP["hr"], "visit_date": add_days(BASE_DATE, 2), "expected_checkin": "09:00:00", "expected_checkout": "13:00:00", "position_applied": "Management Trainee", "candidate_interview_type": "Group Discussion", "interview_panel": "Rajesh, Priya, Divya", "meal_required": 1},
    {"visitor_type": "Candidate", "visitor_full_name": "Deepa Krishnan", "mobile_number": "+91 9876100005", "email_id": "deepa.k@gmail.com", "id_proof_type": "Aadhaar", "id_proof_number": "3001-3002-3003", "purpose_of_visit": "Final round — HR Executive", "person_to_visit": EMP["hr"], "visit_date": add_days(BASE_DATE, 2), "expected_checkin": "11:00:00", "expected_checkout": "12:30:00", "position_applied": "HR Executive", "candidate_interview_type": "Scheduled", "interview_panel": "Priya Sharma"},
    {"visitor_type": "Candidate", "visitor_full_name": "Mohammed Farhan", "mobile_number": "+91 9876100006", "email_id": "farhan@gmail.com", "id_proof_type": "Aadhaar", "id_proof_number": "3101-3102-3103", "purpose_of_visit": "Technical interview — DevOps", "person_to_visit": EMP["hr"], "visit_date": add_days(BASE_DATE, 3), "expected_checkin": "10:00:00", "expected_checkout": "12:00:00", "position_applied": "DevOps Engineer", "candidate_interview_type": "Technical Round", "interview_panel": "Arun Patel"},

    # CUSTOMERS (6)
    {"visitor_type": "Customer", "visitor_full_name": "Arvind Rao", "mobile_number": "+91 9876200001", "email_id": "arvind@techspark.io", "company__organisation": "TechSpark Industries", "id_proof_type": "PAN Card", "id_proof_number": "AABPR2345T", "purpose_of_visit": "Product demo — Enterprise ERP", "person_to_visit": EMP["mkt"], "visit_date": add_days(BASE_DATE, 1), "expected_checkin": "10:00:00", "expected_checkout": "12:00:00", "visit_category": "Product Demo", "sales_executive": EMP["mkt"], "products_discussed": "ERP Suite, Cloud Hosting", "meeting_outcome": "Follow-Up Needed", "followup_date": add_days(BASE_DATE, 8), "meal_required": 1, "special_diet": "Vegetarian", "conference_room": "Boardroom A", "items": [{"item_name": "Laptop", "item_category": "Electronics", "quantity": 1, "serial_number": "AR-DEMO-01"}]},
    {"visitor_type": "Customer", "visitor_full_name": "Neha Kapoor", "mobile_number": "+91 9876200002", "email_id": "neha@globalretail.com", "company__organisation": "Global Retail Pvt Ltd", "id_proof_type": "Aadhaar", "id_proof_number": "4001-4002-4003", "purpose_of_visit": "Contract negotiation — annual service", "person_to_visit": EMP["ceo"], "visit_date": add_days(BASE_DATE, 2), "expected_checkin": "11:00:00", "expected_checkout": "13:00:00", "visit_category": "Negotiation", "sales_executive": EMP["mkt"], "products_discussed": "AMC, Priority Support", "meeting_outcome": "Pending", "meal_required": 1, "special_diet": "Non-vegetarian", "conference_room": "Boardroom A"},
    {"visitor_type": "Customer", "visitor_full_name": "Vijay Sundaram", "mobile_number": "+91 9876200003", "email_id": "vijay@manuflow.co.in", "company__organisation": "ManuFlow Systems", "id_proof_type": "Driving License", "id_proof_number": "DL-AP-01210033", "purpose_of_visit": "Site visit — manufacturing integration", "person_to_visit": EMP["ops"], "visit_date": add_days(BASE_DATE, 3), "expected_checkin": "09:00:00", "expected_checkout": "16:00:00", "vehicle_number": "AP-09-CD-5678", "visit_category": "Site Visit", "sales_executive": EMP["mkt"], "products_discussed": "Manufacturing ERP, IoT", "meal_required": 1, "special_diet": "Vegetarian"},
    {"visitor_type": "Customer", "visitor_full_name": "Lakshmi Iyer", "mobile_number": "+91 9876200004", "email_id": "lakshmi@brightpath.edu", "company__organisation": "BrightPath Education", "id_proof_type": "Aadhaar", "id_proof_number": "4101-4102-4103", "purpose_of_visit": "Complaint — delayed implementation", "person_to_visit": EMP["ceo"], "visit_date": BASE_DATE, "expected_checkin": "14:00:00", "expected_checkout": "16:00:00", "visit_category": "Complaint", "sales_executive": EMP["mkt"], "meeting_outcome": "Follow-Up Needed", "followup_date": add_days(BASE_DATE, 3)},
    {"visitor_type": "Customer", "visitor_full_name": "Prakash Hegde", "mobile_number": "+91 9876200005", "email_id": "prakash@greenfield.co", "company__organisation": "GreenField Agro", "id_proof_type": "PAN Card", "id_proof_number": "AABPH4567U", "purpose_of_visit": "Annual software audit", "person_to_visit": EMP["ops"], "visit_date": add_days(BASE_DATE, 4), "expected_checkin": "10:00:00", "expected_checkout": "17:00:00", "visit_category": "Audit", "sales_executive": EMP["mkt"], "meal_required": 1, "special_diet": "Jain", "items": [{"item_name": "Audit Folder", "item_category": "Document / Sample / Gift / Perishable / Weapon / Other", "quantity": 1}, {"item_name": "Laptop", "item_category": "Electronics", "quantity": 1, "serial_number": "PH-AUD-01"}]},
    {"visitor_type": "Customer", "visitor_full_name": "Roshni Mehta", "mobile_number": "+91 9876200006", "email_id": "roshni@cloudvault.in", "company__organisation": "CloudVault Tech", "id_proof_type": "Passport", "id_proof_number": "M9876543", "purpose_of_visit": "Cloud backup product demo", "person_to_visit": EMP["mkt"], "visit_date": add_days(BASE_DATE, 1), "expected_checkin": "14:00:00", "expected_checkout": "16:00:00", "visit_category": "Product Demo", "sales_executive": EMP["mkt"], "products_discussed": "Cloud Backup, DR-as-a-Service", "conference_room": "Video Conference Room"},

    # SUPPLIERS (6)
    {"visitor_type": "Supplier", "visitor_full_name": "Ravi - OfficeMart", "mobile_number": "+91 9876300001", "email_id": "ravi@officemart.in", "company__organisation": "OfficeMart Supplies", "id_proof_type": "Aadhaar", "id_proof_number": "5101-5102-5103", "purpose_of_visit": "Monthly stationery delivery", "person_to_visit": EMP["ops"], "visit_date": BASE_DATE, "expected_checkin": "10:00:00", "expected_checkout": "11:00:00", "supplier_visit_mode": "Delivery", "goods_description": "A4 paper 50 reams, Cartridges 10", "vehicle_number": "TN-02-EF-9012", "items": [{"item_name": "Stationery Package", "item_category": "Document / Sample / Gift / Perishable / Weapon / Other", "quantity": 1}]},
    {"visitor_type": "Supplier", "visitor_full_name": "Sheela - FreshBites", "mobile_number": "+91 9876300002", "email_id": "sheela@freshbites.in", "company__organisation": "FreshBites Catering", "id_proof_type": "Aadhaar", "id_proof_number": "5201-5202-5203", "purpose_of_visit": "Catering contract renewal", "person_to_visit": EMP["ops"], "visit_date": add_days(BASE_DATE, 1), "expected_checkin": "11:00:00", "expected_checkout": "12:30:00", "supplier_visit_mode": "Meeting", "nda_required": 1, "documents_shared": "Rate card, Menu, Hygiene cert"},
    {"visitor_type": "Supplier", "visitor_full_name": "Murugan - TechParts", "mobile_number": "+91 9876300004", "email_id": "murugan@techparts.in", "company__organisation": "TechParts India", "id_proof_type": "Aadhaar", "id_proof_number": "5301-5302-5303", "purpose_of_visit": "IT equipment delivery — laptops", "person_to_visit": EMP["ops"], "visit_date": add_days(BASE_DATE, 2), "expected_checkin": "10:00:00", "expected_checkout": "11:00:00", "supplier_visit_mode": "Delivery", "goods_description": "Dell laptops x10, Monitors x10", "vehicle_number": "KA-01-IJ-7890", "items": [{"item_name": "Laptop Box", "item_category": "Electronics", "quantity": 10}]},
    {"visitor_type": "Supplier", "visitor_full_name": "Padma - QualityFirst", "mobile_number": "+91 9876300005", "email_id": "padma@qualityfirst.com", "company__organisation": "QualityFirst Auditors", "id_proof_type": "PAN Card", "id_proof_number": "AABPP6789V", "purpose_of_visit": "ISO 9001 surveillance audit", "person_to_visit": EMP["ops"], "visit_date": add_days(BASE_DATE, 3), "expected_checkin": "09:00:00", "expected_checkout": "17:00:00", "supplier_visit_mode": "Audit", "nda_required": 1, "documents_shared": "Audit report, Corrective actions", "meal_required": 1, "special_diet": "Vegetarian", "items": [{"item_name": "Audit Folder", "item_category": "Document / Sample / Gift / Perishable / Weapon / Other", "quantity": 1}]},
    {"visitor_type": "Supplier", "visitor_full_name": "Bala - FurniCraft", "mobile_number": "+91 9876300006", "email_id": "bala@furnicraft.in", "company__organisation": "FurniCraft Interiors", "id_proof_type": "Aadhaar", "id_proof_number": "5401-5402-5403", "purpose_of_visit": "Office furniture delivery", "person_to_visit": EMP["ops"], "visit_date": add_days(BASE_DATE, 4), "expected_checkin": "08:00:00", "expected_checkout": "16:00:00", "supplier_visit_mode": "Delivery", "goods_description": "Desks x20, Chairs x20", "vehicle_number": "TN-07-KL-2345"},
    {"visitor_type": "Supplier", "visitor_full_name": "Devi - SafeGuard", "mobile_number": "+91 9876300007", "email_id": "devi@safeguard.in", "company__organisation": "SafeGuard Fire", "id_proof_type": "Aadhaar", "id_proof_number": "5501-5502-5503", "purpose_of_visit": "Fire safety annual service", "person_to_visit": EMP["ops"], "visit_date": add_days(BASE_DATE, 5), "expected_checkin": "09:00:00", "expected_checkout": "14:00:00", "supplier_visit_mode": "Service"},

    # VIPs (6)
    {"visitor_type": "VIP", "visitor_full_name": "Justice K. Raghavan", "mobile_number": "+91 9876400001", "email_id": "raghavan@gov.in", "company__organisation": "High Court of Madras", "id_proof_type": "Passport", "id_proof_number": "P1234567", "purpose_of_visit": "Board meeting — legal compliance", "person_to_visit": EMP["ceo"], "visit_date": add_days(BASE_DATE, 3), "expected_checkin": "10:00:00", "expected_checkout": "13:00:00", "vip_category": "Government Official", "mdceo_notified": 1, "protocol_notes": "Vegetarian lunch. Reserved VIP parking.", "meal_required": 1, "special_diet": "Vegetarian", "conference_room": "Boardroom A", "items": [{"item_name": "Briefcase", "item_category": "Document / Sample / Gift / Perishable / Weapon / Other", "quantity": 1}]},
    {"visitor_type": "VIP", "visitor_full_name": "Dr. Sunita Patel", "mobile_number": "+91 9876400002", "email_id": "sunita@venturecap.com", "company__organisation": "VentureCap Partners", "id_proof_type": "Passport", "id_proof_number": "R8765432", "purpose_of_visit": "Series B funding due diligence", "person_to_visit": EMP["ceo"], "visit_date": add_days(BASE_DATE, 2), "expected_checkin": "10:00:00", "expected_checkout": "14:00:00", "vip_category": "Investor", "mdceo_notified": 1, "protocol_notes": "NDA before presentation. Full financials.", "meal_required": 1, "special_diet": "Vegan", "conference_room": "Boardroom A", "items": [{"item_name": "Laptop", "item_category": "Electronics", "quantity": 1}, {"item_name": "Presentation Folder", "item_category": "Document / Sample / Gift / Perishable / Weapon / Other", "quantity": 1}]},
    {"visitor_type": "VIP", "visitor_full_name": "Mr. Hiroshi Tanaka", "mobile_number": "+91 9876400003", "email_id": "tanaka@nippontech.jp", "company__organisation": "Nippon Technologies", "id_proof_type": "Passport", "id_proof_number": "JP12345678", "purpose_of_visit": "Partnership agreement signing", "person_to_visit": EMP["ceo"], "visit_date": add_days(BASE_DATE, 5), "expected_checkin": "09:00:00", "expected_checkout": "15:00:00", "vip_category": "Partner", "mdceo_notified": 1, "interpreter_required": 1, "interpreter_language": "English", "protocol_notes": "Business card ceremony. Interpreter arranged.", "meal_required": 1, "conference_room": "Boardroom A"},
    {"visitor_type": "VIP", "visitor_full_name": "Ms. Priya Ramaswamy", "mobile_number": "+91 9876400004", "email_id": "priya.r@et.com", "company__organisation": "Economic Times", "id_proof_type": "PAN Card", "id_proof_number": "AABPR0001W", "purpose_of_visit": "Exclusive CEO interview — expansion plans", "person_to_visit": EMP["ceo"], "visit_date": add_days(BASE_DATE, 4), "expected_checkin": "11:00:00", "expected_checkout": "13:00:00", "vip_category": "Media", "mdceo_notified": 1, "protocol_notes": "No photography in restricted areas. PR team.", "conference_room": "Video Conference Room"},
    {"visitor_type": "VIP", "visitor_full_name": "Prof. Arun Shankar", "mobile_number": "+91 9876400005", "email_id": "shankar@iitm.ac.in", "company__organisation": "IIT Madras", "id_proof_type": "Aadhaar", "id_proof_number": "1234-5678-9012", "purpose_of_visit": "Guest lecture — AI in Manufacturing", "person_to_visit": EMP["ceo"], "visit_date": add_days(BASE_DATE, 6), "expected_checkin": "09:00:00", "expected_checkout": "13:00:00", "vip_category": "Guest Speaker", "mdceo_notified": 1, "protocol_notes": "Training Hall. AV setup by 08:30.", "meal_required": 1, "conference_room": "Training Hall"},
    {"visitor_type": "VIP", "visitor_full_name": "Mr. Rajendra Shah", "mobile_number": "+91 9876400006", "email_id": "shah@shahgroup.com", "company__organisation": "Shah Group Holdings", "id_proof_type": "Passport", "id_proof_number": "S5432167", "purpose_of_visit": "Board meeting — strategy and dividend", "person_to_visit": EMP["ceo"], "visit_date": add_days(BASE_DATE, 7), "expected_checkin": "09:30:00", "expected_checkout": "16:00:00", "vip_category": "Board Member", "mdceo_notified": 1, "protocol_notes": "Reserved parking. Nameplate. Docs pre-distributed.", "meal_required": 1, "special_diet": "Jain", "conference_room": "Boardroom A"},
]


def run():
    print("=" * 60)
    print("VMS FULL DEMO — ALL REQUIRED FIELDS FILLED")
    print("=" * 60)

    # ── Step 1: Create VPs ──
    print("\n--- Step 1: Creating Visitor Passes ---")
    vp_names = []
    for vdata in VISITORS:
        items = vdata.pop("items", [])
        vp = frappe.new_doc("Visitor Pass")
        vp.update(vdata)
        vp.entry_type = "New"
        vp.request_channel = "Desk"
        for item in items:
            vp.append("visitor_items", item)
        vp.insert(ignore_permissions=True, ignore_mandatory=True)

        # Attach placeholder images
        id_url = attach_image(vp, "id_proof_scan", f"id_{vp.name}")
        photo_url = attach_image(vp, "visitor_photo", f"photo_{vp.name}")
        vp.db_set("id_proof_scan", id_url, update_modified=False)
        vp.db_set("visitor_photo", photo_url, update_modified=False)

        vp_names.append(vp.name)
        print(f"  Created {vp.visitor_type}: {vp.name} — {vp.visitor_full_name}")
    frappe.db.commit()

    # ── Step 2: Submit VPs through workflow ──
    print("\n--- Step 2: Approving Visitor Passes ---")
    approved = []
    from frappe.model.workflow import apply_workflow
    for vp_name in vp_names:
        try:
            vp = frappe.get_doc("Visitor Pass", vp_name)

            # Step through workflow: Draft → Pending → Approved
            if vp.visitor_type == "VIP":
                apply_workflow(vp, "Submit")  # Draft → Pending HOD
                vp.reload()
                apply_workflow(vp, "Approve")  # Pending HOD → Pending CEO
                vp.reload()
                apply_workflow(vp, "Approve")  # Pending CEO → Approved (submits)
            else:
                apply_workflow(vp, "Submit")  # Draft → Pending X
                vp.reload()
                apply_workflow(vp, "Approve")  # Pending X → Approved (submits)

            vp.reload()
            approved.append(vp.name)
            print(f"  Approved: {vp.name} — {vp.visitor_full_name} (status: {vp.status}, docstatus: {vp.docstatus})")
        except Exception as e:
            print(f"  FAILED approve {vp_name}: {e}")
            frappe.db.rollback()
    frappe.db.commit()

    # ── Step 3: Security Log — Check-In (first 18) ──
    print("\n--- Step 3: Security Log Check-Ins ---")
    checkin_vps = approved[:18]
    for vp_name in checkin_vps:
        try:
            vp = frappe.get_doc("Visitor Pass", vp_name)
            gate_photo = vp.visitor_photo

            sl = frappe.new_doc("Security Log")
            sl.visitor_pass = vp_name
            sl.event_type = "Check-In"
            sl.gate_name = "Main Gate"
            sl.photo_at_gate = gate_photo
            sl.id_proof_match = 1
            sl.pass_photo_match = 1
            sl.temperature = 36.4
            sl.symptoms_flag = 0
            sl.visited_area = "Reception"
            sl.remarks = f"{vp.visitor_full_name} checked in."
            sl.verification_notes = "ID verified. Photo matches."
            sl.insert(ignore_permissions=True)
            print(f"  Check-In: {sl.name} — {vp.visitor_full_name}")
        except Exception as e:
            print(f"  FAILED check-in {vp_name}: {e}")
            frappe.db.rollback()
    frappe.db.commit()

    # ── Step 4: Security Log — Check-Out (first 10) ──
    print("\n--- Step 4: Security Log Check-Outs ---")
    checkout_vps = checkin_vps[:10]
    for vp_name in checkout_vps:
        try:
            vp = frappe.get_doc("Visitor Pass", vp_name)
            sl = frappe.new_doc("Security Log")
            sl.visitor_pass = vp_name
            sl.event_type = "Check-Out"
            sl.gate_name = "Main Gate"
            sl.remarks = f"{vp.visitor_full_name} checked out. Visit complete."
            sl.insert(ignore_permissions=True)
            print(f"  Check-Out: {sl.name} — {vp.visitor_full_name}")
        except Exception as e:
            print(f"  FAILED check-out {vp_name}: {e}")
            frappe.db.rollback()
    frappe.db.commit()

    # ── Summary ──
    print("\n" + "=" * 60)
    print("FINAL DATABASE STATE")
    print("=" * 60)
    for dt in ["Visitor Pass", "Security Log", "Contact Trace Record",
               "Compliance Check", "Hospitality Request", "Visitor Blacklist"]:
        print(f"  {dt}: {frappe.db.count(dt)}")

    print("\n  VP by Status:")
    for r in frappe.db.sql("SELECT status, COUNT(*) c FROM `tabVisitor Pass` GROUP BY status", as_dict=1):
        print(f"    {r.status}: {r.c}")
    print("\n  VP by Type:")
    for r in frappe.db.sql("SELECT visitor_type, COUNT(*) c FROM `tabVisitor Pass` GROUP BY visitor_type", as_dict=1):
        print(f"    {r.visitor_type}: {r.c}")
    print("\n  Security Log by Event:")
    for r in frappe.db.sql("SELECT event_type, COUNT(*) c FROM `tabSecurity Log` GROUP BY event_type", as_dict=1):
        print(f"    {r.event_type}: {r.c}")
    print("=" * 60)
