# Visitor Management

Modern visitor lifecycle for offices, factories, and campuses — from pre-visit invitation, through QR-based gate check-in and identity verification, all the way to hospitality, conference rooms, contact tracing, and audit reports. Built on Frappe & ERPNext.

ERPNext 15  ·  Frappe 15  ·  license MIT

---

## Main features

**Visitor Lifecycle (invitation → approval → gate → checkout):** One Visitor Pass record drives the entire journey. Walk-in passes from reception or portal pre-registration via Visitor Invitation. Visitor type drives the approval workflow lane automatically.

**Gate Security & Identity Verification:** QR scan at the gate auto-loads the pass. A live photo is captured at the gate, **Matches ID Proof** and **Matches Pass Photo** must be ticked, the system screens against the active **Visitor Blacklist** (by ID number or name+type), issues a badge once items are verified, and records the gate event as an immutable **Security Log** row that can't be edited after save.

**Hospitality Management:** When a Visitor Pass is approved with any hospitality flag (cab, hotel, factory tour, buggy, greeting, meal), the system auto-creates a **Hospitality Request**. Meal slots are computed against the visit window; cab pick-up/drop times auto-populate from the pass.

**Conference Room Booking:** If a room is selected on the pass, a **Conference Room Booking** is auto-created. Meeting time is clamped to the room's operating hours and seating capacity is validated.

**VIP Protocol:** Dedicated **Approved VIP Queue** quick-action at the gate, email notification on pass approval, **protocol notes** carried from the pass through to the gate, pre-allocated meeting room, two-step approval.

**Contact Tracing & Audit Trail:** Every gate event opens or closes a **Contact Trace Record** for the visitor (visited area, time in, time out, exposure risk). Every lifecycle event is logged in the immutable **Visitor Event Log**.

## How to Install

```bash
cd ~/frappe-bench
bench get-app https://github.com/Yuvaraj4-S/Visitor_Management_System
bench --site <your-site> install-app visitormanagement
bench --site <your-site> migrate
bench build
bench restart
```

**Upgrade later:**

```bash
cd ~/frappe-bench/apps/visitormanagement
git pull
cd ~/frappe-bench
bench --site <your-site> migrate
```

Migrations are idempotent — `bench migrate` is safe to run any number of times.

---

## Setup and Use

All system-wide configuration lives in a single doctype: **VMS Settings**. Open the desk Awesome Bar and type "VMS Settings".

### Configure VMS Settings

| Field | What to put |
|---|---|
| **Enable Badge** | ✅ to enable badge generation per visitor |
| **Badge Required For** | Comma-separated visitor types that get badges (e.g. `Contractor,Customer,Supplier`) |
| **Badge Prefix — Contractor / Candidate / Customer / Supplier / VIP** | Defaults: `CON / CAN / CUS / SUP / VIP` |
| **Require Item Declaration** | ✅ to block pass submission until at least one item row is added |
| **Auto-Cancel Pending Passes (Hours)** | After how many hours a `Draft` pass auto-cancels (`0` to disable) |
| **Default Gate** | Gate name to fall back to when no auto-assignment rule matches |

Click **Save**. The app is now configured.

### Configure Workflows

Three approval workflows (Visitor Pass, Hospitality Request, Conference Room Booking) are pre-loaded as fixtures and activated automatically on `bench migrate` — no setup needed.

---

## Pre-Visit Invitation — invite a visitor in advance

1. Open the desk Awesome Bar and type "Visitor Invitation".
2. Click **+ Add Visitor Invitation**.
3. Fill: **Visitor Name**, **Email**, **Visit Date**, **Purpose**, **Person to Visit**.
4. Click **Save**. The system emails the visitor a token-based portal link.
5. The visitor clicks the link, fills the pre-registration form (mobile, ID type, ID number, photo, ID proof scan).
6. A **Visitor Pass** is auto-created in Draft state and routed to its approval lane based on visitor type.
7. On **Approve** → pass becomes Approved + Submitted, QR code is generated, notification is sent, hospitality request + room booking auto-create if flags are set.

End-to-end takes ~1 minute for the host, ~3 minutes for the visitor.

## Walk-in Visitor — visitor is at reception

1. Open `/app/visitor-pass` and click **+ Add Visitor Pass**.
2. Fill: **Visitor Name**, **Mobile**, **ID Proof Type + Number**, **Visitor Type**, **Person to Visit**, **Visit Date**.
3. Tick hospitality flags if applicable (Meal Required, Cab Required, Hotel Required, etc.).
4. Click **Save**. Pass goes to Draft, then **Submit** to start the approval workflow.
5. On **Approve** → QR code generated, notification sent, hospitality + room booking auto-created.

End-to-end takes ~5 minutes including approval.

## Gate Check-In — visitor arrives

1. Visitor presents QR code at the gate.
2. Open `/app/security-log` and click **Scan QR Code**.
3. The pass auto-fills — visitor photo, ID details, purpose, host, hospitality flags, items declared.
4. Click **Capture Gate Photo** → live photo is uploaded and attached.
5. Tick **Matches ID Proof** and **Matches Pass Photo** (both are mandatory).
6. Tick each declared item in the **Items Verification** grid as items are checked.
7. Click **Save**. Visitor Pass status flips to **Checked-In**, badge is issued, a notification email is sent, a Contact Trace Record opens.

The Security Log is **locked after save** — gate events are immutable audit records.

The system **re-checks the blacklist at the gate** even after pass approval — blacklisting may have happened in between. A blacklisted visitor is refused entry with a security alert email.

## Gate Check-Out — visitor leaves

1. Visitor presents QR code at exit.
2. Create a new Security Log with **Event Type = Check-Out**.
3. Live photo + match confirmations (ID + pass photo) are mandatory — same standard as check-in.
4. Click **Save**. Visitor Pass flips to **Checked-Out**, the open Contact Trace Record closes, the badge is marked returned.

Visitors who get blacklisted while still on premises are **allowed to check out** (the goal is to keep them out, not trap them in).

## Hospitality flow

When a Visitor Pass is approved with any hospitality flag set, the system creates a Hospitality Request in `Pending Approval`.

1. Open `/app/hospitality-request`.
2. Review meal type, cab pickup/drop, hotel dates, factory tour, greeting type, etc.
3. Assign staff for execution (food dept, cab vendor, hotel partner).
4. Click **Approve** → request becomes Approved + Submitted, an email goes to the assignees.

## Conference Room flow

When a Visitor Pass is approved with a Conference Room selected, the system creates a Conference Room Booking in `Pending Approval`.

1. Open the **Conference Rooms** workspace — shows today's bookings, pending approvals, total rooms, and a **Bookings by Room** chart.
2. Open the pending booking and review the time window (clamped to room operating hours) and seating capacity.
3. Click **Approve** → booking becomes Approved + Submitted.

---

## Track every visit

Every visitor's journey is captured across three audit doctypes:

| Doctype | Captures |
|---|---|
| **Visitor Event Log** | Every workflow + lifecycle event — pass created, approved, gate scanned, checked in, checked out, etc. Immutable. |
| **Security Log** | Every gate event — Check-In, Check-Out, Gate Transfer, Alert, Badge Collected. Photo at gate, identity match, item verification, gate name. Locked after save. |
| **Contact Trace Record** | Per visitor area-by-area movement log — visited area, time in, time out, exposure risk, close contacts. Auto-closes on Check-Out. |

Click **Visitor Pass → Connections** on any pass to drill into all linked records.

## Identity verification at the gate

The Security Log enforces three identity checks for Check-In and Check-Out:

| Check | Required when |
|---|---|
| Live photo at gate | Check-In and Check-Out |
| Visitor matches the ID proof presented | Check-In and Check-Out |
| Visitor matches the photo on the approved pass | Check-In and Check-Out |

Skipping any blocks the save. The pass photo, ID proof scan, and live gate photo are shown side-by-side in the **Identity Comparison** section.

---

## Multi-Gate Routing

When a visitor arrives without a pre-assigned gate, the Security Log auto-assigns one based on visitor type:

| Visitor Type | Default Gate |
|---|---|
| VIP | VIP Entrance |
| Supplier | Loading Dock |
| Contractor | Back Gate |
| Candidate | Main Gate |
| Customer | Main Gate |

This can be overridden manually on each Security Log or globally via **VMS Settings → Default Gate**.

---

## Visitor Blacklist

| Field | What to put |
|---|---|
| **Visitor Name** | Person's name (used as fallback when ID number is unknown) |
| **ID Proof Type** | Aadhaar / PAN Card / Passport / Driving License |
| **ID Proof Number** | Primary lookup key (auto-named series `VB-YYYY-#####`) |
| **Reason** | Mandatory — why this person is blocked (audit/compliance requirement) |
| **Is Active** | ✅ to enforce the block. Uncheck to suspend the block without deleting the record. |

The blacklist is **enforced at three points**: when a Visitor Pass is submitted, when a Security Log Check-In is saved, and when a QR code is scanned at the gate.

A match by ID Proof Number blocks first; if no number is on the blacklist row, the system falls back to a case-insensitive match on Visitor Name + ID Proof Type.

---

## Reports

Seven query reports are pre-installed:

| Report | Source DocType | Use |
|---|---|---|
| **Active Visitors** | Visitor Pass | Visitors currently on premises |
| **Daily Visitor Log** | Visitor Pass | Day-by-day pass log with status |
| **Gate Wise Count** | Security Log | Check-in / check-out / inside / pending counts per gate |
| **Visitor Identity Match Report** | Visitor Pass | Audit of identity verification at the gate |
| **Daily Hospitality Schedule** | Hospitality Request | Today's cab / hotel / tour / greeting schedule |
| **Daily Booking Schedule** | Conference Room Booking | Today's room bookings |
| **Room Utilization** | Conference Room Booking | Per-room utilization summary |

---

## Limitations

- **Refunds / cancellations** of issued badges are not automated. A pass can be Cancelled via the workflow; badges are revoked on Check-Out only.
- **Recurring visitors** are tracked one Visitor Pass per visit. There is no cross-visit aggregation (e.g. "contractor X visited 12 times this year" requires a custom report on top of the Visitor Pass table).
- **NDA / data-retention purge** is not implemented. Visitor records remain until manually deleted.
- **Items-out reconciliation** (verifying the visitor leaves with the same items they brought in) is not enforced — only items-in is verified.
- **Currency / localisation** — the app is locale-neutral, but date formatting follows the site's Frappe locale.

---

## Dependencies

- Frappe v15
- ERPNext v15
- HRMS v15 (Employee doctype used for visitor host links)
- Python 3.10+
- MariaDB 10.6+ with InnoDB

---

## License

MIT — see `license.txt`.

---

Built with Frappe · by Finstein
