"""
VMS Validation Test Suite — run via:
bench --site visitor.local execute visitormanagement.test_validations.run
"""
import base64
import frappe
from frappe.utils import today, add_days, get_datetime

EMP_OPS = "HR-EMP-00003"  # Arun — Operations
EMP_HR = "HR-EMP-00002"   # Priya — HR
EMP_MKT = "HR-EMP-00004"  # Divya — Marketing

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8/5+hHgAHggJ/PchI7wAAAABJRU5ErkJggg==")

passed = 0
failed = 0
results = []


def attach_image(doc, fieldname):
    """Attach placeholder image."""
    f = frappe.get_doc({
        "doctype": "File", "file_name": f"{fieldname}_{frappe.generate_hash(length=4)}.png",
        "content": PNG, "is_private": 1,
        "attached_to_doctype": doc.doctype, "attached_to_name": doc.name, "attached_to_field": fieldname,
    })
    f.save(ignore_permissions=True)
    return f.file_url


def test(name, fn):
    global passed, failed
    try:
        fn()
        print(f"  PASS: {name}")
        results.append(("PASS", name, ""))
        passed += 1
    except AssertionError as e:
        print(f"  FAIL: {name} — {e}")
        results.append(("FAIL", name, str(e)))
        failed += 1
    except Exception as e:
        # Some tests intentionally raise frappe.ValidationError inside `expect_error`
        print(f"  ERROR: {name} — {type(e).__name__}: {e}")
        results.append(("ERROR", name, str(e)))
        failed += 1
    finally:
        frappe.db.rollback()


def expect_validation_error(callable_, expected_keyword=None):
    """Call `callable_`. Assert it raises frappe.ValidationError. Optionally check message."""
    try:
        callable_()
    except frappe.ValidationError as e:
        if expected_keyword and expected_keyword.lower() not in str(e).lower():
            raise AssertionError(f"Wrong error. Expected keyword '{expected_keyword}', got: {e}")
        return
    raise AssertionError("Expected ValidationError but no error raised")


def make_vp(**overrides):
    """Build a valid Visitor Pass dict — overrides patch it."""
    base = {
        "visitor_type": "Customer",
        "visitor_full_name": "Test Visitor",
        "mobile_number": "+91 9876543210",
        "email_id": "test@example.com",
        "company__organisation": "Test Corp",
        "id_proof_type": "Aadhaar",
        "id_proof_number": "123456789012",
        "purpose_of_visit": "Meeting",
        "person_to_visit": EMP_MKT,
        "visit_date": add_days(today(), 30),  # far future to avoid duplicates
        "expected_checkin": "10:00:00",
        "expected_checkout": "12:00:00",
        "entry_type": "New",
        "request_channel": "Desk",
    }
    base.update(overrides)
    return base


def insert_vp(**overrides):
    """Insert a VP (ignoring image mandatory), attach images, return doc."""
    vp = frappe.new_doc("Visitor Pass")
    vp.update(make_vp(**overrides))
    vp.insert(ignore_permissions=True, ignore_mandatory=True)
    url1 = attach_image(vp, "id_proof_scan")
    url2 = attach_image(vp, "visitor_photo")
    vp.db_set("id_proof_scan", url1, update_modified=False)
    vp.db_set("visitor_photo", url2, update_modified=False)
    vp.reload()
    return vp


def run():
    global passed, failed, results
    passed = failed = 0
    results = []

    print("=" * 70)
    print("VMS VALIDATION TEST SUITE")
    print("=" * 70)

    # ═══════════════════════════════════════════════════════════
    # VISITOR PASS — Schedule Validations
    # ═══════════════════════════════════════════════════════════
    print("\n--- VISITOR PASS: Schedule ---")

    def t_past_date():
        b = make_vp(visit_date=add_days(today(), -1))
        vp = frappe.new_doc("Visitor Pass")
        vp.update(b)
        expect_validation_error(lambda: vp.insert(ignore_permissions=True, ignore_mandatory=True), "past")
    test("Past date blocked", t_past_date)

    def t_future_date_ceiling():
        b = make_vp(visit_date=add_days(today(), 100))
        vp = frappe.new_doc("Visitor Pass")
        vp.update(b)
        expect_validation_error(lambda: vp.insert(ignore_permissions=True, ignore_mandatory=True), "90 days")
    test("Future date > 90 days blocked", t_future_date_ceiling)

    def t_checkin_after_checkout():
        b = make_vp(expected_checkin="14:00:00", expected_checkout="10:00:00")
        vp = frappe.new_doc("Visitor Pass")
        vp.update(b)
        expect_validation_error(lambda: vp.insert(ignore_permissions=True, ignore_mandatory=True), "before")
    test("Check-in >= Check-out blocked", t_checkin_after_checkout)

    def t_valid_schedule():
        vp = insert_vp(visit_date=add_days(today(), 31))
        assert vp.name
    test("Valid schedule accepted", t_valid_schedule)

    # ═══════════════════════════════════════════════════════════
    # VISITOR PASS — Format Validations
    # ═══════════════════════════════════════════════════════════
    print("\n--- VISITOR PASS: Formats ---")

    def t_invalid_email():
        b = make_vp(email_id="abc@@@test")
        vp = frappe.new_doc("Visitor Pass")
        vp.update(b)
        expect_validation_error(lambda: vp.insert(ignore_permissions=True, ignore_mandatory=True), "email")
    test("Invalid email format blocked", t_invalid_email)

    def t_invalid_aadhaar():
        b = make_vp(id_proof_type="Aadhaar", id_proof_number="12345", visit_date=add_days(today(), 32))
        vp = frappe.new_doc("Visitor Pass")
        vp.update(b)
        expect_validation_error(lambda: vp.insert(ignore_permissions=True, ignore_mandatory=True), "aadhaar")
    test("Aadhaar < 12 digits blocked", t_invalid_aadhaar)

    def t_invalid_pan():
        b = make_vp(id_proof_type="PAN Card", id_proof_number="INVALID123", visit_date=add_days(today(), 33))
        vp = frappe.new_doc("Visitor Pass")
        vp.update(b)
        expect_validation_error(lambda: vp.insert(ignore_permissions=True, ignore_mandatory=True), "pan")
    test("Invalid PAN format blocked", t_invalid_pan)

    def t_valid_pan():
        vp = insert_vp(id_proof_type="PAN Card", id_proof_number="AABCD1234E", visit_date=add_days(today(), 34))
        assert vp.name
    test("Valid PAN accepted", t_valid_pan)

    def t_valid_aadhaar_with_hyphens():
        vp = insert_vp(id_proof_type="Aadhaar", id_proof_number="1234-5678-9012", visit_date=add_days(today(), 35))
        assert vp.name
    test("Valid Aadhaar (12 digits with hyphens) accepted", t_valid_aadhaar_with_hyphens)

    # ═══════════════════════════════════════════════════════════
    # VISITOR PASS — Host & Duplicate
    # ═══════════════════════════════════════════════════════════
    print("\n--- VISITOR PASS: Host & Duplicates ---")

    def t_inactive_host():
        # Create a disabled employee — set via db_set to bypass HRMS mandatory relieving_date
        emp = frappe.new_doc("Employee")
        emp.employee_name = "Inactive Test"
        emp.first_name = "Inactive"
        emp.gender = "Male"
        emp.date_of_birth = "1990-01-01"
        emp.date_of_joining = "2024-01-01"
        emp.company = "Yuvi Enterprises"
        emp.status = "Active"
        emp.insert(ignore_permissions=True, ignore_mandatory=True)
        frappe.db.set_value("Employee", emp.name, "status", "Left")
        frappe.db.commit()

        b = make_vp(person_to_visit=emp.name, visit_date=add_days(today(), 36))
        vp = frappe.new_doc("Visitor Pass")
        vp.update(b)
        expect_validation_error(lambda: vp.insert(ignore_permissions=True, ignore_mandatory=True), "active")
    test("Inactive host blocked", t_inactive_host)

    def t_duplicate_pass():
        import random
        unique_id = f"{random.randint(100000000000, 999999999999)}"
        # Create first VP — commit so it's persistent
        vp1 = insert_vp(id_proof_number=unique_id, visit_date=add_days(today(), 37))
        frappe.db.commit()
        # Try to create another with same ID + same date
        b = make_vp(id_proof_number=unique_id, visit_date=add_days(today(), 37))
        vp2 = frappe.new_doc("Visitor Pass")
        vp2.update(b)
        try:
            expect_validation_error(lambda: vp2.insert(ignore_permissions=True, ignore_mandatory=True), "duplicate")
        finally:
            # Cleanup
            frappe.delete_doc("Visitor Pass", vp1.name, force=True, ignore_permissions=True, delete_permanently=True)
            frappe.db.commit()
    test("Duplicate pass (same ID + same date) blocked", t_duplicate_pass)

    def t_same_id_different_date_ok():
        import random
        unique_id = f"{random.randint(100000000000, 999999999999)}"
        vp1 = insert_vp(id_proof_number=unique_id, visit_date=add_days(today(), 38))
        frappe.db.commit()
        vp2 = insert_vp(id_proof_number=unique_id, visit_date=add_days(today(), 39))
        try:
            assert vp2.name
        finally:
            frappe.delete_doc("Visitor Pass", vp1.name, force=True, ignore_permissions=True, delete_permanently=True)
            frappe.delete_doc("Visitor Pass", vp2.name, force=True, ignore_permissions=True, delete_permanently=True)
            frappe.db.commit()
    test("Same ID on different dates allowed", t_same_id_different_date_ok)

    # ═══════════════════════════════════════════════════════════
    # VISITOR PASS — Images at submit
    # ═══════════════════════════════════════════════════════════
    print("\n--- VISITOR PASS: Required Documents at Submit ---")

    def t_submit_without_photo():
        from frappe.model.workflow import apply_workflow
        vp = frappe.new_doc("Visitor Pass")
        vp.update(make_vp(visit_date=add_days(today(), 40), id_proof_number="777777777777"))
        vp.insert(ignore_permissions=True, ignore_mandatory=True)
        # Attach only ID scan, NOT photo
        url = attach_image(vp, "id_proof_scan")
        vp.db_set("id_proof_scan", url, update_modified=False)
        # Walk workflow: Submit -> Pending -> Approve (which triggers before_submit)
        vp.reload()
        apply_workflow(vp, "Submit")
        vp.reload()
        expect_validation_error(lambda: apply_workflow(vp, "Approve"), "photo")
    test("Submit without visitor_photo blocked", t_submit_without_photo)

    # ═══════════════════════════════════════════════════════════
    # VISITOR PASS — Duration
    # ═══════════════════════════════════════════════════════════
    print("\n--- VISITOR PASS: Duration ---")

    def t_visit_too_long():
        # VMS Settings has max_visit_duration_hrs = 12
        b = make_vp(expected_checkin="06:00:00", expected_checkout="22:00:00", visit_date=add_days(today(), 41))
        vp = frappe.new_doc("Visitor Pass")
        vp.update(b)
        expect_validation_error(lambda: vp.insert(ignore_permissions=True, ignore_mandatory=True), "maximum")
    test("Visit duration > max (12h) blocked", t_visit_too_long)

    # ═══════════════════════════════════════════════════════════
    # VISITOR INVITATION
    # ═══════════════════════════════════════════════════════════
    print("\n--- VISITOR INVITATION ---")

    def t_inv_past_date():
        inv = frappe.new_doc("Visitor Invitation")
        inv.update({
            "visitor_type": "Customer", "visitor_email": "inv@test.com", "host_employee": EMP_MKT,
            "visit_date": add_days(today(), -1),
            "expected_checkin": "10:00:00", "expected_checkout": "12:00:00",
            "purpose_of_visit": "Test",
        })
        expect_validation_error(lambda: inv.insert(ignore_permissions=True, ignore_mandatory=True), "past")
    test("Invitation past date blocked", t_inv_past_date)

    def t_inv_invalid_email():
        inv = frappe.new_doc("Visitor Invitation")
        inv.update({
            "visitor_type": "Customer", "visitor_email": "bad@@email", "host_employee": EMP_MKT,
            "visit_date": add_days(today(), 10),
            "expected_checkin": "10:00:00", "expected_checkout": "12:00:00",
            "purpose_of_visit": "Test",
        })
        expect_validation_error(lambda: inv.insert(ignore_permissions=True, ignore_mandatory=True), "email")
    test("Invitation invalid email blocked", t_inv_invalid_email)

    # ═══════════════════════════════════════════════════════════
    # SUMMARY
    # ═══════════════════════════════════════════════════════════
    print("\n" + "=" * 70)
    print(f"RESULTS: {passed} PASSED, {failed} FAILED / {passed + failed} TESTS")
    print("=" * 70)
    if failed == 0:
        print("\nALL VALIDATIONS WORKING.")
    else:
        print("\nFailed tests:")
        for status, name, err in results:
            if status != "PASS":
                print(f"  [{status}] {name}: {err[:150]}")
