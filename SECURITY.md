# Security Policy

> **Publisher: entries marked `[PUBLISHER TO FILL]` are placeholders.** Fill them in before
> the app is listed, and do not publish a response time that nobody is staffed to keep.

## Supported versions

| App version | Runs on | Security fixes |
|---|---|---|
| 1.1.x | Frappe 15, ERPNext 15, Frappe HR 15 | Yes |
| 1.0.x | Frappe 15, ERPNext 15, Frappe HR 15 | No — upgrade to 1.1.x. `CHANGELOG.md` lists the security fixes 1.1.0 carries and what to check after upgrading. |
| 2.x | Frappe 16 | `[PUBLISHER TO FILL: Yes / from which 2.x release]` |

## Reporting a vulnerability

Email **yuvaraj.s@finstein.ai** — the contact currently set as `app_email` in the app.
`[PUBLISHER TO FILL: a security mailbox that does not depend on one person]`

Please do **not** open a public GitHub issue for a security problem.

Include:

- the app version (`bench version`, or the Apps page of your site) and the Frappe version;
- what an attacker can do, and what access they need to do it (anonymous, or a logged-in user
  with which role);
- the steps to reproduce it;
- **no real visitor data** — use invented names, ID numbers and photographs.

## What to expect

- An acknowledgement within `[PUBLISHER TO FILL: e.g. 2 business days]`.
- An assessment and, if the report is confirmed, a target date within
  `[PUBLISHER TO FILL: e.g. 7 business days]`.
- A fix released as a patch version and recorded under "Security" in `CHANGELOG.md`. Reporters
  who want credit are credited.
- Please allow `[PUBLISHER TO FILL: e.g. 90 days]`, or until a fix is released if that is
  sooner, before disclosing publicly.

## Scope notes for researchers

What the app is designed to do, so that a report can say how it departs from this.

**The public form**

- **Three endpoints work without a login**, all for the visitor pre-registration form: the
  invitation look-up (30 requests an hour per address, and after 20 wrong tokens in an hour
  every token from that address is answered as not valid), the meal-plan preview (60 an hour
  for callers who are not signed in, on every API route) and the form submission (20 calls,
  and a cap on stored submissions). Each answers under one method name only. Whether the form accepts a registration without an invitation is a site
  setting; such a registration is stored as a draft pass that staff must review.
- **The invitation token in the link is a bearer credential by design.** Whoever holds the
  link can open and submit that visitor's form until it is submitted, cancelled or expired.
- The form is meant to tell an anonymous caller nothing about other records: not whether an
  employee, supplier or meeting room exists, and not whether an ID number is blacklisted or
  already has a pass. A way to learn any of those from it is a vulnerability.
- Uploads must be genuine JPG, PNG or PDF files of at most 5 MB (pictures at most 50
  megapixels). A file that gets a server error out of the form, or is stored without passing
  those checks, is a vulnerability. A JPEG that was cut short in transfer is accepted on
  purpose: Frappe itself is set to tolerate those.

**ID numbers and ID documents**

- **Government ID numbers are stored in full and unencrypted, by design** — matching needs
  them — and this is documented in the README ("Data & privacy"). What the app promises is
  that the full number never reaches a browser: every read returns the masked form, for every
  role. Only a System Manager can read one back, through one audited action, and each read is
  written to the record's timeline and to Frappe's Activity Log. **Any other way to obtain a
  full number through the application — a field in a response, a list filter, a report, an
  export, the change history, an email, a print — is a vulnerability.** So is a reveal that
  leaves no record.
- **ID scans and visa copies** may be opened only by Security, System Manager and the approver
  roles of that pass's Visitor Type, however the file is requested. Opening one as a host, a
  Hospitality or Facility Manager, or any other role is a vulnerability.
- Other uploaded photographs are private files, readable by people who can read the record
  they are attached to.
- The record of who viewed a full number is kept in Activity Log, which Frappe clears after
  90 days unless the site lengthens that. That limit is documented and is not a vulnerability.

**Permissions**

- Records are scoped per person as described in the README ("Who can read and export"). In
  particular Hospitality Manager and Facility Manager may open only the passes their work is
  about, and a Security officer only approved passes. Reading or changing a record outside
  those rules, including through a linked Hospitality Request, room booking or Security Log,
  is a vulnerability.
- Approval must come through the visitor type's approver, and from somebody other than the
  pass's creator, the user who sent it for approval and its host (the Administrator account
  excepted). A way to get a pass to Approved, or to the gate, without that is a vulnerability.
  One person who is not involved in the visit and holds both roles of a two-step lane taking
  both steps is documented behaviour, not a vulnerability.
- The app adds no permissions to Employee, Supplier, Job Applicant or Maintenance Visit. Its
  own search for those records returns a name and a label only. Getting more than that out of
  it is a vulnerability.
- Privacy-consent fields on a pass are written by the public form only. Setting them any other
  way is a vulnerability.

**Known and documented, not vulnerabilities:** the retention purge is off until switched on;
"QR Code Scanned" is asserted by the gate terminal rather than proven to the server; the
roles that still hold `export` on Security Log, Visitor Invitation and Hospitality Request as
shipped; database and backup access exposes everything; and the four residual points listed
under "Security testing" below.

Problems in Frappe, ERPNext or Frappe HR themselves should be reported to the Frappe team
under the security policy published in their repositories, not here.

## Security testing

Version 1.1.0 was put through a security test and, after the fixes, an independent re-test
(October 2026). Both were run **on a development site**, by reading the code and then
attacking the app's own pages, endpoints and records as each of its roles and as an anonymous
visitor.

**Result of the re-test.** Every finding of the first test — one medium and five low — was
confirmed fixed, and no critical, high or medium issue was found. In particular:

- the full ID number did not come back through any read path tried, for any role, and
  filtering or sorting on it was refused;
- ID scans were refused to the pass's own host and uploader, to ordinary staff, to Facility
  and Hospitality Managers and to anonymous callers, and served to Security only once the
  pass was approved;
- a pass could not be approved by the user who created and sent it, by any route tried;
- with the gate switches on, a Check-In without the QR scan, the photo or the identity
  confirmations was refused, as was reusing the visitor's ID scan as the gate photo;
- viewing a full ID number was limited to System Manager, and was recorded;
- forged consent, status and staff-only fields in a public-form submission were discarded,
  and malformed or disguised uploads were refused cleanly.

**Live test of the public form's limits.** The rate limits were then exercised against the
running development site, as an anonymous visitor:

- the ceilings held as documented: the 61st meal-plan preview in an hour was refused, the
  31st invitation look-up, and the 21st submission (20 drafts were created); after 20 wrong
  invitation links, every link from that caller — the valid one included — was answered as
  not valid, on the API and on the page, while another caller was unaffected;
- 100 requests sent at once against a limit of 60 got exactly 60 answers and 40 refusals;
- a visitor could not pass as signed-in staff (forged cookies and tokens stayed anonymous
  and limited), and the methods that need a login refused anonymous callers uniformly;
- **one low-severity finding:** the meal-plan preview kept a separate budget for each API
  route, so a caller could get a second 60 an hour through `/api/v2/method`. It exposes no
  personal data and writes nothing. It is fixed in 1.1.0 — the preview now has one budget
  per caller on every route. The fix was checked in the code and by the app's own tests; it
  was not put through a second live test. Submissions and invitation look-ups were not
  affected;
- changing `X-Forwarded-For` did reset the limits **on the test server**, which was reached
  directly on the same machine with no reverse proxy in front. That is residual point 2
  below, not a new defect.

**Residual points, all low or informational, left as documented behaviour:**

1. **Staff can list colleagues' names and departments through the host picker.** Any employee
   who can raise a pass can search employees by name, because any employee may be a host. No
   contact, salary or identity data is returned. By design.
2. **The public form's per-address limits depend on a trusted proxy.** They believe
   `X-Forwarded-For` only from the same machine, a private or link-local address, or an
   address listed in `trusted_proxy_ips`; a caller arriving from a public address is never
   believed. Behind bench's standard nginx, which overwrites the header, this is correct. For
   Frappe Cloud it is expected to be correct but is **inferred from Frappe's published
   configuration, not observed** — test on a real site. A site whose app server is exposed
   without a reverse proxy that overwrites that header — reachable directly from an office
   network, say, or behind a proxy that passes the visitor's own header through — must set
   `trusted_proxy_ips` (and make the proxy overwrite the header), or the limits can be reset
   by changing it.
3. **VMS Settings is readable by every employee.** It shows the gate switches, the privacy
   notice, branding and the home country. It holds no secrets.
4. **A visitor without an invitation may name a host who cannot be matched.** So that the form
   never reveals who works at the site, such a registration is saved as a draft for reception
   to complete, rather than refused.

**What was not tested.** A real camera, badge printer and mail server; the limits behind a
real reverse proxy; Frappe Cloud or any production hosting; Frappe, ERPNext and Frappe HR
themselves; and the Frappe 16 line of the app. A test on a development site is evidence about
the code, not a certification of your installation.

## For administrators

Settings on your own site that affect how exposed the app is:

- Leave the System Setting **Allow Guests to Upload Files** off unless another app needs it.
  This app does not.
- **Never grant permission level 1** on Visitor Pass or Visitor Blacklist to any role. The
  full ID number is kept at that level precisely because nobody holds it.
- Give each **approver role** to at least two people. Nobody can approve a pass they created,
  sent for approval or host, so a role with a single holder leaves that person's own passes
  waiting. If two different people must sign a two-step approval, do not give both roles to
  the same user.
- Keep the **System Manager** role to the few people who should be able to view full ID
  numbers and export passes, and lengthen Activity Log retention (**Log Settings**) if you
  need the record of those views for more than 90 days.
- Decide whether the public form should accept visitors without an invitation (**VMS Settings
  → Allow Pre-Registration Without an Invitation**), and review the privacy notice shown on it.
- Decide which gate checks are mandatory in **VMS Settings → Gate & Security**. On a new
  installation the QR scan, the gate photo and the identity confirmations are optional until
  you tick them; a site upgraded from 1.0.0 has them switched on.
- Switch on the retention purge in **VMS Settings** once you have decided a period.
- Keep Frappe's **`developer_mode` off in production**. With it on, error answers — including
  those of the public form — carry debugging detail from the server.
- **Reverse proxies.** The public form's per-address limits believe `X-Forwarded-For` only
  from a proxy on the same machine or on a private or link-local address, which covers the
  usual bench, Docker, Kubernetes and Frappe Cloud set-ups. If your proxy reaches the app
  server from any other address (carrier-grade NAT space, a public address), or your app
  server can be reached directly from an office network, set `trusted_proxy_ips` in the
  site's configuration to the addresses or networks of your proxies. When it is set, only
  those and the same machine are believed.
