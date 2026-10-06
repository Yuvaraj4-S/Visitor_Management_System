# Privacy templates for Visitor Management

> **These are templates, not legal advice, and not a published policy.** Nothing in this file
> binds anyone until a person with authority adapts it, has it reviewed by a lawyer who knows
> the law that applies to them, and publishes it under their own name. Text in
> `[SQUARE BRACKETS]` must be replaced or removed.

There are two templates here, for two different readers:

- **Part A** is for the **publisher** of the app: a privacy policy to host at a public address
  of the publisher's own, which the Frappe Cloud Marketplace listing asks for.
- **Part B** is for the **organisation that installs the app**: a notice to show its own
  visitors, who are the people whose data the app actually holds.

The factual statements about what the app does were checked against the code of version 1.1.0
(Frappe 15). If the app changes — for example, if it ever makes a connection to an outside
service — these templates must change with it. The detailed, maintained account is the
"Data & privacy" section of [`README.md`](README.md).

---

## Part A — Publisher's privacy policy (template)

**Last updated:** `[DATE]`
**Publisher:** `[LEGAL ENTITY NAME, REGISTERED ADDRESS]`
**Contact:** `[PRIVACY CONTACT EMAIL]`

### 1. What this policy covers

Visitor Management is a software application that you (the customer) install on your own
Frappe / ERPNext site, on Frappe Cloud or on your own server. This policy explains what data
the application handles and what `[PUBLISHER]` does and does not receive.

### 2. We do not receive your visitors' data

The application runs entirely on your site. It makes no network connection to `[PUBLISHER]`
or to any third party: no telemetry, no analytics, no licence check, and no fonts or scripts
loaded from outside your site. Visitor records, photographs and ID documents are stored in
your site's database and file storage and stay there. The emails the application sends go out
through the mail account you configure on your site.

`[PUBLISHER]` therefore does not collect, access or process your visitors' personal data,
unless you separately engage us for support and give us access to your site (see section 6).
`[FOR THE LAWYER: whether the publisher is a processor in any circumstance, and the wording of
this paragraph, is a legal conclusion to confirm.]`

### 3. Data the application stores on your site

You decide what is collected and you are responsible for it. The application stores what you
and your visitors enter:

| Data | Where |
|---|---|
| Visitor name, mobile number, email address, company, nationality | Visitor Pass, Visitor Invitation; name and mobile are copied to the visit's Security Log and Hospitality Request |
| Photograph (uploaded at pre-registration, and taken at the gate) | Private file attachments |
| Government ID type and number | Visitor Pass. Stored in full as plain text, not encrypted, so that blacklist and duplicate checks can match on it. It is shown masked on every screen, report, email and print; only a System Manager of your site can view a full number, and each view is recorded. The Security Log keeps the masked form only |
| Scan or photo of the ID document; visa copy for foreign visitors | Private file attachments, which only your security staff, the approvers of that kind of visit and your System Managers can open |
| People accompanying a visitor — name, mobile number, ID type and number | Visitor Pass, handled as for the main visitor |
| That the visitor accepted your privacy notice, when, and which version of it | Visitor Pass |
| Vehicle number, items carried, purpose of visit, host | Visitor Pass, Security Log |
| Dietary needs, allergies, accessibility requirements, hotel and transport details | Hospitality Request |
| Gate events: check-in, check-out, gate, time, verifying officer | Security Log, Visitor Event Log |
| Areas visited and times | Contact Trace Record |
| Blacklist entries: name, ID number (masked on screen), mobile number, reason | Visitor Blacklist |

Access is controlled by your site's role permissions and by the application's own rules for
each kind of record. Anyone with database or backup access to your site can read all of it,
including full ID numbers.

### 4. Retention and deletion

The application keeps visitor data until you delete it. It includes an optional retention
job, switched off by default. When you enable it and set a period, a nightly job anonymises
visits that are finished and older than that period: it clears the details that identify the
visitor and the people with them — names, contact details, ID numbers, vehicle, purpose of
visit, items carried, dietary and accessibility information — on the pass and on the gate
logs, hospitality request, room booking and invitation of that visit, deletes the ID scan,
visa copy, photographs and QR code, and removes the same details from those records' change
history and from the alert emails stored about them. A registration a visitor started on the
public form and nobody took further is cleared 30 days after its visit date. The visit itself
(dates, host, gate, approval history) and the record of the visitor's consent are kept.
Blacklist entries are never purged. `[FOR THE LAWYER: the README lists the few traces that
remain — a three-letter fragment of the visitor's name in a record name, backups, mail already
delivered.]`

Uninstalling the application removes its records and permanently deletes the stored ID scans,
photographs and visa copies.

Requests to erase or to obtain a copy of one person's data can be raised by your
administrators with Frappe's built-in personal-data requests, which cover that person's
passes and invitations. The application has no visitor-facing tool for such requests and no
way to withdraw consent; handling them is your responsibility.

### 5. Your responsibilities

- Tell visitors what you collect and why. The public pre-registration form shows the notice
  you enter in **VMS Settings → Privacy Notice & Consent** and records each visitor's
  agreement; Part B below is a starting point for it.
- Decide a retention period and enable the retention job.
- Decide who holds the System Manager role, which can view full ID numbers and export passes.
- Meet the requirements of the laws that apply to you `[FOR EXAMPLE: GDPR, India's Digital
  Personal Data Protection Act]`.

### 6. Data we may receive when you contact us

If you email us or open an issue, we receive what you send: your name, email address, and
anything you include, such as screenshots or logs. Please remove visitor personal data from
anything you send. We use this information only to respond to you, and keep it for
`[PERIOD]`. If you grant us access to your site for support, we access only what is needed
for that request, under `[REFERENCE TO SUPPORT TERMS / DATA PROCESSING AGREEMENT, IF OFFERED]`.

Public issue trackers are public: do not post personal data or security details there.

### 7. Frappe Cloud

If you install the application from the Frappe Cloud Marketplace, Frappe Cloud hosts your
site and processes billing. Their handling of your data is covered by Frappe's own privacy
policy and terms, not this one. `[IF PAID PLANS ARE OFFERED: describe what subscriber details
Frappe Cloud shares with the publisher, as stated in the Marketplace terms in force.]`

### 8. Third-party components

The application bundles the open-source html5-qrcode library to read QR codes in the
browser. It runs locally in the browser and sends nothing to its authors. Camera images used
for scanning are processed in the browser; the gate photograph you choose to capture is
uploaded to your site only.

### 9. Changes

We will post changes to this policy at this address and update the date above.

### 10. Contact

`[PRIVACY CONTACT EMAIL]` · `[POSTAL ADDRESS]`
`[IF REQUIRED IN YOUR JURISDICTION: grievance officer / data protection contact name and details.]`

---

## Part B — Notice to visitors (template for the organisation running the app)

The public pre-registration form shows a privacy notice above a box the visitor must tick,
and records on each pass that the visitor agreed, when, and to which version of the notice.
If you leave **VMS Settings → Privacy Notice & Consent → Privacy Notice Text** blank, the app
shows a short standard notice. To use your own, adapt the text below, have it reviewed, and
paste it into that field. It is shown as plain text; a web address in it becomes a link, so
the usual pattern is the short version in the field and the full version on your own website.
When you change the text, the notice version recorded with each consent goes up by one.

Passes your own staff create at the desk do not go through the form and record no consent:
tell those visitors in person or with a notice at reception.

> **Short version, for the Privacy Notice Text field**
>
> `[ORGANISATION]` collects your name, contact details, photograph and a copy of your ID to
> manage your visit and keep our premises secure. They are seen only by the people who handle
> your visit and our security staff, and kept for `[PERIOD]`. Full notice:
> `[ADDRESS OF YOUR PRIVACY NOTICE]`. Questions: `[CONTACT]`.

> **Full version, for your own website**
>
> **Who we are.** `[ORGANISATION, ADDRESS]` runs the visitor registration you are using and
> decides how your information is used. Contact: `[CONTACT]`.
>
> **What we collect.** Your name, mobile number, email address, organisation and nationality;
> a photograph of you; the type and number of the identity document you present and a copy of
> it (and of your visa, if you are visiting from another country); the same for anyone
> accompanying you; your vehicle number and the items you bring in, if you declare them; the
> times you enter and leave and the areas you visit; and, if we arrange them for you, your
> meal, transport and accommodation needs.
>
> **Why.** To approve and manage your visit, to verify your identity at the gate, to keep our
> premises and people secure, and `[OTHER PURPOSES]`. `[LEGAL BASIS, WHERE THE LAW REQUIRES ONE
> TO BE STATED. NOTE FOR THE LAWYER: THE FORM RECORDS A TICK AGAINST THIS NOTICE; WHETHER THAT
> TICK IS "CONSENT" IN THE LEGAL SENSE, OR AN ACKNOWLEDGEMENT OF A NOTICE WITH ANOTHER LEGAL
> BASIS, IS FOR YOU TO DECIDE AND WORD.]`
>
> **Who sees it.** The person you are visiting and the staff who approve and arrange your
> visit see your details, with your ID number shown only in part. The copy of your ID is seen
> only by our security staff and the people who approve your visit. `[ANY OTHER RECIPIENTS —
> FOR EXAMPLE A HOSTING PROVIDER.]`
>
> **How long we keep it.** `[PERIOD]` after your visit, after which the details that identify
> you are removed. `[STATE THIS ONLY IF THE RETENTION JOB IS SWITCHED ON WITH THAT PERIOD.]`
>
> **Your choices.** `[HOW TO ASK FOR A COPY, A CORRECTION OR DELETION, AND WHO TO COMPLAIN TO.
> THE APP HAS NO SELF-SERVICE TOOL FOR THESE; SAY HOW YOU WILL HANDLE A REQUEST.]`
