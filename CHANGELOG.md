# Changelog

All notable changes to Visitor Management are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Entries are written for the people who run the app, not for the people who wrote it.

## [1.1.0] - Unreleased

The Frappe 15 back-port of the work done on the `version-16` branch (released there as
2.0.0 and later). Runs on Frappe 15, ERPNext 15 and HRMS 15, with Python 3.10+ and
MariaDB 10.6+. Everything below is a change from 1.0.0. Where an entry mentions "the Frappe 16
builds", it is pointing out a difference from the 2.x line and does not describe anything a
1.0.0 site ever had.

> Not yet tagged in git. The version in `visitormanagement/__init__.py` is `1.1.0`; cutting
> the tag is a maintainer action.

### Upgrading from 1.0.0

The first migrate prints one line for each thing it changes. Keep that output.

- **Take a database backup before `bench migrate`.** Setup commits part of its work as it goes
  (index and column changes cannot share a transaction), so a migrate that fails halfway
  leaves some of it applied.
- **Your gate stays as strict as it was.** 1.0.0 demanded a QR scan, a gate photo and both
  identity confirmations on every Check-In and Check-Out, and ignored the three switches in
  VMS Settings that were meant to control them. 1.1.0 obeys the switches, so on a site
  upgraded from 1.0.0 the migrate turns all three on, once. Untick any your gate does not need
  in **VMS Settings → Gate & Security**; your choice is kept. A new installation starts with
  them off. One thing is stricter for everyone: the gate refuses a Check-In or Check-Out while
  a declared item is unverified, unless you tick **Allow Gate Events Without Item
  Verification**.
- **The public pre-registration form stays open.** A new switch, **Allow Pre-Registration
  Without an Invitation**, is off on a new installation. On an upgraded site it is switched on
  once, because in 1.0.0 the form was always open and you may have its address on a QR code at
  reception. Untick it to accept invited visitors only.
- **The public form now asks visitors to accept a privacy notice.** It is on by default, with a
  standard notice. Review the text, or switch the requirement off, in **VMS Settings → Privacy
  Notice & Consent**.
- **Only System Manager can export Visitor Passes.** 1.0.0 shipped that right for nine roles.
  The migrate removes it from the other eight, once. If you grant it back to a role in Role
  Permission Manager, that is kept.
- **Inactive blacklist entries are reported, not changed.** On 1.0.0 a new blacklist entry was
  created with **Is Active** unticked, so it blocked nobody until somebody ticked it. The
  migrate tells you how many entries are inactive and leaves every one of them alone, because
  unticking is also how a person is cleared. Open Visitor Blacklist, filter on Is Active = No,
  and tick the ones that should be in force.
- **Blacklist entries that hold only a name now warn instead of refusing** — see Fixed. Add
  the ID number or the mobile number to any that must refuse entry.
- **ID numbers disappear from screens, including from the past.** The masked form is filled in
  on existing passes, group members and blacklist entries, and the full numbers already
  written to those records' change history and to Frappe's copies of deleted ones are masked
  or removed. See Added.
- **Integrations that write an ID number** must send it in `id_proof_number_entry` when they
  update a record. `id_proof_number` still works when a record is created. See "Notes for
  integrators" in the README.
- **Hospitality Manager and Facility Manager no longer see every Visitor Pass**, only the ones
  their work is about, and **hosts can no longer open their visitors' ID scans**. See Security.
- **The Visitor Pass approval workflow is rebuilt once.** It is now generated from your Visitor
  Types (see Added). If you had edited the "Visitor Pass Approval" workflow by hand, make those
  edits again after the first migrate; from then on they are kept.
- **Make sure each approver role has at least two holders.** Nobody can approve a pass they
  created, sent for approval or host any more (see Security). Where one person is the only
  holder of an approver role, the passes they raise or host will wait until a second user is
  given the role; the pass says so. Passes already waiting for approval are held to the same
  rule.
- **Job Applicant interview fields are renamed** to the `custom_` prefix
  (`custom_interview_checkin_time`, `custom_interview_checkout_time`, `custom_interview_host`,
  `custom_interview_mode`, `custom_interview_visit_date`). The first migrate copies every
  applicant's values from the old fields once; the old columns stay in the database, hidden.
  Update any report, print format or script of your own that reads the old names.
- **Visitor Invitation → Purpose of Visit is now mandatory.** Existing invitations without one
  must have it filled in the next time they are saved.
- **Gates and ID types are now records.** The five gate names 1.0.0 offered are created as
  Visitor Gate records and its four ID types as ID Proof Type records, so existing passes and
  logs keep working. The free-text Gate List in VMS Settings is gone.
- **Legacy values are repaired once on migrate** so old records can still be saved: Visitor Item
  category "Document / Sample / Gift / Perishable / Weapon / Other" becomes "Other"; a Visitor
  Invitation's free-text Conference Room that matches no Conference Room is cleared (each one
  is printed in the migrate log); a duplicated invitation token is kept on the oldest
  invitation only (use **Send Invitation** on the others), because the token is now unique;
  invitations already past their expiry are marked Expired; and a Conference Room that was
  created with unusable opening hours is given 08:00–20:00.
- **Permissions on HRMS / ERPNext DocTypes are handed back, once.** 1.0.0 gave eight roles Read
  on Employee and put an Employee row on the core Page DocType. Adding a row copies the
  DocType's whole standard set into Custom DocPerm and freezes it, so later HRMS / ERPNext
  permission updates stopped applying. The first 1.1.0 migrate removes exactly those rows
  (never a role the owning app grants itself, never a row you added) and, where what remains
  equals the DocType's standard set, drops the copy so the DocType follows its own app again.
  A DocType you had customised further is left as it is, with a note in the migrate log. It
  runs once per site; permissions you grant later are yours. The same clean-up covers the rows
  the Frappe 16 builds added on Supplier, Job Applicant and Maintenance Visit.
- **The site's own logo comes back.** 1.0.0 set the navbar and login-page logo of the whole
  site to this app's logo. 1.1.0 does not, so the logo your site is configured with shows
  again.

### Added

- **ID numbers are stored in full and shown masked.** The blacklist and the duplicate checks
  still match on the whole number, but nobody reads it off a screen any more: forms, lists,
  reports, exports, emails, prints, the change history and the API all carry the masked form
  (`XXXX-XXXX-1234` for Aadhaar, the last characters for other types). A number is typed into
  an entry box and stored out of sight. Only a System Manager can view a full number, with
  **Show Full ID Number**, one record at a time, and each view is recorded on the record's
  timeline and in Frappe's Activity Log (which Frappe clears after 90 days unless you lengthen
  that in Log Settings). This covers Visitor Pass, the accompanying visitors on a group pass
  and Visitor Blacklist.
- **Visible Trailing Characters** on each ID Proof Type: how many characters of a number stay
  readable (0 to 6, default 4, never more than half the number).
- **A privacy notice and recorded consent on the public form.** The form shows your notice (or
  a standard one) and a box the visitor must tick. Each pass created through the form records
  that the visitor agreed, when, and to which version of the notice; the version goes up when
  you change the text. Those fields cannot be set from the desk or the API.
- **You choose whether the public form needs an invitation** (**Allow Pre-Registration Without
  an Invitation**). See "Upgrading from 1.0.0" for how it starts out.
- **Visitor Types now really decide who approves.** 1.0.0 had Visitor Types, but its workflow
  only knew five fixed approver roles, so a type pointed at any other role could never be sent
  for approval. The workflow is now generated from the Visitor Types: name any role as approver
  (and optionally a second one), save, and the approval lane, the approver's permissions and
  the approval email follow at once. Each type also says whether it gets a physical badge,
  whether the badge is issued at the gate, whether the MD/CEO must be notified first, which
  gate it is routed to and which detail section the pass shows.
- **Visitor Gate master.** Gates are records you can add, rename and deactivate, instead of a
  fixed list of five. A deactivated gate is not offered and cannot be used for a new gate event.
- **ID Proof Type master.** The four built-in ID types became records you can edit and add to:
  accepted aliases, how the number is tidied up before it is stored, and the validation rule
  (a pattern, or the Aadhaar checksum 1.0.0 already used). A "Foreign Passport" type is added
  for visitors of another nationality.
- **Group visits.** One pass can list the people accompanying the visitor. Each is screened
  against the blacklist, and each one's ID number is checked against its ID type.
- **Country-aware mobile number checking** on every way a number can arrive — desk, portal
  pre-registration and invitation. A rejected number is reported with a valid example for
  that country.
- **More of the policy is in VMS Settings instead of in code:** how far ahead a visit may be
  booked, the no-show grace period, the phone country code, the meal windows, which roles
  receive blacklist alerts and the hospitality digest, a cap on public pre-registrations, and
  the colour, logo, organisation name and footer note of the public pre-registration page.
  Changes to VMS Settings are now kept in its change history.
- **Overstay alerts.** A visitor still shown as inside after **Max Visit Duration** is reported
  to the security roles, once. Nobody is checked out automatically.
- **An approved pass can be cancelled.** Every role that can approve a pass can cancel it, and
  cancelling calls off the pass's hospitality request and room booking. A pass cannot be
  cancelled while its visitor is still inside.
- **The host is told when a pre-registration arrives.** A visitor's submission lands as a
  draft pass and the host gets an email asking them to review it.
- **Invitations expire by themselves.** An invitation past its expiry is marked Expired within
  the hour, whether or not anyone opens the link. Give an expired invitation a later expiry
  and it opens again where it left off.
- **Security response headers on the public pre-registration page**: other sites cannot frame
  it, and the invitation link is not passed on in the `Referer` to other sites.
- **Rate limits on the public pre-registration endpoints**, which had none: 20 calls an hour
  per visitor address, a cap on stored submissions that you can lower in VMS Settings, 30
  invitation look-ups and 60 meal-plan previews, and after 20 wrong invitation links from one
  address every link from it is answered as not valid for the rest of the hour. The limits
  apply to visitors, not to your signed-in staff working in the desk. Behind a proxy they
  follow the visitor's real address; see "Before you install" in the README for
  `trusted_proxy_ips`.
- **A getting-started block on the workspace**: six steps — review the settings, decide who
  approves each kind of visitor, a guided look at a Visitor Type, name your gates, invite your
  first visitor, see who is on site.
- Database indexes for the app's heaviest filter-and-sort paths, which were full table scans
  on a large site.
- **A data-retention purge, switched off by default.** Until now, visitor names, mobile
  numbers, emails, photographs and government ID numbers stayed in the database forever.
  **VMS Settings → Data Retention & Purge** adds an opt-in switch and a retention period
  (pre-filled at 365 days, inert while the switch is off). With it on, a nightly job at 03:00
  anonymises visits that are over and older than the period: on the pass, the name, contact
  details, company, ID number, vehicle, purpose, items, protocol notes, diet and hospitality
  notes, and the ID scan, visa copy, photos and QR image; and the same for the accompanying
  visitors, declared items, gate logs, hospitality request, room booking, contact-trace notes
  and invitation of that visit, their change history, and the alert emails sent about them.
  A draft a visitor started on the public form and nobody took further is treated the same way
  30 days after its visit date. The job keeps the visit itself (dates, host, gate, badge,
  workflow history, item verification) and the record of the visitor's consent, so reporting
  and the gate's audit trail survive. It only ever considers a pass that is Checked-Out,
  Rejected, Cancelled or flagged No-Show, and an invitation that is Submitted, Expired or
  Cancelled. It never touches the **Visitor Blacklist** at any age, because anonymising a
  blacklist entry would silently switch off blocking for that person. The job is batched and
  safe to re-run. The README's "Data & privacy" section has the full list.
- **Frappe's personal-data requests cover visitors.** A Personal Data Deletion or Download
  Request for an email address now includes that visitor's passes and invitations.
- **Abandoned uploads are cleaned up.** A photo or ID scan uploaded on a desk form that was
  never saved is deleted after a day.
- **A clean uninstall.** See Fixed.

### Changed

- **A gate event is recorded when the officer says so.** A new Security Log has one button —
  **Record Check-In**, **Record Check-Out** or **Record Event** — and nothing is saved until
  it is pressed. Attaching the gate photo no longer saves the log. Before recording, the
  officer is shown anything the site requires that is still missing.
- **The gate asks for what VMS Settings says, and the server enforces it.** The QR scan, the
  gate photo and the two identity confirmations are each required on Check-In and Check-Out
  when their switch is on, whatever the form or an API caller sends. A required gate photo
  must be one taken or attached on that log: the visitor's own pre-registration photo or ID
  scan pasted in is not accepted. See "Upgrading from 1.0.0" for how the switches start out.
- **Security officers record in their own name.** The officer on a log is the person who
  recorded it; only a System Manager can record on someone else's behalf.
- **A pass that cannot be used is refused before the checks, not after.** The **Check In**
  button, the QR scan and the Security Log all refuse a pass for another day, an unapproved or
  cancelled pass, or a blacklisted visitor at the first step.
- **The app no longer changes Frappe, ERPNext, HRMS or any other installed app.**
  - It grants nothing on DocTypes it does not own. Its forms pick an Employee, Supplier, Job
    Applicant or Maintenance Visit through the app's own search, which shows a name and a label
    only, answers only users who may edit the app record the field is on, honours their User
    Permissions (unless the app's field ignores them), and lists Job Applicants only to HR,
    reception (Front Office Executive), System Manager and the Candidate approver role. Picking
    a supplier or candidate no longer copies their phone and email into the pass — reception
    types the visitor's own.
  - It no longer sets the site's navbar and login logo (`app_logo_url`); its `/apps` tile keeps
    its logo. It ships no site-wide desk script or stylesheet either (the Frappe 16 builds do).
  - It no longer adds an "Interview Mode" column to HRMS's Job Applicant list.
  - It ships no site-wide tour. Frappe 15 loads every "UI tour" on a site for every desk user
    on every page; the app's two guided tours are ordinary form tours, started only from its
    own getting-started block. A workspace tour ("VMS Setup Tour") that earlier builds of this
    version installed is removed on migrate, with the progress users had recorded for it.
  - It no longer creates Employee records for users holding the Security, Facility Manager or
    Hospitality Manager role, and no longer deletes those users' Employee User Permissions, as
    a 1.0.0 upgrade step did.
  - Its roles, workflow states and workflow actions are created only when missing (see Fixed).
  - Its data-retention purge, gate-photo repair and Portal Logo publishing only touch File rows
    attached to the app's own records; uninstall keeps a role while any other app's
    permissions, reports, pages, workspaces, workflows or notifications still use it; the portal
    security headers apply to the portal route only.
  - What it adds outside its own records is narrow and listed in the README ("Before you
    install"): the Job Applicant fields, two guards on files that only ever refuse this app's
    own files — one of them a check that runs when a private file is requested — and a
    one-time masking of ID numbers in the change history and deleted copies of this app's own
    records.
  - An administrator who wants VMS roles to filter list views by those fields, or to open the
    linked records, grants that in Role Permission Manager — see "Before you install" in the
    README.
- **Reports name people, and count visits once.** Active Visitors and Daily Visitor Log show
  the host by name instead of an Employee ID, and Daily Visitor Log has one row per pass —
  first check-in, last check-out and the number of entries — where a visitor who stepped out
  and came back used to appear several times. Gate Wise Count lists every active gate, including
  one with no traffic that day. Each report shows a reader only the passes they are allowed to
  see, and applies a bounded date range on the server, not only in the form.
- **Security Logs and invitations show names.** The officer and the host on a Security Log,
  and the host on an invitation, appear by name instead of Employee ID. The itinerary print
  names staff the same way.
- **Nationality and Visa Copy are part of the Visitor Pass itself** rather than Custom Fields
  added to it. Existing values carry over.
- The group members grid shows each person's masked ID number in place of the "Checked In"
  column.
- Notification emails were rewritten: one line plus a link to the record, and colours that
  stay readable in a dark mail client.
- Visitor Event Log shows what happened in plain sentences instead of a raw JSON payload.
- The Visitor Passes by Type and Pending Passes by Type charts list every visitor type,
  including those with no passes. The Checked-In Visitors card counts only visitors the gate
  actually recorded coming in. Security Events by Gate is a bar chart instead of a donut. The
  workspace now carries 15 number cards and seven charts.
- **The numbers in VMS Settings that set a limit must be at least 1** — maximum visit
  duration, how far ahead a visit may be booked, invitation expiry, no-show grace and the
  retention period. A 0 is refused, with the field's own name in the message, instead of
  quietly switching the rule off. A 0 that is stored anyway (by an import, or an earlier
  build) is read as the default rather than as "no limit": a site that had **Max Advance
  Booking (days)** at 0 now has a 90-day ceiling until it enters a number. The same goes for
  invitation expiry (7 days), no-show grace (4 hours) and the cap on public pre-registrations
  (20 an hour).
- On the public form, choosing a nationality moves the phone field's country code with it.
- Setup — roles, gates, visitor types, ID proof types, settings and the generated workflows —
  moved into `setup.py` and is re-applied idempotently on every `bench migrate`. It used to
  run as patches, which `bench install-app` marks complete without running, so a fresh site
  silently got none of it.

### Fixed

- **Fresh installs finish.** Workflow states, workflow actions and the roles a workflow
  refers to are now created before the workflow itself, so a clean install no longer ends up
  with a half-built approval flow.
- **A Cancel button that could never work.** Hospitality Request offered a Cancel action that
  was refused every time it was clicked, because no role held the `cancel` permission.
  Hospitality Manager and System Manager now do.
- **Pass status disagreed with the workflow.** `Status` and the workflow state now move
  together, including on rejection, which previously left a rejected pass reading "Draft" in
  every list and report.
- **Check-out sends the thank-you email.** The gate moved the pass to Checked-In and
  Checked-Out in a way that skipped every notification watching the pass's status, so the
  visitor's thank-you mail had never gone out on a real visit.
- **The Blacklist Action setting works, everywhere at the gate.** The gate always blocked,
  whatever the setting said. Block Entry, Alert Only and Log Only now do what they say on the
  QR scan, on the Check In button and when the Check-In is recorded.
- **A blacklist entry is accepted when it can do something, and says what.** The form used to
  insist on "an ID number, or a name and an ID type" — which accepted entries that could never
  refuse anyone and rejected a name with a mobile number, which can. An entry now needs an ID
  number, a name or a mobile number, and one that can only warn says so when it is saved.
- **"QR Code Scanned" belongs to the pass that was scanned.** If the officer scans one pass and
  then picks a different one by hand, the tick is cleared.
- **Two visitors can no longer be issued the same badge number** when their passes are
  approved at the same moment. Badge numbers are also counted against the visit date, not the
  day of approval.
- **Two officers cannot check the same visitor in twice** by recording at two gates in the
  same second.
- **Removing an item's row no longer skips its verification.** Declared items missing from a
  Check-In are put back, unverified.
- **The 07:00 digest went to people with no access to Hospitality Request at all.** Transport
  Coordinator, Factory Tour Coordinator, Greeting Staff and Hospitality User now hold read and
  report permission, so the Daily Hospitality Schedule opens for them. Each still sees only the
  requests they raised, are assigned to or host; a Hospitality Manager sees all of them.
- **Broken link pickers.** Choosing an existing Supplier, Maintenance Visit or Job Applicant
  on the matching pass layout threw "Insufficient Permission", so those layouts could not be
  linked to anything.
- **Declared items are verified one by one.** A free-text list of items is split into one row
  per item, and a gate event is refused while any declared item is still unticked (see
  "Upgrading from 1.0.0").
- **People, not IDs.** The host appears as a name (and email where known) on passes, alerts
  and reports instead of an employee ID.
- Records created before newer fields existed are backfilled once — host name, host email,
  officer name, and the normalised mobile digits used for matching.
- Duplicate emails: the Notification copy of the approval mail the code already sends itself
  is switched off once. If you switch it back on, it stays on.
- **Installing the app no longer changes other apps.** Workflow states ("Draft", "Approved",
  "Rejected", "Cancelled", "Pending Approval"), workflow actions and roles were shipped as
  fixtures, which Frappe re-imports on every migrate — so each update reset their colours,
  icons and role settings for every app on the site. They are now created only when missing.
- **Hand edits to the workflows survive a migrate.** All three approval workflows were
  re-imported from fixtures on every migrate. Visitor Pass Approval is now rewritten only when
  the approval routing itself changes; the other two are created once and then left alone.
- **Uninstalling leaves the site clean.** Frappe's uninstall left the Job Applicant interview
  fields, the three workflows, the app's notifications, its permission rows on HRMS/ERPNext
  DocTypes (which also froze those DocTypes' permissions), its roles, scheduled jobs — and
  visitors' ID scans and photos on disk. The app's uninstall hook now removes them, and leaves
  alone anything the site may own too. `uninstall-app --dry-run` changes nothing.
- **A visitor can step out and come back.** Checking out used to retire the pass, so a visitor
  back from lunch — or a contractor returning on day two of a five-day pass — was refused and
  needed a new pass. Re-entry is now allowed on the visit date, and on any day inside a
  multi-day pass's validity window. Nobody can check in before their visit date or after the
  pass has run out.
- **The hospitality sections appear reliably.** A Hospitality Request shows its Cab, Hotel,
  Factory Tour, Buggy and Greeting sections only when the matching flag is set on the visitor
  pass. Those flags were hidden fields, and a value landing on a hidden field does not reliably
  re-trigger the section that depends on it — so a request with a cab booked could open with no
  Cab section at all. The five flags are now visible and read-only, which both fixes the
  rendering and gives the hospitality manager a status readout of what was actually requested.
- **The blacklist no longer blocks namesakes.** Without an ID number match, a block used to
  fire on the name plus the *type* of ID shown — so an unrelated visitor with the same common
  name and the same kind of ID card was refused entry. An ID number still blocks on its own; a
  name now has to be backed by a matching mobile number on the same blacklist entry. A match on
  the name alone, or the mobile number alone, shows a warning asking the desk or the gate to
  verify the ID instead of blocking. An entry that holds only a name and an ID type therefore
  warns but no longer blocks: add the mobile number or the ID number to it if it must block.
- **Blacklisted visitors are caught earlier, and a disguised ID number still matches.**
  Reception is warned as soon as a draft pass matches, instead of after the documents have been
  collected, and the pass is stopped when it is sent for approval rather than when it is
  approved. ID numbers are compared ignoring case, spaces, hyphens and slashes.
- **Fields that existed but could not be seen.** Several fields were shipped hidden and were
  therefore uneditable and invisible: the customer-visit fields on a pass (products discussed,
  meeting outcome, follow-up date, meeting minutes), the declared-items grid, and the
  hospitality fields on a Visitor Invitation (meal required, refreshments, conference room,
  and the meal type / slots derived from them). They now appear where they apply. The declared
  items grid is shown read-only, because it is rebuilt by the server from the items the
  visitor declared and what security verified at the gate.
- **ID numbers are stored in their normalised form.** Validation normalised the number to
  check it, then saved whatever was typed — so the same ID entered with different spacing
  was stored two different ways, and blacklist and duplicate matching missed it. A number is
  checked and normalised when it is entered or its type changes; an old pass whose number no
  longer fits today's rule can still be edited.
- **A missing email account no longer gets in the way.** When the site has no outgoing email
  account, approving a pass or checking a visitor in popped a "setup Email Account" error at
  the user even though the record itself had saved, and could hide a blacklist warning shown
  in the same step. Mail problems are now logged for the administrator instead.
- **A room that is already taken no longer holds up the pass.** The pass is sent for approval
  and approved as usual, and says "Room Not Reserved" so that another room can be picked.
- **A Rejected booking no longer blocks its room** or shows as "Busy" on the calendar.
- **Approvers without the Employee role can work on a pass without an error.** An approver
  whose login carries only the approver role — a HOD or CEO with no Employee record, say — got
  a red "No permission for VMS Settings" on every Visitor Pass, and the badge fields stayed
  hidden. The form now gets the few settings it needs through a method open to anyone who can
  read passes; VMS Settings itself stays closed to them. Where such a user cannot pick a
  Visitor Type, ID Proof Type or Conference Room, a line under the field says why.
- **The "Visitors by Type" and "Pending Approvals by Type" charts load for every role that
  reads passes.** Staff who hold a Visitor Management role without the Employee role — a
  Security-only guard, for instance — got "No permission for Visitor Type" on the workspace
  instead of the charts. The counts are still limited to the passes the viewer may read.
- **A Conference Room created through the API or an import can be booked.** It used to get
  the moment of its creation as both opening and closing time, and refused every booking. It
  now gets 08:00–20:00 when no hours are given.

### Security

An independent security re-test of this version, on a development site, confirmed the findings
of the first test fixed and found no critical, high or medium issue. A live test of the public
form's rate limits followed; it found one low-severity point, fixed below. What was covered,
what was not, and the low-risk points left documented are in `SECURITY.md`.

- **Full ID numbers no longer reach a browser.** See Added. On 1.0.0 everyone who could open a
  pass read the visitor's full ID number, and it was in every export, in the change history
  and in the blacklist alert email. Old change-history rows and Frappe's copies of deleted
  passes and blacklist entries are cleaned on upgrade; a record deleted from now on is kept
  without its number.
- **ID scans and visa copies open only for the people who need them:** Security, System
  Manager, and the approver roles of that pass's Visitor Type. A host, or anyone else who can
  read a pass, could previously open the visitor's passport or Aadhaar scan. The rule holds
  however the file is requested, and the scan and visa copy are left out of prints and PDFs
  of the pass.
- **Hospitality Manager and Facility Manager no longer read every visitor's record.** They
  could open any Visitor Pass. They now open only passes that ask for hospitality or a room,
  or have a request or booking — read-only, with the ID number masked and the scans closed.
- **A Hospitality Request or room booking cannot be used to read someone else's pass.** Any
  employee could link a request to any pass and have the visitor's name, mobile number and
  visit times copied onto it. Linking now requires being able to open the pass.
- **Only System Manager can export Visitor Passes.** See "Upgrading from 1.0.0".
- **The public page no longer lists your employees.** The form's host field carried every
  Employee ID in the page source, and the submission answered "must be a valid Employee" to
  anyone who tried a name. A visitor without an invitation now types whom they have come to
  see; an unmatched name is saved for reception to complete, and the answer is the same
  either way. The form no longer tells an anonymous caller whether an ID number is blacklisted
  or already has a pass that day, and cannot be used to test whether a supplier, job applicant
  or meeting room exists.
- **A file that only pretends to be a picture is refused cleanly.** An upload must open as a
  real JPG, PNG or PDF, be at most 5 MB, and a picture at most 50 megapixels. A crafted file
  could previously cause a server error on the public form.
- **The public form's limits hold whichever way a request is addressed.** The anonymous
  meal-plan preview is limited to 60 requests an hour per visitor address on every API route
  (`/api/method` and `/api/v2/method`); in earlier builds of this version each route had its
  own budget. Each of the three endpoints that work without a login answers under one method
  name only.
- **The public form cannot be used to flood a host's inbox.** A host named on registrations
  made without an invitation is mailed about at most five an hour; the passes are still
  created for reception.
- **Approval is always by somebody else.** On 1.0.0 an approver could approve a visitor pass
  they had raised themselves. Closing that for the pass's creator alone was not enough: a host
  who also held the approver role could invite a visitor, send the visitor's pre-registration
  for approval and approve it alone, because that pass was created by the visitor. Now nobody
  can approve a pass they **created**, **sent for approval** or **host** — at either step of a
  two-step approval, and whether the approval comes from the Actions menu, bulk approve in the
  list or the API. The pass records who sent it for approval (**Sent for Approval By**). For
  those three people Approve is not offered and a banner says why; they can still Reject or
  Cancel. While a pass waits for approval its host cannot change **Person to Visit**. The
  Administrator account is exempt, as in Frappe's own rule. One limit to know: a single
  uninvolved person who holds both roles of a two-step lane can still take both steps.
- **Approval bypass closed** on the visitor pass workflow. Any employee could submit a draft
  pass directly and have it land on Approved without an approver ever seeing it. Employees no
  longer hold `submit` on Visitor Pass, and a pass that did not come through its approval lane
  is refused both on submit and at the gate.
- **The identity on an approved pass is fixed.** Its ID number cannot be changed; cancel and
  amend the pass instead.
- **Invitations, hospitality requests and room bookings are no longer open to all staff.** On
  1.0.0 every employee could read and edit every other host's invitation — including the
  pre-registration link in it — every hospitality request (dietary and accessibility needs,
  hotel costs, drivers' phone numbers) and every room booking. Each person now sees their own,
  those they host and those assigned to them; approvers see the invitations for their visitor
  types, read-only; Hospitality Manager, Facility Manager and System Manager see everything in
  their area. The room calendar and the Daily Booking Schedule still show every slot so nobody
  double-books, but show "Busy" in place of someone else's meeting title.
- **A gate officer learns nothing about a pass they may not read.** The gate endpoints give
  one answer for a pass that does not exist, is still a draft, is awaiting approval or was
  rejected, and a Security Log cannot be recorded against such a pass.
- **A recorded Security Log cannot be re-pointed.** Only a System Manager can correct one, and
  never the pass or the kind of event it records.
- **Guest file uploads stay off.** As in 1.0.0, the visitor pre-registration form reads the ID
  scan, photo and visa copy in the visitor's browser and sends them inside the submission, and
  the server stores each one as a private file on the new pass. The app never switches on the
  site-wide `allow_guests_to_upload_files` setting, at install or migrate (the Frappe 16 builds
  did). New in 1.1.0: if a site has that setting on for another app, anonymous uploads aimed
  at this app's records are refused (with that setting on, Frappe's upload handler skips the
  write-permission check for anonymous callers and takes the target record from form data).
- **A visitor can no longer write staff instructions onto their own pass.** The public form's
  endpoint accepted the VIP category, the protocol notes read by the gate ("escort not
  required…") and the interview panel from the visitor. Those are now set by staff only.
- **An invitation link is single-use even under a race.** Two submissions of the same link
  arriving together could both create a pass.
- **Self-approval is switched off on upgraded sites too.** The Conference Room Booking and
  Hospitality Request approval workflows ship with self-approval off on Approve, but an
  existing site kept the 1.0.0 setting (on). The first migrate switches it off once; an
  administrator can turn it back on in the workflow and it stays that way.
- **Visitor Type is read-only for ordinary staff.** Any employee could previously point a
  visitor type's approver role at a role they held and approve their own visitors. Only a
  System Manager can change who approves a visitor type.
- **Read no longer implies export** in the permissions the app grants itself on migrate.
  Granting a role read access used to also hand it the right to download the whole table,
  because Frappe defaults `export` to on; export is now a separate, explicit decision. Each
  such grant is made once per site and then belongs to the Role Permission Manager: setup does
  not re-open a permission an administrator has tightened.
- **Guards and hosts can no longer read the whole HR record.** 1.0.0 granted READ — and, by
  Frappe's default, EXPORT — on Employee to eight roles (Security, Host Employee, HOD, CEO, HR
  Manager, Sales Manager, Facility Manager and Hospitality Manager) so they could pick a person
  in a link field, and READ is the whole record: bank account, salary, PAN, date of birth,
  health details. These roles now get no permission there at all (see Changed): the app's own
  picker shows a name and a label, and the browser receives only the host's name and department
  or the candidate's / supplier's name. Existing sites have those rows removed once on migrate;
  roles HRMS or ERPNext grant themselves are not touched. The picker decides for itself
  whether a field may ignore User Permissions; it does not take the browser's word for it.
- **Security can no longer delete a Security Log**, on a fresh install and on any upgraded site
  that had not customised that DocType's permissions.
- **A draft pass's status can no longer be set by hand.** `status` is read-only on the form,
  but the API does not honour read-only, so the owner of a draft could mark it "Checked-In".
  The Active Visitors and Daily Visitor Log reports then showed a visitor on site who was
  never approved, and the draft appeared to Security. A draft's status is now always derived
  from its approval stage.
- **Values in staff and visitor emails are escaped.** Driver names, item names, purposes and
  similar fields were pasted into HTML mails as typed, so a planted link or image reached
  every recipient. The mails the app builds now escape record values, and markup is stripped
  from the free text a visitor can enter. On an approved pass the text that can still change
  is kept clean too: Current Location and the meal slots are stored as plain text, and
  Hospitality Notes keeps its formatting but no script.
- **A guest submission cannot name a stored file.** The portal accepts a file only as content
  inside the submission; a file address is accepted only when it is the one already on the
  visitor's own saved draft, so one visitor cannot claim another's ID scan.
- **Invitation links are now case-sensitive, and always `https` where the site is.** The
  pre-registration token is a bearer credential — whoever holds the string can open that
  visitor's form — but it was stored in a case-insensitive database collation, so a token with
  its letters' case flipped opened the same invitation. The column is moved to a
  byte-comparison collation on migrate; existing tokens keep working. The emailed link now
  follows the site's configured address (`host_name`) and the scheme its proxy reports, so it
  no longer comes out as `http://` on a site served over TLS by a proxy on another host.
- **The QR code and the gate photo are private files.** They are attached to the pass and
  readable by people who can read the pass, not by anyone holding the address.

## [1.0.0] - 2026-05-16

Initial release: visitor lifecycle (invitation, approval, gate check-in and check-out),
gate security and identity verification, hospitality requests, conference room booking,
contact tracing, the immutable audit logs, and seven query reports. Built for Frappe 15.
