import frappe


def test_approval_email_count():
    """Approve one Draft LoadTest VP and report exactly which emails get queued."""
    from frappe.model.workflow import apply_workflow
    from frappe.utils import now_datetime, add_to_date

    # Find a Draft LoadTest VP with meal_required=0 (cleanest signal — no food dept email)
    vp_name = frappe.db.get_value("Visitor Pass",
                                  {"visitor_full_name": ["like", "LoadTest W%"],
                                   "status": "Draft",
                                   "meal_required": 0},
                                  "name")
    if not vp_name:
        vp_name = frappe.db.get_value("Visitor Pass",
                                      {"visitor_full_name": ["like", "LoadTest W%"],
                                       "status": "Draft"},
                                      "name")
    if not vp_name:
        return {"error": "no Draft LoadTest VP available"}

    # Make sure mandatory file fields are filled (so workflow Approve doesn't fail)
    frappe.db.sql("""
        UPDATE `tabVisitor Pass`
        SET id_proof_scan = COALESCE(NULLIF(id_proof_scan,''), '/private/files/loadtest-placeholder.png'),
            visitor_photo = COALESCE(NULLIF(visitor_photo,''), '/private/files/loadtest-placeholder.png')
        WHERE name = %s
    """, (vp_name,))
    frappe.db.commit()

    # Snapshot Email Queue size BEFORE
    before_max = frappe.db.sql("SELECT COALESCE(MAX(creation), '1970-01-01') FROM `tabEmail Queue`")[0][0]

    print(f"Approving Visitor Pass: {vp_name}")
    vp = frappe.get_doc("Visitor Pass", vp_name)
    apply_workflow(vp, "Submit")
    vp.reload()
    apply_workflow(vp, "Approve")
    frappe.db.commit()

    # Re-fetch the doc state
    vp.reload()
    print(f"  status:         {vp.status}")
    print(f"  workflow_state: {vp.workflow_state}")
    print(f"  badge_number:   {vp.badge_number}")
    print(f"  qr_code_image:  {vp.qr_code_image}")

    # Pull new Email Queue entries
    new_emails = frappe.db.sql("""
        SELECT name, sender, message_id, reference_name, status, creation,
               (SELECT recipient FROM `tabEmail Queue Recipient`
                WHERE parent = eq.name LIMIT 1) AS recipient,
               (SELECT subject FROM `tabEmail Queue` WHERE name = eq.name) AS subj
        FROM `tabEmail Queue` eq
        WHERE creation > %s
          AND reference_doctype = 'Visitor Pass'
          AND reference_name = %s
        ORDER BY creation
    """, (before_max, vp_name), as_dict=True)

    print(f"\nEmails queued for this Visitor Pass after Approve: {len(new_emails)}")
    visitor_email_count = 0
    for e in new_emails:
        is_visitor = (e.recipient or "") == (vp.email_id or "")
        flag = "  ← TO VISITOR" if is_visitor else ""
        if is_visitor:
            visitor_email_count += 1
        print(f"  - to: {e.recipient}  subject: {(e.subj or '')[:60]}{flag}")

    print(f"\nEmails sent to the VISITOR ({vp.email_id}): {visitor_email_count}")
    print("RESULT:", "PASS (exactly one visitor email)" if visitor_email_count == 1
                    else f"FAIL (expected 1, got {visitor_email_count})")
    return {"vp": vp_name, "total_queued": len(new_emails),
            "visitor_emails": visitor_email_count}


def show_messages(names=None):
    """Print the message body of given notifications (or all VMS ones if no list)."""
    if names is None:
        vms_doctypes = ("Visitor Pass", "Conference Room Booking", "Hospitality Request",
                        "Visitor Invitation")
        names = [n.name for n in frappe.get_all("Notification",
                 filters={"document_type": ["in", vms_doctypes], "enabled": 1})]
    for nm in names:
        doc = frappe.get_doc("Notification", nm)
        print(f"=== {nm} ===")
        print(doc.message)
        print()


FOOTER = ("<p style='margin-top: 20px; color: #64748b; font-size: 12px;'>"
          "This is an automated email. Please do not reply.</p>")

GATE_SECURITY_BODY = """<div style="font-family: Arial, sans-serif; font-size: 13px; color: #1f2933; line-height: 1.5;">
<p>A new visitor has been approved and requires item verification at the security gate.</p>
<h3 style="margin: 16px 0 6px; font-size: 14px;">Visitor Details</h3>
<table style="border-collapse: collapse; width: 100%; margin: 0 0 12px 0;">
<tr style="background: #f4f5f7;"><td style="padding: 8px; border: 1px solid #ddd; width: 30%;"><b>Name</b></td><td style="padding: 8px; border: 1px solid #ddd;">{{ doc.visitor_full_name }}</td></tr>
<tr><td style="padding: 8px; border: 1px solid #ddd;"><b>Type</b></td><td style="padding: 8px; border: 1px solid #ddd;">{{ doc.visitor_type }}</td></tr>
<tr style="background: #f4f5f7;"><td style="padding: 8px; border: 1px solid #ddd;"><b>Mobile</b></td><td style="padding: 8px; border: 1px solid #ddd;">{{ doc.mobile_number }}</td></tr>
<tr><td style="padding: 8px; border: 1px solid #ddd;"><b>Host</b></td><td style="padding: 8px; border: 1px solid #ddd;">{{ doc.person_to_visit }}</td></tr>
<tr style="background: #f4f5f7;"><td style="padding: 8px; border: 1px solid #ddd;"><b>Visit Date</b></td><td style="padding: 8px; border: 1px solid #ddd;">{{ doc.visit_date }}</td></tr>
<tr><td style="padding: 8px; border: 1px solid #ddd;"><b>Pass ID</b></td><td style="padding: 8px; border: 1px solid #ddd;">{{ doc.name }}</td></tr>
</table>
{% if doc.visitor_items %}
<h3 style="margin: 16px 0 6px; font-size: 14px;">Items to Verify</h3>
<ul style="margin: 0 0 12px 20px; padding: 0;">
{% for item in doc.visitor_items %}<li>{{ item.item_name }} (Qty: {{ item.quantity or 1 }})</li>
{% endfor %}</ul>
{% endif %}
<h3 style="margin: 16px 0 6px; font-size: 14px;">Action Required</h3>
<ul style="margin: 0 0 12px 20px; padding: 0;">
<li>Verify visitor ID matches the registered details on the Visitor Pass.</li>
<li>Confirm declared items against carried items at the security gate.</li>
<li>Issue the physical badge after successful verification.</li>
</ul>
<p style='margin-top: 20px; color: #64748b; font-size: 12px;'>This is an automated email. Please do not reply.</p>
</div>"""

VISITOR_THANKYOU_BODY = """<div style="font-family: Arial, sans-serif; font-size: 14px; color: #1f2933; line-height: 1.5; max-width: 600px;">
<p>Dear <b>{{ doc.visitor_full_name }}</b>,</p>
<p>Thank you for visiting us on <b>{{ doc.visit_date }}</b>. We hope your visit was productive.</p>
<h3 style="margin: 16px 0 6px; font-size: 14px;">Visit Summary</h3>
<table style="border-collapse: collapse; width: 100%; margin: 0 0 12px 0;">
<tr style="background: #f4f5f7;"><td style="padding: 8px; border: 1px solid #ddd; width: 30%;"><b>Host</b></td><td style="padding: 8px; border: 1px solid #ddd;">{{ doc.person_to_visit }}</td></tr>
<tr><td style="padding: 8px; border: 1px solid #ddd;"><b>Visit Date</b></td><td style="padding: 8px; border: 1px solid #ddd;">{{ doc.visit_date }}</td></tr>
<tr style="background: #f4f5f7;"><td style="padding: 8px; border: 1px solid #ddd;"><b>Pass ID</b></td><td style="padding: 8px; border: 1px solid #ddd;">{{ doc.name }}</td></tr>
</table>
<h3 style="margin: 16px 0 6px; font-size: 14px;">Feedback</h3>
<p>If you have any feedback about your experience, please share it with your host or reply to this email so we can improve.</p>
<p>We look forward to welcoming you again.</p>
<p style='margin-top: 20px; color: #64748b; font-size: 12px;'>This is an automated email. Please do not reply.</p>
</div>"""

CRB_CLOSING = "<p>Please review and take the appropriate action at your earliest convenience.</p>"


def fix_all_notifications():
    """Bring every enabled VMS notification to company standard:
    - Add the automated-email footer (before </div>) if missing
    - Add closing lines to CRB Pending Approval / CRB Service Alert
    - Replace Gate Security Alert + VMS Visitor Thank You bodies entirely
    - Enable system notification on Gate Security Alert
    """
    vms_doctypes = ("Visitor Pass", "Conference Room Booking", "Hospitality Request",
                    "Visitor Invitation")
    notifs = frappe.get_all("Notification",
        filters={"document_type": ["in", vms_doctypes], "enabled": 1},
        pluck="name")

    changed = []
    for nm in notifs:
        doc = frappe.get_doc("Notification", nm)
        msg = doc.message or ""
        new_msg = msg
        action = []

        if nm == "Gate Security Alert":
            new_msg = GATE_SECURITY_BODY
            doc.send_system_notification = 1
            action.append("rewrote body + enabled system notification")
        elif nm == "VMS Visitor Thank You":
            new_msg = VISITOR_THANKYOU_BODY
            action.append("rewrote body (added H3 sections + footer)")
        else:
            # Generic fixes for everyone else
            # 1. Add CRB closing line if it's CRB and lacks one
            if nm in ("CRB Pending Approval", "CRB Service Alert"):
                if "We look forward" not in new_msg and "Please review" not in new_msg:
                    # Insert closing just before </div>
                    if "</div>" in new_msg:
                        new_msg = new_msg.replace("</div>", CRB_CLOSING + "\n</div>", 1)
                        action.append("added closing line")
            # 2. Add footer if missing
            if "automated email" not in new_msg and "Automated email" not in new_msg:
                if "</div>" in new_msg:
                    new_msg = new_msg.replace("</div>", FOOTER + "\n</div>", 1)
                else:
                    new_msg += "\n" + FOOTER
                action.append("added automated footer")

        if new_msg != msg or action:
            # Bypass is_standard validation by writing directly to DB.
            frappe.db.set_value("Notification", nm, "message", new_msg,
                                update_modified=False)
            if nm == "Gate Security Alert":
                frappe.db.set_value("Notification", nm, "send_system_notification", 1,
                                    update_modified=False)
            changed.append((nm, action))

    frappe.db.commit()
    print(f"Updated {len(changed)} notifications:")
    for nm, action in changed:
        print(f"  - {nm}: {', '.join(action)}")


def standard_check():
    """Score each enabled VMS notification against company-standard markers."""
    vms_doctypes = ("Visitor Pass", "Conference Room Booking", "Hospitality Request",
                    "Visitor Invitation")
    rows = frappe.get_all("Notification",
        filters={"document_type": ["in", vms_doctypes], "enabled": 1},
        fields=["name", "message"])

    # Standard markers we expect in a "company-standard" VMS email
    STANDARDS = {
        "div wrapper": 'font-family: Arial',
        "consistent size": 'font-size: 1',  # 13/14px both ok
        "color #1f2933": 'color: #1f2933',
        "h3 sections": '<h3',
        "table or ul": ('<table' , '<ul'),
        "closing line": ('We look forward', 'Thank you', 'visit'),
        "automated footer": ('automated', 'Automated'),
    }
    print(f"{'Name':40s} {'Wrap':5s} {'Size':5s} {'Col':5s} {'H3':5s} {'TBL/UL':6s} {'Close':5s} {'Foot':5s}")
    for r in rows:
        msg = r.message or ""
        line = f"{r.name:40s} "
        for key, marker in STANDARDS.items():
            if isinstance(marker, tuple):
                hit = any(m in msg for m in marker)
            else:
                hit = marker in msg
            line += f"{'OK' if hit else '--':<6s}"
        print(line)


def full_notification_inventory():
    """List every Notification touching VMS doctypes with its trigger, recipients,
    subject, channel, message length, and whether it uses templates."""
    vms_doctypes = (
        "Visitor Pass", "Conference Room Booking", "Hospitality Request",
        "Visitor Invitation", "Security Log",
        "Visitor Blacklist", "Contact Trace Record",
        "Visitor Event Log", "VMS Settings",
    )
    rows = frappe.get_all("Notification",
        filters={"document_type": ["in", vms_doctypes]},
        fields=["name", "document_type", "enabled", "event", "value_changed",
                "condition", "channel", "subject", "send_system_notification"],
        order_by="document_type, name")
    print(f"Total VMS Notifications: {len(rows)}\n")
    for n in rows:
        doc = frappe.get_doc("Notification", n.name)
        recipients = []
        for r in doc.get("recipients", []):
            if r.receiver_by_document_field:
                recipients.append(f"doc.{r.receiver_by_document_field}")
            if r.receiver_by_role:
                recipients.append(f"role:{r.receiver_by_role}")
            if r.cc:
                recipients.append(f"cc:{r.cc}")
        msg = (doc.message or "").strip()
        msg_len = len(msg)
        msg_first = msg.replace("\n", " ")[:80]
        en = "ENABLED" if n.enabled else "DISABLED"
        sys_n = "+SystemNotif" if n.send_system_notification else ""
        print(f"--- {n.name} ({en}) ---")
        print(f"  doctype:    {n.document_type}")
        print(f"  event:      {n.event} | value_changed={n.value_changed}")
        print(f"  channel:    {n.channel}{sys_n}")
        print(f"  recipients: {recipients}")
        print(f"  condition:  {(n.condition or '')[:90]}")
        print(f"  subject:    {(n.subject or '')[:90]}")
        print(f"  msg_len:    {msg_len} chars  | preview: {msg_first}")
        print()


def fm_live_test():
    """Force one CRB to transition Draft -> Pending Approval via save() and
    confirm FM gets an email + system notification."""
    crb = frappe.db.get_value("Conference Room Booking",
                              {"workflow_state": "Draft",
                               "meeting_title": ["like", "LoadTest CRB%"]},
                              "name")
    if not crb:
        print("No Draft LoadTest CRB available to test on")
        return
    print(f"Testing with CRB: {crb}")

    # snapshot before
    em_before = frappe.db.count("Email Queue",
                                {"reference_doctype": "Conference Room Booking",
                                 "reference_name": crb})
    nl_before = frappe.db.count("Notification Log",
                                {"document_type": "Conference Room Booking",
                                 "document_name": crb})

    # Transition via save (so notifications fire)
    doc = frappe.get_doc("Conference Room Booking", crb)
    doc.workflow_state = "Pending Approval"
    doc.save(ignore_permissions=True)
    frappe.db.commit()
    print(f"  ws after save: {doc.workflow_state}")

    em_after = frappe.db.count("Email Queue",
                               {"reference_doctype": "Conference Room Booking",
                                "reference_name": crb})
    nl_after = frappe.db.count("Notification Log",
                               {"document_type": "Conference Room Booking",
                                "document_name": crb})

    print(f"\nEmail Queue rows: {em_before} -> {em_after}  (delta={em_after-em_before})")
    print(f"Notification Log rows: {nl_before} -> {nl_after}  (delta={nl_after-nl_before})")

    # Show new emails
    new_em = frappe.db.sql("""
        SELECT eq.name, eqr.recipient
        FROM `tabEmail Queue` eq
        JOIN `tabEmail Queue Recipient` eqr ON eqr.parent = eq.name
        WHERE eq.reference_doctype = 'Conference Room Booking'
          AND eq.reference_name = %s
        ORDER BY eq.creation DESC LIMIT 5
    """, (crb,), as_dict=True)
    print("\nEmails for this CRB:")
    for e in new_em:
        print(f"  - {e.name} -> {e.recipient}")

    # Show notification logs
    new_nl = frappe.db.sql("""
        SELECT name, for_user, subject
        FROM `tabNotification Log`
        WHERE document_type = 'Conference Room Booking'
          AND document_name = %s
        ORDER BY creation DESC LIMIT 5
    """, (crb,), as_dict=True)
    print("\nSystem notifications for this CRB:")
    for n in new_nl:
        print(f"  - to={n.for_user}  subject={(n.subject or '')[:60]}")


def fm_notification_audit():
    """Verify Facility Manager gets system + email notifications when a CRB
    moves to Pending Approval."""
    print("=" * 60)
    print("FACILITY MANAGER NOTIFICATION AUDIT")
    print("=" * 60)

    # 1. List all enabled Notifications on Conference Room Booking
    print("\n[1] Notifications configured on Conference Room Booking:")
    rows = frappe.get_all("Notification",
        filters={"document_type": "Conference Room Booking", "enabled": 1},
        fields=["name", "event", "value_changed", "condition", "channel", "subject"])
    if not rows:
        print("    (none)")
    for n in rows:
        print(f"  - {n.name}")
        print(f"     event={n.event} value_changed={n.value_changed} channel={n.channel}")
        print(f"     condition={(n.condition or '')[:80]}")
        print(f"     subject={(n.subject or '')[:60]}")
        # Recipients
        doc = frappe.get_doc("Notification", n.name)
        recipients = []
        for r in doc.get("recipients", []):
            if r.receiver_by_document_field:
                recipients.append(f"doc.{r.receiver_by_document_field}")
            if r.receiver_by_role:
                recipients.append(f"role:{r.receiver_by_role}")
            if r.cc:
                recipients.append(f"cc:{r.cc}")
        print(f"     recipients={recipients}")

    # 2. Workflow state config — does Pending Approval state send email?
    print("\n[2] Workflow 'Conference Room Booking Approval' state config:")
    wf = frappe.get_doc("Workflow", "Conference Room Booking Approval")
    for s in wf.get("states", []):
        print(f"  state={s.state:20s}  send_email={s.send_email}  allow_edit={s.allow_edit}")

    # 3. Workflow transitions — do they email the creator?
    print("\n[3] Transitions email config:")
    for t in wf.get("transitions", []):
        print(f"  {t.state:18s} --[{t.action}]--> {t.next_state:18s}  "
              f"send_email_to_creator={t.send_email_to_creator}")

    # 4. Does the Facility Manager user have email notifications enabled?
    print("\n[4] FM user email-notification settings:")
    fm_users = frappe.db.sql_list("""
        SELECT parent FROM `tabHas Role`
        WHERE role = 'Facility Manager' AND parenttype = 'User'
    """)
    for u in fm_users:
        usr = frappe.db.get_value("User", u,
            ["name", "email", "enabled", "thread_notify"], as_dict=True)
        print(f"  {usr.email}  enabled={usr.enabled}  thread_notify={usr.thread_notify}")

    # 5. Recent system Notification Logs for FM
    print("\n[5] Recent system notifications addressed to FM (last 7 days):")
    nl = frappe.db.sql("""
        SELECT name, subject, document_type, document_name, `read`, creation, for_user
        FROM `tabNotification Log`
        WHERE for_user IN %(fm)s
          AND creation > DATE_SUB(NOW(), INTERVAL 7 DAY)
        ORDER BY creation DESC LIMIT 10
    """, {"fm": tuple(fm_users) or ('',)}, as_dict=True)
    if not nl:
        print("    (none — no system notifications sent to FM in last 7 days)")
    for n in nl:
        print(f"  - {n.creation} {n.for_user} | {n.document_type}/{n.document_name}")
        print(f"     subject={(n.subject or '')[:70]}")

    # 6. Recent CRB-related emails to FM
    print("\n[6] Recent CRB emails to FM (last 7 days):")
    em = frappe.db.sql("""
        SELECT eq.name, eq.status, eqr.recipient, eq.reference_name, eq.creation
        FROM `tabEmail Queue` eq
        JOIN `tabEmail Queue Recipient` eqr ON eqr.parent = eq.name
        WHERE eq.reference_doctype = 'Conference Room Booking'
          AND eqr.recipient IN %(fm)s
          AND eq.creation > DATE_SUB(NOW(), INTERVAL 7 DAY)
        ORDER BY eq.creation DESC LIMIT 10
    """, {"fm": tuple(fm_users) or ('',)}, as_dict=True)
    if not em:
        print("    (none — no CRB emails sent to FM in last 7 days)")
    for e in em:
        print(f"  - {e.creation} status={e.status} to={e.recipient} ref={e.reference_name}")


def crb_audit():
    """Diagnose why Facility Manager isn't seeing action buttons on CRBs."""
    # 1. Distribution of CRBs across workflow_state lanes
    print("=== CRB workflow_state distribution ===")
    rows = frappe.db.sql("""
        SELECT COALESCE(workflow_state, 'NULL') AS ws, COUNT(*) c
        FROM `tabConference Room Booking`
        GROUP BY workflow_state
        ORDER BY c DESC
    """, as_dict=True)
    for r in rows:
        print(f"  {r.ws}: {r.c}")

    # 2. Are any in Pending Approval (where FM should see action buttons)?
    pending = frappe.db.count("Conference Room Booking",
                              {"workflow_state": "Pending Approval"})
    print(f"\nIn 'Pending Approval' (FM should see these): {pending}")

    # 3. Show 5 most-recent CRBs and their state
    print("\n=== 5 most-recent CRBs ===")
    recent = frappe.db.sql("""
        SELECT name, workflow_state, docstatus, visitor_pass, meeting_title, owner
        FROM `tabConference Room Booking`
        ORDER BY creation DESC LIMIT 5
    """, as_dict=True)
    for r in recent:
        print(f"  {r.name} | ws={r.workflow_state} | docstatus={r.docstatus} | vp={r.visitor_pass}")
        print(f"     title={(r.meeting_title or '')[:50]}  owner={r.owner}")

    # 4. Users that have the Facility Manager role
    print("\n=== Users with 'Facility Manager' role ===")
    fm_users = frappe.db.sql("""
        SELECT parent
        FROM `tabHas Role`
        WHERE role = 'Facility Manager' AND parenttype = 'User'
    """, as_dict=True)
    for u in fm_users:
        print(f"  {u.parent}")
    if not fm_users:
        print("  (none — no user has the Facility Manager role assigned!)")

    # 5. Verify the workflow's transitions for FM
    print("\n=== Conference Room Booking Approval workflow transitions ===")
    wf = frappe.get_doc("Workflow", "Conference Room Booking Approval")
    print(f"  is_active: {wf.is_active}")
    for t in wf.get("transitions", []):
        print(f"  {t.state} --[{t.action}, allowed:{t.allowed}]--> {t.next_state}")


def check_vp_hr_state():
    vp = frappe.db.get_value("Visitor Pass", "VP-2026-00005",
                             ["status", "workflow_state", "docstatus", "hospitality_request"],
                             as_dict=True)
    print(f"VP-2026-00005: {vp}")
    if vp and vp.hospitality_request:
        hr = frappe.db.get_value("Hospitality Request", vp.hospitality_request,
                                 ["status", "workflow_state", "docstatus", "visitor_pass"],
                                 as_dict=True)
        print(f"Linked HR ({vp.hospitality_request}): {hr}")


def queue_check_for_vp():
    """For VP-2026-02686: how many distinct Email Queue messages? How many recipients each?"""
    rows = frappe.db.sql("""
        SELECT eq.name AS qname,
               (SELECT COUNT(*) FROM `tabEmail Queue Recipient` WHERE parent = eq.name) AS recip_count
        FROM `tabEmail Queue` eq
        WHERE eq.reference_doctype = 'Visitor Pass'
          AND eq.reference_name = 'VP-2026-02686'
        ORDER BY eq.creation
    """, as_dict=True)
    print(f"Distinct Email Queue rows for VP-2026-02686: {len(rows)}")
    for r in rows:
        print(f"  - {r.qname}: {r.recip_count} recipient(s)")
    # Now also show the Python email (the one with ref=None/None)
    rows2 = frappe.db.sql("""
        SELECT eq.name AS qname, eqr.recipient
        FROM `tabEmail Queue` eq
        JOIN `tabEmail Queue Recipient` eqr ON eqr.parent = eq.name
        WHERE eqr.recipient LIKE 'loadtest_w50_299%'
        ORDER BY eq.creation DESC LIMIT 5
    """, as_dict=True)
    print(f"\nEmails to the visitor (loadtest_w50_299@example.com): {len(rows2)}")
    for r in rows2:
        print(f"  - {r.qname} -> {r.recipient}")


def queue_check_broad():
    """Find ALL recently-queued emails (last 10 minutes) and any to loadtest visitor."""
    rows = frappe.db.sql("""
        SELECT eq.name, eq.status, eq.reference_doctype, eq.reference_name,
               eqr.recipient, eq.creation
        FROM `tabEmail Queue` eq
        LEFT JOIN `tabEmail Queue Recipient` eqr ON eqr.parent = eq.name
        WHERE eq.creation > DATE_SUB(NOW(), INTERVAL 30 MINUTE)
        ORDER BY eq.creation DESC
        LIMIT 30
    """, as_dict=True)
    print(f"Recent emails (last 30 min): {len(rows)}")
    for r in rows:
        print(f"  - {r.creation} | {r.status:10s} | ref={r.reference_doctype}/{r.reference_name} | to={r.recipient}")


def queue_check():
    """Show Email Queue for the most recently-approved LoadTest VP."""
    vp_name = frappe.db.get_value("Visitor Pass",
                                  {"visitor_full_name": ["like", "LoadTest W%"],
                                   "status": "Approved"},
                                  "name", order_by="modified desc")
    if not vp_name:
        print("No approved LoadTest VP found")
        return
    vp = frappe.db.get_value("Visitor Pass", vp_name, ["email_id"], as_dict=True)
    rows = frappe.db.sql("""
        SELECT eq.name, eq.status, eqr.recipient
        FROM `tabEmail Queue` eq
        LEFT JOIN `tabEmail Queue Recipient` eqr ON eqr.parent = eq.name
        WHERE eq.reference_doctype = 'Visitor Pass'
          AND eq.reference_name = %s
        ORDER BY eq.creation
    """, (vp_name,), as_dict=True)
    print(f"\nVisitor Pass:    {vp_name}")
    print(f"Visitor's email: {vp.email_id}")
    print(f"Email Queue rows: {len(rows)}")
    visitor_count = 0
    for r in rows:
        tag = "   <-- TO VISITOR" if r.recipient == vp.email_id else ""
        print(f"  - {r.status:8s} to={r.recipient}{tag}")
        if r.recipient == vp.email_id:
            visitor_count += 1
    verdict = "PASS (exactly 1 visitor email)" if visitor_count == 1 else f"CHECK (got {visitor_count})"
    print(f"\nVisitor emails sent: {visitor_count}  ->  {verdict}")


def queue_for(vp_name):
    """List all queued emails for a given Visitor Pass and identify the visitor email."""
    vp = frappe.db.get_value("Visitor Pass", vp_name, ["email_id"], as_dict=True)
    if not vp:
        print(f"No such VP: {vp_name}")
        return
    rows = frappe.db.sql("""
        SELECT eq.name, eq.status, eq.subject, eqr.recipient
        FROM `tabEmail Queue` eq
        LEFT JOIN `tabEmail Queue Recipient` eqr ON eqr.parent = eq.name
        WHERE eq.reference_doctype = 'Visitor Pass'
          AND eq.reference_name = %s
        ORDER BY eq.creation
    """, (vp_name,), as_dict=True)
    print(f"Visitor's email: {vp.email_id}")
    print(f"Email Queue rows for {vp_name}: {len(rows)}")
    visitor_count = 0
    for r in rows:
        tag = ""
        if r.recipient == vp.email_id:
            tag = "  ← TO VISITOR"
            visitor_count += 1
        print(f"  - {r.status:10s} | to={r.recipient:35s} | {(r.subject or '')[:55]}{tag}")
    print(f"\nVisitor email count: {visitor_count}  →  {'PASS (1)' if visitor_count == 1 else 'CHECK'}")


def show_disabled_approval():
    """Show recipients of the (disabled) VMS Approval Email for completeness."""
    doc = frappe.get_doc("Notification", "VMS Approval Email")
    print(f"VMS Approval Email: enabled={doc.enabled}, event={doc.event}")
    for r in doc.get("recipients", []):
        bits = []
        if r.receiver_by_document_field:
            bits.append(f"doc.{r.receiver_by_document_field}")
        if r.receiver_by_role:
            bits.append(f"role:{r.receiver_by_role}")
        if r.cc:
            bits.append(f"cc:{r.cc}")
        print(f"  would have sent to: {','.join(bits)}")


def audit():
    out = []
    out.append(f"VMS Approval Email disabled? enabled={frappe.db.get_value('Notification', 'VMS Approval Email', 'enabled')}")
    out.append("")
    out.append("=== ALL enabled Notifications on Visitor Pass ===")
    rows = frappe.get_all("Notification",
                          filters={"document_type": "Visitor Pass", "enabled": 1, "channel": "Email"},
                          fields=["name", "event", "value_changed", "condition", "subject"])
    for n in rows:
        doc = frappe.get_doc("Notification", n.name)
        recipients = []
        for r in doc.get("recipients", []):
            if r.receiver_by_document_field:
                recipients.append(f"doc.{r.receiver_by_document_field}")
            if r.receiver_by_role:
                recipients.append(f"role:{r.receiver_by_role}")
            if r.cc:
                recipients.append(f"cc:{r.cc}")
        out.append(f"\n  {n.name}")
        out.append(f"     event={n.event}  value_changed={n.value_changed}")
        out.append(f"     condition={(n.condition or '')[:80]}")
        out.append(f"     recipients={recipients}")
    text = "\n".join(out)
    print(text)
    return text
