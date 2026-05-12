"""
Load test for Visitor Pass creation.

Run:
    bench --site vms.local execute visitormanagement._loadtest.run --kwargs '{"target":1000}'

Cleanup:
    bench --site vms.local execute visitormanagement._loadtest.cleanup
"""
import time
import frappe
from frappe.utils import today, add_days


def run(target=1000, batch_commit=50):
    base_date = today()
    host = frappe.db.get_value("Employee", {"status": "Active"}, "name")
    if not host:
        return {"error": "no active employee found"}

    start_count = frappe.db.count("Visitor Pass")
    t_start = time.time()

    successes = 0
    failures = []
    per_record = []

    for i in range(target):
        try:
            t0 = time.time()
            vp = frappe.new_doc("Visitor Pass")
            vp.update({
                "visitor_type": "Supplier",
                "entry_type": "New",
                "request_channel": "Desk",
                "visit_date": add_days(base_date, (i % 60) + 1),
                "expected_checkin": "10:00:00",
                "expected_checkout": "12:00:00",
                "visitor_full_name": f"LoadTest Visitor {i+1}",
                "company__organisation": f"LT Co {i % 50}",
                "email_id": f"loadtest_{i}@example.com",
                "mobile_number": f"+91 99{i:08d}",
                "id_proof_type": "Aadhaar",
                "id_proof_number": f"5{i:011d}",
                "purpose_of_visit": f"Load test #{i+1}",
                "person_to_visit": host,
                "supplier_visit_mode": "Meeting",
            })
            vp.insert(ignore_permissions=True, ignore_mandatory=True)
            successes += 1
            per_record.append(time.time() - t0)
            if (i + 1) % batch_commit == 0:
                frappe.db.commit()
        except Exception as e:
            failures.append(f"#{i}: {str(e)[:120]}")

    frappe.db.commit()
    elapsed = time.time() - t_start

    r0 = time.time()
    frappe.get_all("Visitor Pass", filters={"visitor_full_name": ["like", "LoadTest%"]},
                   fields=["name", "visitor_full_name", "visit_date", "status"],
                   limit_page_length=20)
    read_list_ms = (time.time() - r0) * 1000

    r0 = time.time()
    total = frappe.db.count("Visitor Pass")
    read_count_ms = (time.time() - r0) * 1000

    r0 = time.time()
    one_name = frappe.db.get_value("Visitor Pass",
                                   {"visitor_full_name": ["like", "LoadTest%"]}, "name")
    if one_name:
        frappe.get_doc("Visitor Pass", one_name)
    read_single_ms = (time.time() - r0) * 1000

    per_record.sort()
    n = len(per_record)
    pct = lambda p: per_record[min(int(n * p), n - 1)] if n else 0
    avg = sum(per_record) / n if n else 0

    report = {
        "target": target,
        "inserted": successes,
        "failed": len(failures),
        "elapsed_seconds": round(elapsed, 2),
        "throughput_per_sec": round(successes / elapsed, 2) if elapsed > 0 else 0,
        "avg_insert_ms": round(avg * 1000, 1),
        "p50_insert_ms": round(pct(0.50) * 1000, 1),
        "p95_insert_ms": round(pct(0.95) * 1000, 1),
        "p99_insert_ms": round(pct(0.99) * 1000, 1),
        "read_list_20_ms": round(read_list_ms, 1),
        "read_count_total_ms": round(read_count_ms, 1),
        "read_single_doc_ms": round(read_single_ms, 1),
        "vps_before": start_count,
        "vps_after": total,
        "sample_failures": failures[:3],
    }

    print("=" * 60)
    print("VISITOR PASS LOAD TEST RESULT")
    print("=" * 60)
    for k, v in report.items():
        print(f"  {k:25s}: {v}")
    print("=" * 60)
    return report


def insert_batch(worker_id=0, count=100):
    """Insert a batch of LoadTest passes; designed to run as a parallel worker.

    Each worker uses a unique numeric range so id_proof_number and email collide
    with no other worker.
    """
    base_date = today()
    host = frappe.db.get_value("Employee", {"status": "Active"}, "name")
    if not host:
        return {"worker": worker_id, "error": "no active employee"}

    offset = 100000 + (worker_id * 10000)
    t_start = time.time()
    successes = 0
    failures = []

    for i in range(count):
        idx = offset + i
        try:
            vp = frappe.new_doc("Visitor Pass")
            vp.update({
                "visitor_type": "Supplier",
                "entry_type": "New",
                "request_channel": "Desk",
                "visit_date": add_days(base_date, (i % 60) + 1),
                "expected_checkin": "10:00:00",
                "expected_checkout": "12:00:00",
                "visitor_full_name": f"LoadTest W{worker_id} V{i+1}",
                "company__organisation": f"LT Concurrent W{worker_id}",
                "email_id": f"loadtest_w{worker_id}_{i}@example.com",
                "mobile_number": f"+91 88{idx:08d}",
                "id_proof_type": "Aadhaar",
                "id_proof_number": f"6{idx:011d}",
                "purpose_of_visit": f"Concurrent W{worker_id} #{i+1}",
                "person_to_visit": host,
                "supplier_visit_mode": "Meeting",
            })
            vp.insert(ignore_permissions=True, ignore_mandatory=True)
            successes += 1
        except Exception as e:
            failures.append(str(e)[:120])

    frappe.db.commit()
    elapsed = time.time() - t_start
    result = {
        "worker": worker_id,
        "inserted": successes,
        "failed": len(failures),
        "elapsed_sec": round(elapsed, 2),
        "sample_failure": failures[0] if failures else None,
    }
    print(f"WORKER {worker_id}: {result}")
    return result


def prepare_for_approval():
    """Bulk-fill mandatory file fields on LoadTest passes so they can be approved."""
    placeholder = "/private/files/loadtest-placeholder.png"
    t0 = time.time()
    frappe.db.sql("""
        UPDATE `tabVisitor Pass`
        SET id_proof_scan = %s, visitor_photo = %s
        WHERE visitor_full_name LIKE 'LoadTest%%'
          AND (id_proof_scan IS NULL OR id_proof_scan = ''
               OR visitor_photo IS NULL OR visitor_photo = '')
    """, (placeholder, placeholder))
    frappe.db.commit()
    return {"updated_ms": round((time.time() - t0) * 1000, 1)}


def approve_all(limit=1000, batch_commit=25):
    """Walk LoadTest passes from Draft -> Pending System Manager -> Approved."""
    from frappe.model.workflow import apply_workflow

    names = frappe.get_all(
        "Visitor Pass",
        filters={"visitor_full_name": ["like", "LoadTest%"], "status": "Draft"},
        pluck="name",
        limit_page_length=limit,
    )

    if not names:
        return {"error": "no LoadTest Draft passes to approve"}

    t_start = time.time()
    submitted = 0
    approved = 0
    failures = []
    per_record = []

    for i, n in enumerate(names):
        try:
            t0 = time.time()
            vp = frappe.get_doc("Visitor Pass", n)
            apply_workflow(vp, "Submit")
            submitted += 1

            vp.reload()
            apply_workflow(vp, "Approve")
            approved += 1
            per_record.append(time.time() - t0)

            if (i + 1) % batch_commit == 0:
                frappe.db.commit()
        except Exception as e:
            failures.append(f"{n}: {str(e)[:100]}")

    frappe.db.commit()
    elapsed = time.time() - t_start

    r0 = time.time()
    hr_count = frappe.db.count("Hospitality Request",
                               {"visitor_pass": ["like", "VP-%"]})
    read_hr_ms = (time.time() - r0) * 1000

    per_record.sort()
    n = len(per_record)
    pct = lambda p: per_record[min(int(n * p), n - 1)] if n else 0
    avg = sum(per_record) / n if n else 0

    report = {
        "candidates": len(names),
        "submitted": submitted,
        "approved": approved,
        "failed": len(failures),
        "elapsed_seconds": round(elapsed, 2),
        "throughput_per_sec": round(approved / elapsed, 2) if elapsed > 0 else 0,
        "avg_submit_approve_ms": round(avg * 1000, 1),
        "p50_ms": round(pct(0.50) * 1000, 1),
        "p95_ms": round(pct(0.95) * 1000, 1),
        "p99_ms": round(pct(0.99) * 1000, 1),
        "hospitality_requests_total": hr_count,
        "read_hr_count_ms": round(read_hr_ms, 1),
        "sample_failures": failures[:3],
    }

    print("=" * 60)
    print("VISITOR PASS APPROVAL THROUGHPUT")
    print("=" * 60)
    for k, v in report.items():
        print(f"  {k:25s}: {v}")
    print("=" * 60)
    return report


def run_crb(target=1000, batch_commit=50, day_offset=0, title_tag=""):
    """Bulk-create Conference Room Bookings spread across rooms/days/slots to avoid overlap."""
    base_date = today()
    rooms = frappe.get_all("Conference Room",
                           filters={"is_active": 1},
                           fields=["name"], pluck="name")
    if not rooms:
        return {"error": "no active conference rooms"}
    bookers = frappe.get_all("Employee", filters={"status": "Active"},
                             fields=["name"], pluck="name", limit_page_length=10)
    if not bookers:
        return {"error": "no active employees"}

    # 10 one-hour slots per day, 9:00 to 19:00 — fits every room's operating window
    slots = [(f"{h:02d}:00:00", f"{h+1:02d}:00:00") for h in range(9, 19)]

    t_start = time.time()
    successes = 0
    failures = []
    per_record = []

    for i in range(target):
        try:
            t0 = time.time()
            room_idx = i % len(rooms)
            slot_idx = (i // len(rooms)) % len(slots)
            day_idx = (i // (len(rooms) * len(slots))) + 1 + day_offset
            start_t, end_t = slots[slot_idx]

            crb = frappe.new_doc("Conference Room Booking")
            crb.update({
                "meeting_title": f"LoadTest CRB{title_tag} #{i+1}",
                "conference_room": rooms[room_idx],
                "meeting_type": "Internal",
                "booking_date": add_days(base_date, day_idx),
                "start_time": start_t,
                "end_time": end_t,
                "booked_by": bookers[i % len(bookers)],
                "expected_attendees": 3,
                "description": f"Concurrent bulk-test booking #{i+1}",
            })
            crb.insert(ignore_permissions=True, ignore_mandatory=True)
            successes += 1
            per_record.append(time.time() - t0)
            if (i + 1) % batch_commit == 0:
                frappe.db.commit()
        except Exception as e:
            failures.append(f"#{i}: {str(e)[:100]}")

    frappe.db.commit()
    elapsed = time.time() - t_start

    r0 = time.time()
    frappe.get_all("Conference Room Booking",
                   filters={"meeting_title": ["like", "LoadTest CRB%"]},
                   fields=["name", "meeting_title", "booking_date", "start_time"],
                   limit_page_length=20)
    read_list_ms = (time.time() - r0) * 1000

    per_record.sort()
    n = len(per_record)
    pct = lambda p: per_record[min(int(n * p), n - 1)] if n else 0
    avg = sum(per_record) / n if n else 0

    report = {
        "target": target,
        "inserted": successes,
        "failed": len(failures),
        "elapsed_seconds": round(elapsed, 2),
        "throughput_per_sec": round(successes / elapsed, 2) if elapsed > 0 else 0,
        "avg_insert_ms": round(avg * 1000, 1),
        "p50_insert_ms": round(pct(0.50) * 1000, 1),
        "p95_insert_ms": round(pct(0.95) * 1000, 1),
        "p99_insert_ms": round(pct(0.99) * 1000, 1),
        "read_list_20_ms": round(read_list_ms, 1),
        "sample_failures": failures[:3],
    }
    print("=" * 60)
    print("CONFERENCE ROOM BOOKING LOAD TEST")
    print("=" * 60)
    for k, v in report.items():
        print(f"  {k:25s}: {v}")
    print("=" * 60)
    return report


def run_hr(target=1000, batch_commit=50):
    """Bulk-create Hospitality Requests linked to existing Approved LoadTest VPs."""
    vps = frappe.get_all("Visitor Pass",
                         filters={"visitor_full_name": ["like", "LoadTest Visitor%"],
                                  "status": "Approved"},
                         pluck="name", limit_page_length=target)
    if not vps:
        return {"error": "no Approved LoadTest Visitor Passes — run run() + approve_all() first"}

    t_start = time.time()
    successes = 0
    failures = []
    per_record = []

    for i, vp in enumerate(vps[:target]):
        try:
            t0 = time.time()
            hr = frappe.new_doc("Hospitality Request")
            hr.update({
                "visitor_pass": vp,
                "meal_required": 1,
                "meal_type": "Lunch",
                "hospitality_type": "Single Meal",
                "special_diet": "None",
                "accessibility_requirements": "None",
                "no_of_guests": 1,
                "status": "Pending",
            })
            hr.insert(ignore_permissions=True, ignore_mandatory=True)
            successes += 1
            per_record.append(time.time() - t0)
            if (i + 1) % batch_commit == 0:
                frappe.db.commit()
        except Exception as e:
            failures.append(f"{vp}: {str(e)[:100]}")

    frappe.db.commit()
    elapsed = time.time() - t_start

    r0 = time.time()
    frappe.get_all("Hospitality Request",
                   filters={"visitor_pass": ["like", "VP-%"]},
                   fields=["name", "visitor_pass", "meal_type"],
                   limit_page_length=20)
    read_list_ms = (time.time() - r0) * 1000

    per_record.sort()
    n = len(per_record)
    pct = lambda p: per_record[min(int(n * p), n - 1)] if n else 0
    avg = sum(per_record) / n if n else 0

    report = {
        "target": target,
        "candidates": len(vps),
        "inserted": successes,
        "failed": len(failures),
        "elapsed_seconds": round(elapsed, 2),
        "throughput_per_sec": round(successes / elapsed, 2) if elapsed > 0 else 0,
        "avg_insert_ms": round(avg * 1000, 1),
        "p50_insert_ms": round(pct(0.50) * 1000, 1),
        "p95_insert_ms": round(pct(0.95) * 1000, 1),
        "p99_insert_ms": round(pct(0.99) * 1000, 1),
        "read_list_20_ms": round(read_list_ms, 1),
        "sample_failures": failures[:3],
    }
    print("=" * 60)
    print("HOSPITALITY REQUEST LOAD TEST")
    print("=" * 60)
    for k, v in report.items():
        print(f"  {k:25s}: {v}")
    print("=" * 60)
    return report


def cleanup():
    deleted = {"Visitor Pass": 0, "Conference Room Booking": 0, "Hospitality Request": 0}

    # Hospitality Requests first (no FK constraint, but logical order)
    hr_names = frappe.get_all("Hospitality Request",
                              filters={"visitor_pass": ["in",
                                  frappe.get_all("Visitor Pass",
                                                 filters={"visitor_full_name": ["like", "LoadTest%"]},
                                                 pluck="name")]},
                              pluck="name")
    for n in hr_names:
        try:
            frappe.delete_doc("Hospitality Request", n, force=True, ignore_permissions=True)
            deleted["Hospitality Request"] += 1
        except Exception:
            pass

    crb_names = frappe.get_all("Conference Room Booking",
                               filters={"meeting_title": ["like", "LoadTest CRB%"]},
                               pluck="name")
    for n in crb_names:
        try:
            frappe.delete_doc("Conference Room Booking", n, force=True, ignore_permissions=True)
            deleted["Conference Room Booking"] += 1
        except Exception:
            pass

    vp_names = frappe.get_all("Visitor Pass",
                              filters={"visitor_full_name": ["like", "LoadTest%"]},
                              pluck="name")
    for n in vp_names:
        try:
            frappe.delete_doc("Visitor Pass", n, force=True, ignore_permissions=True)
            deleted["Visitor Pass"] += 1
        except Exception:
            pass

    frappe.db.commit()
    return deleted
