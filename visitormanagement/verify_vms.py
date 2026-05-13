"""
VMS Comprehensive Verification — run via:
bench --site visitor.local execute visitormanagement.verify_vms.run
"""
import frappe

passed = 0
failed = 0

def test(name, condition, detail=""):
    global passed, failed
    if condition:
        print(f"  PASS: {name}")
        passed += 1
    else:
        print(f"  FAIL: {name} — {detail}")
        failed += 1

def run():
    global passed, failed
    passed = 0
    failed = 0

    print("=" * 65)
    print("VMS COMPREHENSIVE VERIFICATION")
    print("=" * 65)

    # ── 1. RECORD COUNTS ──
    print("\n--- 1. Record Counts ---")
    test("30 Visitor Passes", frappe.db.count("Visitor Pass") == 30, f"got {frappe.db.count('Visitor Pass')}")
    test("28 Security Logs (18 in + 10 out)", frappe.db.count("Security Log") == 28, f"got {frappe.db.count('Security Log')}")
    test("18 Contact Trace records", frappe.db.count("Contact Trace Record") >= 18)
    test("Hospitality Requests auto-created", frappe.db.count("Hospitality Request") >= 1, f"got {frappe.db.count('Hospitality Request')}")
    test("4 Blacklist entries", frappe.db.count("Visitor Blacklist") == 4)

    # ── 2. STATUS DISTRIBUTION ──
    print("\n--- 2. VP Status Distribution ---")
    statuses = {r.status: r.c for r in frappe.db.sql("SELECT status, COUNT(*) c FROM `tabVisitor Pass` GROUP BY status", as_dict=1)}
    test("12 Approved (at gate)", statuses.get("Approved") == 12, f"got {statuses.get('Approved')}")
    test("8 Checked-In (inside)", statuses.get("Checked-In") == 8, f"got {statuses.get('Checked-In')}")
    test("10 Checked-Out (done)", statuses.get("Checked-Out") == 10, f"got {statuses.get('Checked-Out')}")
    test("All 30 submitted (docstatus=1)", frappe.db.sql("SELECT COUNT(*) FROM `tabVisitor Pass` WHERE docstatus=1")[0][0] == 30)

    # ── 3. TYPE DISTRIBUTION ──
    print("\n--- 3. Type Distribution ---")
    types = {r.visitor_type: r.c for r in frappe.db.sql("SELECT visitor_type, COUNT(*) c FROM `tabVisitor Pass` GROUP BY visitor_type", as_dict=1)}
    for vtype in ["Contractor", "Candidate", "Customer", "Supplier", "VIP"]:
        test(f"6 {vtype} passes", types.get(vtype) == 6, f"got {types.get(vtype)}")

    # ── 4. FETCH-FROM FIELDS ──
    print("\n--- 4. Fetch-From Verification ---")

    # VP: host_department from Employee
    vps = frappe.db.sql("SELECT name, person_to_visit, host_department FROM `tabVisitor Pass` WHERE host_department IS NOT NULL AND host_department != '' LIMIT 3", as_dict=1)
    test("VP.host_department fetched from Employee", len(vps) > 0, "no host_department set")
    if vps:
        emp_dept = frappe.db.get_value("Employee", vps[0].person_to_visit, "department")
        test(f"  host_department matches ({vps[0].host_department})", vps[0].host_department == emp_dept, f"emp={emp_dept}")

    # SL: visitor_name from VP
    sl = frappe.db.sql("""SELECT sl.visitor_name, vp.visitor_full_name
        FROM `tabSecurity Log` sl JOIN `tabVisitor Pass` vp ON sl.visitor_pass = vp.name LIMIT 1""", as_dict=1)
    if sl:
        test("SL.visitor_name = VP.visitor_full_name", sl[0].visitor_name == sl[0].visitor_full_name)

    # SL: badge_number from VP
    sl_b = frappe.db.sql("""SELECT sl.badge_number, vp.badge_number vb
        FROM `tabSecurity Log` sl JOIN `tabVisitor Pass` vp ON sl.visitor_pass = vp.name
        WHERE sl.badge_number IS NOT NULL AND sl.badge_number != '' LIMIT 1""", as_dict=1)
    test("SL.badge_number = VP.badge_number", len(sl_b) > 0 and sl_b[0].badge_number == sl_b[0].vb,
         f"sl={sl_b[0].badge_number if sl_b else 'none'}, vp={sl_b[0].vb if sl_b else 'none'}")

    # SL: visitor_company from VP
    sl_c = frappe.db.sql("""SELECT sl.visitor_company, vp.company__organisation
        FROM `tabSecurity Log` sl JOIN `tabVisitor Pass` vp ON sl.visitor_pass = vp.name
        WHERE sl.visitor_company IS NOT NULL AND sl.visitor_company != '' LIMIT 1""", as_dict=1)
    test("SL.visitor_company = VP.company__organisation", len(sl_c) > 0 and sl_c[0].visitor_company == sl_c[0].company__organisation)

    # ── 5. ID MASKING ──
    print("\n--- 5. ID Proof Masking ---")
    sl_masked = frappe.db.sql("""SELECT sl.id_proof_number as masked, vp.id_proof_number as raw
        FROM `tabSecurity Log` sl JOIN `tabVisitor Pass` vp ON sl.visitor_pass = vp.name
        WHERE sl.id_proof_number IS NOT NULL AND sl.id_proof_number != '' LIMIT 5""", as_dict=1)
    test("ID numbers are masked on Security Log", len(sl_masked) > 0 and "X" in sl_masked[0].masked,
         f"got: {sl_masked[0].masked if sl_masked else 'none'}")
    if sl_masked:
        m = sl_masked[0]
        test(f"  Last 4 preserved: ...{m.masked[-4:]} matches ...{m.raw[-4:]}", m.masked[-4:] == m.raw[-4:])
        test(f"  Full number NOT exposed: {m.masked}", m.masked != m.raw)
        print(f"    Example: {m.raw} → {m.masked}")

    # ── 6. BADGE COLOURS BY TYPE ──
    print("\n--- 6. Badge Colours ---")
    colour_map = {"Contractor": "Orange", "Candidate": "Purple", "Customer": "Green", "Supplier": "Teal", "VIP": "Gold"}
    for vtype, expected in colour_map.items():
        colours = frappe.db.sql_list(f"SELECT DISTINCT badge_colour FROM `tabVisitor Pass` WHERE visitor_type='{vtype}'")
        test(f"{vtype} badge = {expected}", colours == [expected], f"got {colours}")

    # ── 7. CONTRACTOR FIELDS ──
    print("\n--- 7. Contractor Validations ---")
    cons = frappe.get_all("Visitor Pass", filters={"visitor_type": "Contractor"}, fields=["tools_list"])
    test("All contractors: tools_list filled", all(c.tools_list for c in cons))

    # ── 8. CANDIDATE FIELDS ──
    print("\n--- 8. Candidate Fields ---")
    cands = frappe.get_all("Visitor Pass", filters={"visitor_type": "Candidate"}, fields=["position_applied", "candidate_interview_type", "interview_panel"])
    test("All candidates: position_applied filled", all(c.position_applied for c in cands))
    test("All candidates: interview_type filled", all(c.candidate_interview_type for c in cands))
    test("All candidates: interview_panel filled", all(c.interview_panel for c in cands))

    # ── 9. CUSTOMER FIELDS ──
    print("\n--- 9. Customer Fields ---")
    custs = frappe.get_all("Visitor Pass", filters={"visitor_type": "Customer"}, fields=["visit_category", "sales_executive"])
    test("All customers: visit_category filled", all(c.visit_category for c in custs))
    test("All customers: sales_executive linked", all(c.sales_executive for c in custs))

    # ── 10. SUPPLIER FIELDS ──
    print("\n--- 10. Supplier Fields ---")
    sups = frappe.get_all("Visitor Pass", filters={"visitor_type": "Supplier"}, fields=["supplier_visit_mode"])
    test("All suppliers: visit_mode filled", all(s.supplier_visit_mode for s in sups))

    # ── 11. VIP FIELDS ──
    print("\n--- 11. VIP Fields ---")
    vips = frappe.get_all("Visitor Pass", filters={"visitor_type": "VIP"}, fields=["vip_category", "mdceo_notified", "protocol_notes"])
    test("All VIPs: category filled", all(v.vip_category for v in vips))
    test("All VIPs: mdceo_notified=1", all(v.mdceo_notified for v in vips))
    test("All VIPs: protocol_notes filled", all(v.protocol_notes for v in vips))

    # ── 12. AUTO-CREATION CHAINS ──
    print("\n--- 12. Auto-Creation Chains ---")

    # Hospitality back-link
    hr_linked = frappe.db.sql("SELECT COUNT(*) c FROM `tabVisitor Pass` WHERE hospitality_request IS NOT NULL AND hospitality_request != ''")[0][0]
    test(f"VP.hospitality_request back-linked ({hr_linked} VPs)", hr_linked > 0)

    # ── 13. QR CODES ──
    print("\n--- 13. QR Codes ---")
    qr = frappe.db.sql("SELECT COUNT(*) c FROM `tabVisitor Pass` WHERE qr_code_image IS NOT NULL AND qr_code_image != ''")[0][0]
    test(f"QR codes generated for all 30 VPs", qr == 30, f"got {qr}")

    # ── 14. GATE AUTO-ASSIGN ──
    print("\n--- 14. Gate Assignment ---")
    gates = frappe.db.sql("SELECT gate_name, COUNT(*) c FROM `tabSecurity Log` WHERE event_type='Check-In' GROUP BY gate_name", as_dict=1)
    test("Gates assigned on check-in", len(gates) > 0)
    for g in gates:
        print(f"    {g.gate_name}: {g.c} check-ins")

    # ── 15. VISITOR ITEMS ──
    print("\n--- 15. Visitor Items ---")
    items = frappe.db.count("Visitor Item")
    test(f"Visitor items attached ({items} items)", items > 0)

    # ── 16. WORKFLOWS ──
    print("\n--- 16. Active Workflows ---")
    for wf_name in ["Visitor Pass Approval", "Conference Room Booking Approval"]:
        active = frappe.db.get_value("Workflow", wf_name, "is_active")
        test(f"{wf_name} is active", active == 1)

    # ── 17. NOTIFICATIONS ──
    print("\n--- 17. Notifications ---")
    notifs = frappe.get_all("Notification", filters={"module": ["in", ["Visitor Management", "Conference Room"]]}, fields=["name", "enabled", "document_type"])
    for n in notifs:
        status = "enabled" if n.enabled else "disabled"
        print(f"    {n.name} ({n.document_type}) — {status}")
    test(f"{len(notifs)} notifications configured", len(notifs) >= 1)

    # ── SUMMARY ──
    print("\n" + "=" * 65)
    print(f"RESULTS: {passed} PASSED, {failed} FAILED out of {passed + failed}")
    print("=" * 65)

    if failed == 0:
        print("\nALL TESTS PASSED — System is fully operational.")
    else:
        print(f"\n{failed} issue(s) found — review FAIL items above.")
