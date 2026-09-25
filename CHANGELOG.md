# Changelog

All notable changes to Visitor Management are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entries are written for the people who run the app, not for the people who wrote it.

## [Unreleased]

_Changes on the `version-16` branch that are not yet in a tagged release._

### Added

- **A data-retention purge, switched off by default.** Until now, visitor names, mobile
  numbers, emails, photographs and government ID numbers stayed in the database forever.
  **VMS Settings → Data Retention & Purge** adds an opt-in switch and a retention period
  (pre-filled at 365 days, inert while the switch is off). With it on, a nightly job at 03:00
  anonymises the identifying fields — name, mobile, email, ID number, vehicle number, and the
  ID-scan and photo files — on visits that are already over and older than the period. It
  keeps the visit itself (dates, host, gate, badge, workflow history, item verification), so
  reporting and the gate's audit trail survive the purge. It only ever considers a pass that
  is Checked-Out, Rejected, Cancelled or flagged No-Show, and an invitation that is Submitted,
  Expired or Cancelled. It never touches the **Visitor Blacklist** at any age, because
  anonymising a blacklist entry would silently switch off blocking for that person. The job is
  batched and safe to re-run.

### Security

- **Guards and hosts can no longer read the whole HR record.** The app granted READ on
  Employee to eight roles (Security, Host Employee, HOD, CEO, Sales Manager, Facility and
  Hospitality Manager) so they could pick a person in a link field — and READ is the whole
  record: bank account, salary, PAN, date of birth, health details. Earlier versions granted
  EXPORT too. A bug in the permission helper also gave hosts and reception READ on Job
  Applicant (candidates' CVs), Supplier and Maintenance Visit where only "select" was meant.
  These roles now get SELECT only, and the few fields a pass copies from the picked record
  (host name, department and email; candidate or supplier name, phone and email) come from
  a narrow endpoint. Existing sites are narrowed once on migrate; roles HRMS or ERPNext grant
  themselves are not touched.
- **A draft pass's status can no longer be set by hand.** `status` is read-only on the form,
  but the API does not honour read-only, so the owner of a draft could mark it "Checked-In".
  The Active Visitors and Daily Visitor Log reports then showed a visitor on site who was
  never approved, and the draft appeared to Security. A draft's status is now always derived
  from its approval stage. (The gate itself was never fooled.)
- **Values in staff and visitor emails are escaped.** Driver names, item names, purposes and
  similar fields were pasted into HTML mails as typed; a planted link or image survived
  Frappe's sanitiser and reached every recipient of the daily hospitality digest. All seven
  mails the app builds now escape record values.
- **A visitor's upload can only be claimed by their own submission.** On a shared reception
  kiosk, two anonymous visitors were indistinguishable, so a file one had just uploaded could
  be claimed by the other's submission if its address leaked. The portal page now makes a
  random key, sends it with each upload and with the submission, and only matching files are
  accepted.
- **Invitation links are now case-sensitive.** The pre-registration token is a bearer
  credential — whoever holds the string can open that visitor's form — but it was stored in a
  case-insensitive database collation, so a token with its letters' case flipped opened the
  same invitation. Confirmed against a live site. The column is moved to a byte-comparison
  collation on migrate, which restores the entropy the token was minted with. Existing tokens
  keep working.

### Fixed

- **Installing the app no longer changes other apps.** Workflow states ("Draft", "Approved",
  "Rejected", "Cancelled", "Pending Approval"), workflow actions and roles ("HOD", "CEO",
  "Security") were shipped as fixtures, which Frappe re-imports on every migrate — so each
  update reset their colours, icons and role settings for every app on the site. They are now
  created only when missing. Guest uploads from other apps' public forms are no longer refused
  by this app's upload guard: a site that allowed guest uploads before this app keeps doing so,
  and a page can be allowed in **VMS Settings → Other Pages That May Accept Guest Uploads**.
- **Hand edits to the Visitor Pass workflow survive a migrate.** It used to be regenerated on
  every migrate and every Visitor Type save; it is now rewritten only when the approval routing
  itself changes.
- **Uninstalling leaves the site clean.** Frappe's uninstall left the Job Applicant interview
  fields, the three workflows, two notifications, this app's permission rows on HRMS/ERPNext
  DocTypes (which also froze those DocTypes' permissions), its roles, scheduled jobs — and
  visitors' ID scans and photos on disk. The app's uninstall hook now removes them, and leaves
  alone anything the site may own too. `uninstall-app --dry-run` no longer switches guest
  uploads off.
- **A multi-day pass can now be used on more than one day.** A contractor pass valid from,
  say, Monday to Friday was refused at the gate on Tuesday, because checking out on Monday
  retired the pass. Re-entry is now allowed for any day inside the pass's validity window;
  a single-day pass still retires on check-out, and nobody can check in before their visit
  date.
- **A deactivated gate can no longer be used.** The gate picker already hid inactive gates,
  but a stale form or a direct API call could still record an event against one. Checked when
  the log is created, so correcting an old record whose gate was retired since still works.
- **The hospitality sections appear reliably.** A Hospitality Request shows its Cab, Hotel,
  Factory Tour, Buggy and Greeting sections only when the matching flag is set on the visitor
  pass. Those flags were hidden fields, and a value landing on a hidden field does not reliably
  re-trigger the section that depends on it — so a request with a cab booked could open with no
  Cab section at all. The five flags are now visible and read-only, which both fixes the
  rendering and gives the hospitality manager a status readout of what was actually requested.
- **The blacklist no longer blocks namesakes.** A block used to fire on a name match alone,
  so an unrelated visitor with the same common name was refused entry. An ID number still
  blocks on its own; a name now has to be corroborated by a matching mobile number on the
  same blacklist row.
- **Fields that existed but could not be seen.** Several fields were shipped hidden and were
  therefore uneditable and invisible: the customer-visit fields on a pass (products discussed,
  meeting outcome, follow-up date, meeting minutes), the declared-items grid, and the
  hospitality fields on a Visitor Invitation (meal required, refreshments, conference room,
  and the meal type / slots derived from them). They now appear where they apply. The declared
  items grid is shown read-only, because it is rebuilt by the server from the items the
  visitor declared and what security verified at the gate.
- **ID numbers are stored in their normalised form.** Validation normalised the number to
  check it, then saved whatever was typed — so the same ID entered with different spacing
  was stored two different ways, and blacklist and duplicate matching missed it. Existing
  rows are not rewritten in bulk; a row is normalised the next time it is saved.
- **Alerts no longer leak an email-server error to the person at the gate.** When the site
  has no outgoing email account, a check-in or a blacklist warning popped a "setup Email
  Account" message at the officer even though the gate event itself had saved correctly.

## [2.0.0] - 2026-09-03

The v16 release. This is a **major** version because the platform requirement changed:
the app no longer installs on Frappe/ERPNext/HRMS v15 or Python 3.10, which 1.0.0 supported.

> Not yet tagged in git. The version in `pyproject.toml` is `2.0.0`; cutting the tag is a
> maintainer action.

### Added

- **Visitor Type is now a configurable master.** Which role approves a visitor, whether a
  second approver is needed, the badge prefix and colour, the default gate and the detail
  layout shown on the pass are all set per visitor type. The approval workflows are generated
  from those settings, so adding a visitor type no longer means editing code.
- **Nationality** on a visitor pass, for foreign-national handling. It is a required field on the pass.
- **Country-aware mobile number checking** on every way a number can arrive — desk, portal
  pre-registration and invitation. A rejected number is reported with a valid example for
  that country.
- **ID Proof Type master** with per-type rules: accepted aliases, how the number is
  normalised, and real validation (Aadhaar is checked with the Verhoeff checksum, not just
  counted to twelve).
- **Dashboard charts and cards**: Visitor Passes by Type and Pending Passes by Type, both of
  which now list visitor types that have zero passes instead of hiding them; a Checked-In
  Visitors number card.
- **Daily hospitality digest** emailed at 07:00 to the hospitality roles.
- **No-show and overstay detection** as scheduled jobs — passes that missed their window are
  flagged, and visitors the building still believes are inside are reported to security.
- **Workspace sidebars** for Visitor Management and Conference Rooms.
- **Security response headers** on the app's responses.
- Database indexes for the app's heaviest filter-and-sort paths. On a large site the Active
  Visitors report and the overstay job were doing full table scans.

### Changed

- **Platform requirement: Frappe 16, ERPNext 16, HRMS 16, Python 3.14, MariaDB 11.8+.**
  A v15 site can no longer install this app.
- Notification emails were rewritten: one line plus a link to the record, and colours that
  stay readable in a dark mail client.
- Visitor Event Log shows what happened in plain sentences instead of a raw JSON payload.
- The reports (Active Visitors, Daily Visitor Log, Gate Wise Count, Visitor Identity Match)
  were rebuilt to respect the reader's permissions and to require a bounded date range on the
  server, not only in the form.
- Setup — roles, gates, visitor types, ID proof types, settings and the generated workflows —
  moved into `setup.py` and is re-applied idempotently on every `bench migrate`. It used to
  run as patches, which `bench install-app` marks complete without running, so a fresh site
  silently got none of it.
- Tests use `IntegrationTestCase`; `FrappeTestCase` is deprecated upstream.

### Fixed

- **Fresh installs finish.** Workflow states, workflow actions and the roles a workflow seed
  refers to are now created before the workflow itself, so a clean install no longer ends up
  with a half-built approval flow.
- **Cancel buttons that could never work.** Visitor Pass and Hospitality Request both offered
  a Cancel action in the Actions menu that was refused every time it was clicked, because no
  role held the `cancel` permission. Every role that can approve can now also cancel.
- **Pass status disagreed with the workflow.** `Status` and the workflow state now move
  together, including on rejection, which previously left a pass stuck.
- **The 07:00 digest went to people who could not open anything in it.** The hospitality
  roles now hold read access to Hospitality Request.
- **Broken link pickers.** Choosing an existing Supplier, Maintenance Visit or Job Applicant
  on the matching pass layout threw "Insufficient Permission", so those layouts could not be
  linked to anything.
- **Declared items are verified one by one.** A free-text list of items is split into one row
  per item, and a gate event is refused while any declared item is still unticked.
- **People, not IDs.** The host appears as a name (and email where known) on passes, alerts
  and reports instead of an employee ID.
- Records created before newer fields existed are backfilled once — host name, host email,
  and the normalised mobile digits used for matching.
- Blacklist rows created before the "Is Active" flag existed are activated once, so old
  entries are enforced again.
- Duplicate emails: the Notification-engine copy of an alert the code already sends itself is
  switched off.

### Security

- **Self-approval closed.** An approver could approve a visitor pass they had raised
  themselves.
- **Approval bypass closed** on the visitor pass workflow.
- **Guest uploads fenced.** The visitor pre-registration form needs the site-wide
  `allow_guests_to_upload_files` setting (see the README's "Before you install"). With that
  setting on, Frappe's upload handler skips the write-permission check for anonymous callers
  entirely, and the target record is taken from form data — so any anonymous caller could
  attach a file to any record on the site. Confirmed against a real site: a file landed on an
  Employee record. Anonymous uploads are now restricted to the visitor pass ID-scan and photo
  fields, checked to be genuine JPG/PNG/PDF by their content rather than their name, capped
  at 5 MB, forced private, and rate-limited per IP.
- **`allow_guests_to_upload_files` is enabled once, at install, and never forced back on.**
  It previously turned itself back on at every migrate, silently overriding an administrator
  who had switched it off.
- **Visitor Type is read-only for ordinary staff.** Any employee could previously point a
  visitor type's approver role at a role they held and approve their own visitors.
- **Read no longer implies export** in the permissions the app grants itself on migrate.
  Granting a role read access used to also hand it the right to download the whole table,
  because Frappe defaults `export` to on; export is now a separate, explicit decision.
- **Setup no longer re-opens a permission an administrator has tightened.** Each grant is
  asserted once per site and then belongs to the Role Permission Manager. It previously came
  back on every deploy, with nothing in the log to explain why.

## [1.0.0] - 2026-05-16

Initial release: visitor lifecycle (invitation, approval, gate check-in and check-out),
gate security and identity verification, hospitality requests, conference room booking,
contact tracing, the immutable audit logs, and seven query reports. Built for Frappe 15.
