# For license information, please see license.txt

import re
import frappe
import qrcode
from frappe import _
from frappe.model.document import Document
from frappe.model.naming import getseries
from frappe.utils import now_datetime, today, get_url, getdate, get_time, date_diff, cint
from io import BytesIO
from visitormanagement.visitor_management.lifecycle import (
    ensure_hospitality_request,
    normalize_visitor_pass,
)
from visitormanagement.visitor_management.validators import (
    foreign_national_id_types,
    id_proof_error_message,
    is_valid_for_foreign_nationals,
    normalise_id_number,
    validate_id,
)
from visitormanagement.visitor_management import settings as vms_settings

# Lane names are derived from each Visitor Type's approver_role — there is no
# fixed list of approvers. `workflow_builder` generates a matching workflow state
# and transitions for every role in use, so a Visitor Type pointed at any role
# routes correctly without a code change.
from visitormanagement.visitor_management.workflow_builder import (
    APPROVED_STATES,
    lane_for_role,
    pending_lanes,
)


# Fields that belong to one visitor-type layout only, keyed by that layout — so
# a custom Visitor Type reusing a shipped layout gets the same set. Used both to
# summarise a pass and to clear values that no longer apply after the type
# changes (see VisitorPass._clear_fields_from_other_layouts).
LAYOUT_FIELDS = {
    "Supplier": [
        "supplier_visit_mode",
        "supplier_link",
        "purchase_order",
        "delivery_note",
        "goods_description",
        "meeting_subject",
        "nda_required",
        "documents_shared",
    ],
    "Customer": [
        "crm_reference_type",
        "crm_lead_opportunity",
        "visit_category",
        "sales_executive",
        "products_discussed",
        "meeting_outcome",
        "followup_date",
        "meeting_minutes",
    ],
    "Contractor": [
        "contractor_link",
        "work_order_ref",
        "tools_list",
        "multi_day_pass",
        "pass_valid_until",
    ],
    "Candidate": [
        "job_applicant_link",
        "position_applied",
        "candidate_interview_type",
        "interview_panel",
    ],
    # The VIP layout was missing from this map entirely, which meant its fields
    # were the only ones never cleared when a pass belonged to some other type.
    # Combined with `vip_category` being a Select whose options did not start
    # with a blank line — so Frappe assigned the first option, "Board Member",
    # to every pass that left it empty — 145 Contractor, Customer, Auditor,
    # Supplier and Candidate passes on this site were carrying
    # `vip_category = Board Member` in reports and exports, on records where the
    # VIP section is not even displayed.
    "VIP": [
        "vip_category",
        "mdceo_notified",
        "interpreter_required",
        "interpreter_language",
        "protocol_notes",
    ],
}


def _get_visitor_type_doc(visitor_type_name):
    """Fetch the linked Visitor Type master record (cached — this is a small,
    frequently-read doc, so `get_cached_doc` avoids a DB round-trip on every
    Visitor Pass save)."""
    if not visitor_type_name:
        return None
    try:
        return frappe.get_cached_doc("Visitor Type", visitor_type_name)
    except frappe.DoesNotExistError:
        return None


def _get_home_country():
    """The configured home country (see visitor_management.settings)."""
    return vms_settings.home_country()


class VisitorPass(Document):

    # Aliases used by notification templates and external references.
    # `visitor_name` is referenced by gate_security_alert.html and vms_prr_submitted notification.
    # `company` is referenced by gate_security_alert.html as {{ doc.company }}.
    @property
    def visitor_name(self):
        return self.visitor_full_name

    @property
    def company(self):
        return self.company__organisation

    def _visitor_type_flag(self, fieldname, default=0):
        """Read a behaviour flag off the linked Visitor Type master.

        Behaviour is configured per type rather than branching on the type's
        *name*, so a site can add e.g. a second executive-approval type without
        touching this controller.
        """
        visitor_type_doc = _get_visitor_type_doc(self.visitor_type)
        if visitor_type_doc is None:
            return cint(default)
        return cint(getattr(visitor_type_doc, fieldname, default))

    def validate(self):
        # Nationality is mandatory. It defaults to the configured home country so
        # that any creation path (portal, API, automation, import) that doesn't
        # supply it still saves, instead of failing mandatory validation.
        if not self.custom_nationality:
            self.custom_nationality = _get_home_country()

        self._force_draft_for_untrusted_creation()
        self._sanitize_free_text()
        self._sync_status_with_workflow()
        normalize_visitor_pass(self)
        self._clear_fields_from_other_layouts()
        self._align_workflow_lane_with_visitor_type()
        self._validate_not_blacklisted()
        self._validate_schedule()
        self._validate_formats()
        self._validate_host_active()
        self._validate_duplicate_pass()
        self._validate_visit_duration()

    def _force_draft_for_untrusted_creation(self):
        """A pass created by an anonymous visitor always starts in Draft.

        The approval state is the app's only gate credential — `visitor_gate`
        admits an Approved pass — so it must be earned through the workflow, never
        asserted by the request that creates the record. Frappe's Web Form
        `accept` binds every field listed in `web_form_fields` straight from the
        POST body, and `hidden` is a rendering flag rather than an authorization
        boundary; the governance fields have been unbound there, but this is the
        control that actually holds, on every path into the doctype.

        Only creation is forced. Once staff advance a pass through the workflow
        the states are legitimate, so an existing record is left alone.
        """
        if not self.is_new():
            return
        if frappe.session.user != "Guest":
            return

        self.status = "Draft"
        self.workflow_state = "Draft"
        self.docstatus = 0
        # Provenance is a statement about how the record arrived, so the server
        # states it rather than accepting the caller's word for it.
        self.entry_type = "New"
        self.request_channel = "Portal"
        # Approval and gate artefacts cannot pre-exist the approval itself.
        for fieldname in (
            "badge_number", "approved_by", "approval_date", "qr_code_image",
            "actual_checkin", "actual_checkout", "no_show", "current_location",
            "gate_verified_by", "gate_verified_on", "gate_verified_photo",
        ):
            self.set(fieldname, None)

    # Free-text fields a visitor fills in themselves. Their values are rendered
    # back to staff in datatable report cells and in Notification email bodies —
    # Frappe's Jinja does not autoescape, and a report formatter writes raw HTML
    # into the cell — so the audience for anything smuggled in here is the
    # System Manager running the PII reports.
    GUEST_TEXT_FIELDS = (
        "visitor_full_name",
        "company__organisation",
        "purpose_of_visit",
        "vehicle_number",
        "tools_list",
        "position_applied",
        "meeting_subject",
        "interview_panel",
    )

    def _sanitize_free_text(self):
        """Remove markup from visitor-supplied text at the point it is stored.

        Stripped rather than sanitised: `sanitize_html` keeps `<a>` and `<img>`,
        which is the whole payload for a link-injection phish landing in a
        Security or System Manager inbox. None of these fields — a person's name,
        their employer, why they are visiting — has any legitimate use for
        markup, so tags come out entirely.

        Cleaning on the way in rather than at each render means a report column
        or notification template added later cannot reintroduce the hole by
        forgetting to escape.
        """
        from frappe.utils import strip_html

        for fieldname in self.GUEST_TEXT_FIELDS:
            value = self.get(fieldname)
            if not isinstance(value, str) or "<" not in value:
                continue
            cleaned = strip_html(value).strip()
            if cleaned != value:
                self.set(fieldname, cleaned)

    def _sync_status_with_workflow(self):
        """Keep `status` telling the same story as `workflow_state`.

        The two had drifted apart: the workflow moved Draft -> Pending -> Rejected
        while `status` sat on "Draft" the whole way, so a rejected pass still
        reported itself as a draft in every list, report and dashboard. `status`
        did not even carry a Rejected option, which meant a notification condition
        written as `doc.status == 'Rejected'` could never be true.

        Only the approval half is derived here. Once a pass is approved the gate
        owns `status` — Items Verified, Checked-In and Checked-Out are movements,
        not approval states, and must not be overwritten by a later save.
        """
        state = self.workflow_state
        if not state:
            return

        if state in pending_lanes():
            self.status = "Pending Approval"
        elif state == "Rejected":
            self.status = "Rejected"
        elif state == "Draft":
            # Reapply returns a rejected pass to Draft; the status has to come
            # back with it rather than stay stuck on Rejected.
            if self.status in (None, "", "Rejected", "Pending Approval"):
                self.status = "Draft"
        elif state == "Approved":
            if self.status not in ("Items Verified", "Checked-In", "Checked-Out", "Cancelled"):
                self.status = "Approved"

    def _clear_fields_from_other_layouts(self):
        """Drop values belonging to a layout this pass is not using.

        Two things make this necessary. `crm_reference_type` carries a doctype
        default of "Lead", so every pass — Contractor, Candidate, anything —
        gets saved with a Customer-only value it never displays; the client
        clears it on load, which marks a freshly opened pass dirty and hides the
        workflow Actions button behind Save. And when someone switches the
        Visitor Type on an existing pass, the previous layout's answers would
        otherwise stay in the database, invisible on screen but present in
        reports and exports.
        """
        keep = set(LAYOUT_FIELDS.get(self.visitor_type_layout or "", []))
        for layout, fieldnames in LAYOUT_FIELDS.items():
            if layout == self.visitor_type_layout:
                continue
            for fieldname in fieldnames:
                if fieldname in keep or not self.get(fieldname):
                    continue
                self.set(fieldname, None)

    # ─────────────────────────────────────────────────────────
    # BUSINESS VALIDATIONS
    # ─────────────────────────────────────────────────────────
    def _validate_not_blacklisted(self):
        """Stop a blacklisted visitor as soon as the pass leaves Draft.

        `before_submit` already blocks the final Approve, but a workflow's
        intermediate lanes keep docstatus 0, so a blacklisted visitor could sit
        in an approver's queue until someone tried to approve them. Checking on
        the way out of Draft keeps them out of the queue entirely, while still
        allowing reception to save a draft and correct it.
        """
        state = (self.workflow_state or "Draft").strip()
        if state in ("Draft", "Rejected", ""):
            return

        from visitormanagement.visitor_management.doctype.visitor_blacklist.visitor_blacklist import (
            VisitorBlacklist,
        )

        match = VisitorBlacklist.find_active_match(
            id_proof_number=self.id_proof_number,
            visitor_name=self.visitor_full_name,
            id_proof_type=self.id_proof_type,
            mobile_number=self.mobile_number,
        )
        if not match:
            # No strong (ID, or corroborated name+mobile) match. A weak,
            # single-field hit still deserves a human's attention rather than
            # vanishing — see _warn_weak_blacklist_match.
            self._warn_weak_blacklist_match()
            return

        blacklist = frappe.get_doc("Visitor Blacklist", match)
        self._alert_blacklist_match(blacklist)
        frappe.throw(
            _(
                "Visitor: {0}\nReason: {1}\n\n"
                "This person is on the active blacklist. The pass cannot be sent for approval."
            ).format(self.visitor_full_name, blacklist.reason or _("Not specified")),
            title=_("Access Denied — Blacklisted Visitor"),
        )

    def _warn_weak_blacklist_match(self):
        """Surface a visible, non-blocking warning when this visitor matches
        an active blacklist entry on ONE weak identifier only (name or
        mobile — not both, and not the ID number).

        `VisitorBlacklist.find_active_match()` deliberately does not hard-block
        on a single weak field any more (see its docstring: a shared name used
        to bar a real, unrelated visitor). But silently doing nothing would
        trade one failure for a worse one — a security team believes they
        barred someone by name, the entry sits Active in the list, and it
        never fires again. So this hands the decision to whoever is looking at
        the pass right now: it names the matched entry and its reason and asks
        them to verify ID by hand. It does NOT stop the save.
        """
        from visitormanagement.visitor_management.doctype.visitor_blacklist.visitor_blacklist import (
            VisitorBlacklist,
        )

        weak = VisitorBlacklist.find_weak_match(
            visitor_name=self.visitor_full_name,
            mobile_number=self.mobile_number,
        )
        if not weak:
            return

        blacklist = frappe.get_doc("Visitor Blacklist", weak["name"])
        frappe.msgprint(
            _(
                "This visitor's {0} matches an active blacklist entry ({1}, reason: {2}), "
                "but not strongly enough to block automatically.<br>"
                "<b>Verify their ID before allowing entry.</b>"
            ).format(
                _(weak["matched_on"]),
                blacklist.name,
                frappe.utils.escape_html(blacklist.reason or _("Not specified")),
            ),
            title=_("Possible Blacklist Match — Verify ID"),
            indicator="orange",
        )

    def _validate_schedule(self):
        """Block past dates, enforce check-in < check-out, enforce future-date ceiling."""
        # A multi-day pass with no end date was blocked only by the browser
        # (visitor_pass.js's toggle_reqd), so anything that did not go through the
        # form — an import, an integration, a portal submission — could store one.
        # It fails shut rather than open: security_log.py needs BOTH multi_day_pass
        # and pass_valid_until before it will allow re-entry, so such a pass simply
        # behaves as single-day. That is the damage — a contractor is told they have
        # a week's access and is turned away on day two, while the record still reads
        # "multi-day" to whoever issued it. The sibling rule (a foreign national needs
        # a visa copy) is already enforced server-side; this one now matches.
        if getattr(self, "multi_day_pass", 0) and not self.pass_valid_until:
            frappe.throw(
                _("A multi-day pass needs a 'Pass Valid Until' date."),
                title=_("Missing Pass Validity"),
            )

        if not self.visit_date:
            return

        today_date = getdate(today())
        visit_date = getdate(self.visit_date)

        # Past date blocked — allow only if the pass is already checked-in/out (historical edits OK)
        if visit_date < today_date and self.status in (None, "", "Draft", "Approved"):
            if not (self.docstatus == 1 and self.status in ("Checked-In", "Checked-Out", "Cancelled")):
                frappe.throw(
                    _("Visit date {0} is in the past. Pick today or a future date.").format(self.visit_date),
                    title=_("Invalid Visit Date"),
                )

        # Future date ceiling — configurable in VMS Settings (0 disables it)
        max_days = vms_settings.max_advance_booking_days()
        if max_days and date_diff(visit_date, today_date) > max_days:
            frappe.throw(
                _("Visit date cannot be more than {0} days in the future.").format(max_days),
                title=_("Invalid Visit Date"),
            )

        # Check-in before check-out
        if self.expected_checkin and self.expected_checkout:
            if get_time(self.expected_checkin) >= get_time(self.expected_checkout):
                frappe.throw(
                    _("Expected Check-In ({0}) must be before Expected Check-Out ({1}).").format(
                        self.expected_checkin, self.expected_checkout
                    ),
                    title=_("Invalid Time Range"),
                )

    def _validate_formats(self):
        """Validate email and ID proof number format per type."""
        if self.email_id:
            if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", self.email_id):
                frappe.throw(
                    _("Email ID '{0}' is not a valid email address.").format(self.email_id),
                    title=_("Invalid Email"),
                )

        if self.id_proof_type and self.id_proof_number:
            if not validate_id(self.id_proof_type, self.id_proof_number):
                frappe.throw(
                    _(id_proof_error_message(self.id_proof_type)),
                    title=_("Invalid ID Proof"),
                )
            # validate_id() above only tested a normalised COPY of the number
            # against the ID Proof Type master's regex — it never changed what
            # gets saved. Re-assign the normalised form so the stored value is
            # the one that actually passed validation; otherwise "lfc-1234"
            # saves as typed even though the master says "Uppercase and strip
            # spaces", and an exact-match lookup for "LFC-1234" (gate guard,
            # report) never finds it. No-op for the four built-in types or an
            # unknown type, which normalise_id_number() returns unchanged.
            self.id_proof_number = normalise_id_number(self.id_proof_type, self.id_proof_number)

        # Which documents a foreign national may present is a flag on the ID Proof
        # Type master, not a hardcoded "Passport". A site can mark a foreign
        # national ID / residence permit valid without a code change — and the
        # old rule also forced foreign visitors onto the *Indian* passport
        # pattern, which rejects most real foreign passport numbers.
        if self.id_proof_type and self.custom_nationality and self.custom_nationality != _get_home_country():
            if not is_valid_for_foreign_nationals(self.id_proof_type):
                allowed = ", ".join(foreign_national_id_types()) or _("none configured")
                frappe.throw(
                    _("Foreign national visitors must use one of: {0}.").format(allowed),
                    title=_("Invalid ID Proof Type"),
                )

    def _validate_host_active(self):
        """Host must be an Active employee."""
        if not self.person_to_visit:
            return
        status = frappe.db.get_value("Employee", self.person_to_visit, "status")
        if status != "Active":
            frappe.throw(
                _("Host {0} is not an Active employee (status: {1}). Cannot assign pass.").format(
                    self.person_to_visit, status or "Unknown"
                ),
                title=_("Invalid Host"),
            )

    def _validate_duplicate_pass(self):
        """Same visitor (by ID proof) cannot have multiple active passes on the same date.

        `FOR UPDATE` is what makes this hold under concurrency. Read-then-insert
        is a time-of-check/time-of-use race: two portal submissions arriving
        together both find nothing and both insert, so the rule this message
        announces would not actually be true. Under REPEATABLE READ the locking
        read takes a gap lock on the `id_proof_number` index range, so the second
        transaction waits and then sees the first one's row.

        The lock is held until the surrounding transaction commits, and it is
        narrow because `id_proof_number` is indexed — without that index InnoDB
        would escalate to locking the whole table on every save.
        """
        if not self.id_proof_number or not self.visit_date:
            return
        existing = frappe.db.sql(
            """
            SELECT name FROM `tabVisitor Pass`
            WHERE id_proof_number = %(id)s
              AND visit_date = %(date)s
              AND name != %(self_name)s
              AND docstatus < 2
              AND status NOT IN ('Cancelled', 'Rejected')
            LIMIT 1
            FOR UPDATE
            """,
            {
                "id": self.id_proof_number,
                "date": self.visit_date,
                "self_name": self.name or "NEW",
            },
        )
        if existing:
            frappe.throw(
                _("A visitor pass ({0}) already exists for this ID Proof on {1}. Duplicate passes are not allowed.").format(
                    existing[0][0], self.visit_date
                ),
                title=_("Duplicate Pass"),
            )

    def _validate_visit_duration(self):
        """Enforce max visit duration from VMS Settings."""
        if not (self.expected_checkin and self.expected_checkout):
            return

        if self._is_host_approved_invitation_schedule():
            return

        settings = frappe.get_cached_doc("VMS Settings")
        max_hours = cint(getattr(settings, "max_visit_duration_hrs", 0))
        if not max_hours:
            return

        ci = get_time(self.expected_checkin)
        co = get_time(self.expected_checkout)
        duration_hours = (co.hour * 60 + co.minute - ci.hour * 60 - ci.minute) / 60
        if duration_hours > max_hours:
            frappe.throw(
                _("Visit duration ({0:.1f} hrs) exceeds the maximum allowed ({1} hrs). "
                  "Adjust the expected check-in/out times.").format(duration_hours, max_hours),
                title=_("Visit Too Long"),
            )

    def _is_host_approved_invitation_schedule(self):
        """Allow invitation-backed passes to retain the host-approved visit window."""
        if not self.visitor_invitation or not frappe.db.exists("Visitor Invitation", self.visitor_invitation):
            return False

        invitation = frappe.get_cached_doc("Visitor Invitation", self.visitor_invitation)
        return (
            str(self.visit_date or "") == str(invitation.visit_date or "")
            and str(self.expected_checkin or "") == str(invitation.expected_checkin or "")
            and str(self.expected_checkout or "") == str(invitation.expected_checkout or "")
            and (self.person_to_visit or "") == (invitation.host_employee or "")
        )

    def _pending_lanes_for_type(self):
        """The pending lane(s) a pass of this visitor type may occupy, derived
        from the type's approver_role (+ secondary_approver_role). Data-driven,
        so custom Visitor Types route correctly without a hardcoded map."""
        visitor_type_doc = _get_visitor_type_doc(self.visitor_type)
        if not visitor_type_doc:
            return ()
        lanes = []
        for role in (
            getattr(visitor_type_doc, "approver_role", None),
            getattr(visitor_type_doc, "secondary_approver_role", None),
        ):
            if not role:
                continue
            lane = lane_for_role(role)
            if lane not in lanes:
                lanes.append(lane)
        return tuple(lanes)

    def _align_workflow_lane_with_visitor_type(self):
        if not self.visitor_type or not self.workflow_state:
            return

        if self.workflow_state not in pending_lanes():
            return

        allowed_lanes = self._pending_lanes_for_type()
        if not allowed_lanes:
            return

        if self.workflow_state in allowed_lanes:
            return

        self.workflow_state = allowed_lanes[0]

    # ─────────────────────────────────────────────────────────
    # BEFORE SAVE
    # ─────────────────────────────────────────────────────────
    def before_save(self):
        self._sync_items_carried()
        self._normalize_mobile_number()
        # Must follow _normalize_mobile_number: this mirrors the *stored* number,
        # so deriving it any earlier would index a pre-normalisation value.
        self.mobile_digits = _normalized_digits(self.mobile_number)
        self._set_visitor_summary()
        # Auto-fetch host department from Employee record
        if self.person_to_visit and not self.host_department:
            self.host_department = frappe.db.get_value(
                "Employee", self.person_to_visit, "department"
            )

        # Badge colour comes from the linked Visitor Type master.
        if self.visitor_type:
            visitor_type_doc = _get_visitor_type_doc(self.visitor_type)
            self.badge_colour = getattr(visitor_type_doc, "badge_colour", None) or "Orange"

        # Sync item verification status from child table
        if self.visitor_items:
            total = len(self.visitor_items)
            # Match fieldname from Visitor Item DocType
            verified = sum(1 for i in self.visitor_items if i.verified_by_security)

            if verified == 0:
                self.item_verification_status = "Pending"
            elif verified < total:
                self.item_verification_status = "Partial"
            else:
                self.item_verification_status = "All Verified"
                self.all_items_verified = 1

    def _alert_blacklist_match(self, blacklist_doc):
        recipients = self._security_alert_recipients()
        if not recipients:
            return
        try:
            frappe.sendmail(
                recipients=recipients,
                subject=f"🚨 Blacklist match attempt: {self.visitor_full_name}",
                message=(
                    f"<p><b>A blacklisted visitor attempted entry.</b></p>"
                    f"<ul>"
                    f"<li><b>Visitor:</b> {self.visitor_full_name}</li>"
                    f"<li><b>ID Proof:</b> {self.id_proof_type} — {self.id_proof_number}</li>"
                    f"<li><b>Mobile:</b> {self.mobile_number or '-'}</li>"
                    f"<li><b>Reason on file:</b> {blacklist_doc.reason}</li>"
                    f"<li><b>Attempted host:</b> {self.person_to_visit or '-'}</li>"
                    f"<li><b>Time:</b> {frappe.utils.now()}</li>"
                    f"</ul>"
                    f"<p>Entry was blocked. No Visitor Pass created.</p>"
                ),
                reference_doctype="Visitor Blacklist",
                reference_name=blacklist_doc.name,
                now=True,
            )
        except Exception as exc:
            frappe.log_error(f"Blacklist alert email failed: {exc}", "VMS Blacklist Alert")
            # sendmail(now=True) raises via frappe.throw on a site with no
            # outgoing Email Account, which also queues its own client
            # message even though we catch the exception here. Left alone,
            # that stray message rides along with the very next frappe.throw
            # this method's caller issues — the "Access Denied — Blacklisted
            # Visitor" dialog — so the receptionist sees an unrelated "setup
            # Email Account" line leaked inside a security refusal. Drop it,
            # the same fix Visitor Invitation.send_invitation already applies
            # to the identical failure mode.
            frappe.clear_messages()

    def _security_alert_recipients(self):
        user_names = frappe.get_all(
            "Has Role",
            filters={
                "role": ["in", vms_settings.security_alert_roles()],
                "parenttype": "User",
            },
            pluck="parent",
            distinct=True,
        )
        if not user_names:
            admin_email = vms_settings.admin_email()
            return [admin_email] if admin_email else []
        # Single bulk query for all emails instead of one get_value() per user
        # (avoids an N+1 round-trip per security/admin user on every alert).
        emails = frappe.get_all(
            "User",
            filters={"name": ["in", user_names]},
            pluck="email",
        )
        recipients = {e for e in emails if e and e != "Administrator" and "@" in e}

        # VMS Settings → Admin Email always gets security alerts, so a site with
        # no user holding an alert role still hears about a blacklist match.
        admin_email = vms_settings.admin_email()
        if admin_email:
            recipients.add(admin_email)

        return sorted(recipients)

    def _normalize_mobile_number(self):
        if not self.mobile_number:
            return
        raw = str(self.mobile_number).strip()

        if self.custom_nationality and self.custom_nationality != _get_home_country():
            # Foreign national — the number already carries its own country's
            # ISD prefix from the Phone widget; don't force Indian formatting.
            return

        digits = "".join(c for c in raw if c.isdigit())
        if not digits:
            return
        # Frappe Phone widget expects "+{isd}-{number}" format (hyphen, NOT space)
        # See apps/frappe/frappe/public/js/frappe/form/controls/phone.js:167
        isd = vms_settings.country_code()
        if digits.startswith(isd) and len(digits) > 10:
            digits = digits[len(isd):]
        self.mobile_number = f"+{isd}-{digits[-10:]}" if len(digits) >= 10 else raw

    def _set_visitor_summary(self):
        mobile_display = (self.mobile_number or "").replace("-", " ")
        parts = [self.visitor_full_name or "", mobile_display]
        self.visitor_summary = " | ".join(p for p in parts if p)

    def _sync_items_carried(self):
        """Bi-directional sync between `items_carried` (Small Text shown on the
        desk form) and the structured `visitor_items` child table.

        Rules — in priority order:
          1. If `visitor_items` already has rows AND items_carried matches the
             auto-summary of those rows → data is already consistent, leave
             everything alone. This is the path the portal uses when it sets
             both fields atomically with structured rows + a derived summary.
          2. If items_carried is set and visitor_items is empty → create a
             single mirror row (keeps gate verification working for
             desk-form users who only type free-text).
          3. If items_carried is set but visitor_items rows DON'T match the
             summary → the desk-form user just edited items_carried; rebuild
             the structured rows from the new text (single row mirror,
             preserving prior verification state).
          4. If items_carried is empty and rows exist → derive a summary into
             items_carried so prints / dropdowns have a single label.
        """
        text = (self.items_carried or "").strip()
        rows = list(self.visitor_items or [])
        auto_summary = self._summarise_items(rows)

        if rows and text and text == auto_summary:
            # Already consistent; don't rebuild anything.
            return

        if text:
            parsed = self._parse_items_carried(text)
            if not parsed:
                return

            current = [
                ((r.item_name or "").strip(), int(r.quantity or 1)) for r in rows
            ]
            wanted = [(p["item_name"], p["quantity"]) for p in parsed]
            if current == wanted:
                return

            # Keep whatever the gate already confirmed, matched by item name, so
            # editing the text does not silently un-verify an item an officer
            # has already checked.
            previous = {
                (r.item_name or "").strip().casefold(): r for r in rows
            }

            self.set("visitor_items", [])
            for item in parsed:
                row = self.append("visitor_items", {
                    "item_name": item["item_name"],
                    "quantity": item["quantity"],
                })
                prior = previous.get(item["item_name"].casefold())
                if prior is not None:
                    if prior.verified_by_security:
                        row.verified_by_security = 1
                    if prior.verification_remarks:
                        row.verification_remarks = prior.verification_remarks

            # Write the normalised summary back so the next save sees itself as
            # already in sync; otherwise "Lap,mobile" and "Lap, mobile" differ
            # forever and every save rebuilds the rows.
            self.items_carried = self._summarise_items(self.visitor_items)
        elif rows:
            # items_carried empty but rows exist (portal flow) → derive summary
            self.items_carried = auto_summary

    @staticmethod
    def _parse_items_carried(text):
        """Split the free-text list into one entry per item.

        A visitor typing "Lap, mobile" means two things, not one. Storing the
        whole string as a single row gave the gate officer one checklist line
        covering two physical items — so a laptop and a phone could not be
        verified, or found missing, independently. It also read back to the
        visitor as "Lap,mobile (Qty: 1.0)" in the approval mail.

        Understands the "(xN)" suffix `_summarise_items` writes, so parsing this
        function's own output round-trips to the same rows and repeated saves
        stay stable.
        """
        items = []
        for chunk in re.split(r"[,\n;]+", text or ""):
            name = chunk.strip()
            if not name:
                continue

            quantity = 1
            match = re.search(r"\(\s*x\s*(\d+)\s*\)\s*$", name, re.IGNORECASE)
            if match:
                quantity = int(match.group(1)) or 1
                name = name[: match.start()].strip()

            if name:
                items.append({"item_name": name, "quantity": quantity})
        return items

    @staticmethod
    def _summarise_items(rows):
        """Build the same comma-separated summary string the portal helper
        creates, so the controller can detect 'already-in-sync' state."""
        if not rows:
            return None
        parts = []
        for r in rows:
            name = (r.item_name or "").strip()
            if not name:
                continue
            qty = r.quantity
            if qty and int(qty or 0) > 1:
                name = f"{name} (x{int(qty)})"
            parts.append(name)
        return ", ".join(parts) if parts else None

    def on_update(self):
        if self.docstatus == 0 and self.status == "Draft":
            return
        ensure_hospitality_request(self)

    def run_notifications(self, method):
        """Keep a failing alert email from eating this save's real messages.

        The Notification records this app ships (VMS PRR Submitted, VMS Pass
        Rejected, ...) send through frappe.sendmail. On a site with no outgoing
        Email Account — every fresh install, and any site with a transient mail
        problem — that raises via frappe.throw, which queues a message carrying
        `raise_exception: 1`. The Desk renders that one and drops the plain
        messages queued earlier in the same response.

        The casualty is the blacklist warning: a visitor matching an entry on
        name alone is deliberately not blocked, only flagged for a human to
        check the ID. Proven live on this site — the warning WAS in the
        response and the approver still saw only "Please setup default outgoing
        Email Account", so a name-flagged visitor sailed through with no
        indication. A security prompt must not be one unrelated mail failure
        away from vanishing.

        So the queue is snapshotted and restored around the notification run,
        the same shape as security_log.py's `_advance_pass`. Anything the
        notifications queued is dropped (they have nothing to say to the user
        on the happy path); everything this save had already told the user
        survives. The underlying failure is still recorded in Error Log by
        core, so nothing is hidden from an administrator.
        """
        messages_before = list(frappe.message_log)
        try:
            super().run_notifications(method)
        finally:
            frappe.local.message_log = messages_before

    # ─────────────────────────────────────────────────────────
    # BEFORE SUBMIT
    # ─────────────────────────────────────────────────────────
    def _assert_reached_via_approval(self):
        """A submit is only legitimate as the tail of an Approve transition.

        `Approved` is the workflow's only `doc_status = 1` state, and Frappe's
        `set_workflow_state_on_action` force-sets the state that matches the new
        docstatus whenever a document is submitted directly — so a bare
        `frappe.client.submit` on a Draft lands on `Approved` without any
        transition ever being evaluated, skipping the approver entirely.

        The workflow's own record of where the document was is the thing to
        check: immediately before a genuine approval the stored state is the
        pending lane the approver is acting from. Draft or Rejected means the
        approval step never happened.
        """
        if self.flags.get("ignore_approval_lane_check"):
            return

        previous_state = frappe.db.get_value("Visitor Pass", self.name, "workflow_state")
        if previous_state in pending_lanes():
            return

        # A site with no Visitor Type configured has no lanes at all; blocking
        # every submit there would be worse than the risk it guards against.
        if not pending_lanes():
            return

        frappe.throw(
            _(
                "This pass is in <b>{0}</b> and has not been through approval. "
                "Use the workflow Actions button — a pass cannot be submitted directly."
            ).format(previous_state or _("an unknown state")),
            title=_("Approval Required"),
        )

    def before_submit(self):
        # 0️⃣ THE APPROVAL MUST HAVE BEEN EARNED
        self._assert_reached_via_approval()

        # 0️⃣.1 REQUIRED DOCUMENTS
        if not self.visitor_photo:
            frappe.throw(
                _("Visitor Photo is required before submitting the pass."),
                title=_("Missing Visitor Photo"),
            )
        if not self.id_proof_scan:
            frappe.throw(
                _("ID Proof Scan is required before submitting the pass."),
                title=_("Missing ID Proof Scan"),
            )

        # 0️⃣.5 OPTIONAL ITEM DECLARATION (enforced via VMS Settings)
        settings = frappe.get_cached_doc("VMS Settings")
        if settings.get("require_item_declaration") and not self.visitor_items:
            frappe.throw(
                _("Item declaration is required — add at least one item row before submitting."),
                title=_("Items Not Declared"),
            )

        # 1️⃣ BLACKLIST CHECK
        # Strong match only: exact ID proof number, or name+mobile corroborating
        # each other on the same entry (see VisitorBlacklist.find_active_match's
        # docstring for why name-alone/mobile-alone are not trusted to block).
        from visitormanagement.visitor_management.doctype.visitor_blacklist.visitor_blacklist import VisitorBlacklist
        blacklist_name = VisitorBlacklist.find_active_match(
            id_proof_number=self.id_proof_number,
            visitor_name=self.visitor_full_name,
            id_proof_type=self.id_proof_type,
            mobile_number=self.mobile_number,
        )

        if blacklist_name:
            bl = frappe.get_doc("Visitor Blacklist", blacklist_name)
            self._alert_blacklist_match(bl)
            frappe.throw(
                msg=_(
                    "Visitor: {0}\n"
                    "Reason: {1}\n\n"
                    "This person is on the active blacklist. The pass cannot be submitted."
                ).format(self.visitor_full_name, bl.reason or _("Not specified")),
                title=_("Access Denied — Blacklisted Visitor"),
            )
        else:
            # Weak, single-field hit — warn instead of silently doing nothing.
            self._warn_weak_blacklist_match()

        # 3️⃣ Executive notification — driven by the Visitor Type flag, not a name
        if self._visitor_type_flag("requires_executive_notification"):
            if not getattr(self, "mdceo_notified", 0):
                frappe.throw(
                    _("MD/CEO must be notified before submitting a VIP Visitor Pass. "
                      "Please check the MD/CEO Notified field in the VIP Details section."),
                    title=_("VIP Notification Required"),
                )

        # 4️⃣ Foreign National Document Check
        home_country = _get_home_country()
        if self.custom_nationality and self.custom_nationality != home_country:
            if not self.custom_visa_copy:
                frappe.throw(
                    _("Visa Copy is required for foreign national visitors."),
                    title=_("Missing Travel Documents"),
                )

    # ─────────────────────────────────────────────────────────
    # ON SUBMIT
    # ─────────────────────────────────────────────────────────
    def on_submit(self):
        # Record approval details
        self.db_set("approval_date", now_datetime())
        self.db_set("approved_by", frappe.session.user)
        self.db_set("status", "Approved")

        # Mint the badge now unless this Visitor Type issues it at the gate
        # (handled in security_log.py during check-in).
        if not self._visitor_type_flag("issue_badge_at_gate"):
            self.generate_badge_number(update_status=False)

        # Generate QR Code for the badge
        qr_file_url, qr_content = self._generate_qr_code()

        # Notify the visitor via email, and the food dept if a meal was requested.
        # Both are courtesy mails: an approval that cannot be emailed is still a
        # valid approval, so neither may block the submit or shout at the approver.
        # frappe.sendmail on a site with no outgoing Email Account raises through
        # frappe.throw, which queues a red "Please setup default outgoing Email
        # Account" message for the client — reported live on Approve. Snapshot and
        # restore around both, so a mail problem is logged for the administrator
        # and invisible to the approver, without discarding anything this save had
        # already told them. Same shape as run_notifications() above.
        messages_before = list(frappe.message_log)
        try:
            self._send_approval_email(qr_file_url, qr_content)
        except Exception as exc:
            frappe.log_error(
                f"Approval email failed for {self.name}: {exc}", "VMS Approval Email"
            )
        finally:
            frappe.local.message_log = messages_before

        if getattr(self, "meal_required", 0):
            messages_before = list(frappe.message_log)
            try:
                self._notify_food_dept()
            finally:
                frappe.local.message_log = messages_before

    # ─────────────────────────────────────────────────────────
    # GENERATE BADGE NUMBER (Called by Security Log)
    # ─────────────────────────────────────────────────────────
    def generate_badge_number(self, update_status=True):
        """Generate a badge number if enabled in VMS Settings.

        `update_status=True` means this was called from the items-verification flow
        (Security Log) — move the pass to "Items Verified".
        `update_status=False` means called from on_submit — keep status as "Approved".

        The items-verification path may only run against a pass the workflow
        actually approved. `sync_badge_number` checks this too; repeating it here
        is deliberate defence in depth, because the `db_set` below writes
        `status` straight past validation, and any future caller would otherwise
        inherit the same hole. on_submit is unaffected — it passes
        `update_status=False`, and it is itself what moves the document to
        docstatus 1.
        """
        if self.badge_number:
            return

        if update_status and not (
            cint(self.docstatus) == 1 and self.workflow_state in APPROVED_STATES
        ):
            frappe.throw(
                _("Visitor Pass {0} is not approved (currently {1}), so no badge can be issued.").format(
                    self.name, self.workflow_state or _("Draft")
                ),
                frappe.PermissionError,
            )

        # Check VMS Settings — is badge enabled for this visitor type?
        settings = frappe.get_cached_doc("VMS Settings")
        if not getattr(settings, "enable_badge", 1):
            return

        # Whether this type gets a physical badge is a flag on the Visitor Type.
        # (The legacy `badge_required_for` free-text list used a substring match, so
        # a type named "VI" wrongly matched "VIP". It is still honoured when the
        # type predates the flag, but split into exact lines.)
        visitor_type_doc = _get_visitor_type_doc(self.visitor_type)
        if visitor_type_doc is not None:
            if not cint(getattr(visitor_type_doc, "requires_badge", 1)):
                return
        else:
            legacy = (getattr(settings, "badge_required_for", "") or "").strip()
            if legacy and self.visitor_type not in [t.strip() for t in legacy.splitlines() if t.strip()]:
                return

        p = getattr(visitor_type_doc, "badge_prefix", None) or "VIS"

        # Count and stamp against this pass's own visit date — a future-dated pass
        # must not be numbered against today's sequence. The current pass is already
        # persisted by the time a badge is minted, so exclude it: counting it made
        # the first badge of the day come out as -0002.
        visit_date = str(self.visit_date or today())
        date_str = visit_date.replace("-", "")

        # A badge number is a physical access identifier: two visitors holding the
        # same one is a safety problem, not a cosmetic one. Counting rows and
        # adding 1 is a read-then-write race — two passes approved in the same
        # moment both counted the same total and both minted the same number
        # (this site already contains such a pair). `get_series_next` is Frappe's
        # atomic counter: it increments in the database under a row lock, so each
        # caller gets a distinct value.
        prefix = f"{p}-{date_str}-"
        badge_no = f"{prefix}{getseries(prefix, 4)}"

        self.db_set("badge_number", badge_no)
        if update_status:
            self.db_set("status", "Items Verified")

        frappe.msgprint(
            _("Badge Number Generated: {0}").format(badge_no),
            alert=True,
            indicator="green",
        )

    # ─────────────────────────────────────────────────────────
    # PRIVATE: GENERATE QR CODE
    # ─────────────────────────────────────────────────────────
    def _generate_qr_code(self):
        # Match keys used in visitor_gate.py (scan_qr_checkin), which identifies
        # the pass by PASS / VISITOR+VISIT_DATE only. We deliberately do NOT embed
        # the ID proof number or host in the QR: the QR image is emailed and could
        # be intercepted/leaked, and the gate re-derives those values from the DB.
        qr_data = (
            f"PASS:{self.name}"
            f"|VISITOR:{self.visitor_full_name}"
            f"|VISIT_DATE:{self.visit_date}"
        )

        qr_img = qrcode.make(qr_data)
        buffer = BytesIO()
        qr_img.save(buffer, format="PNG")
        qr_content = buffer.getvalue()

        # Cleanup existing QR files for this record
        frappe.db.delete("File", {
            "attached_to_doctype": "Visitor Pass",
            "attached_to_name": self.name,
            "attached_to_field": "qr_code_image"
        })

        file_doc = frappe.get_doc({
            "doctype": "File",
            "file_name": f"QR_{self.name}.png",
            "attached_to_doctype": "Visitor Pass",
            "attached_to_name": self.name,
            "attached_to_field": "qr_code_image",
            "content": qr_content,
            # Private: the QR is attached to the pass (staff with read access can
            # still view it) but is no longer world-readable in /files. It is
            # emailed to the visitor as an attachment, so this does not break delivery.
            "is_private": 1,
        })

        file_doc.insert(ignore_permissions=True)
        self.db_set("qr_code_image", file_doc.file_url)
        return file_doc.file_url, qr_content

    # ─────────────────────────────────────────────────────────
    # PRIVATE: SEND EMAIL
    # ─────────────────────────────────────────────────────────
    def _send_approval_email(self, qr_file_url, qr_content=None):
        if not self.email_id:
            return

        items_text = ""
        items_section = ""
        if self.visitor_items:
            items_section = (
                "<h3 style='margin: 16px 0 6px; font-size: 14px;'>Items Declared</h3>"
                "<ul style='margin: 0 0 12px 20px; padding: 0;'>"
            )
            for item in self.visitor_items:
                qty = getattr(item, 'quantity', 1)
                items_section += f"<li>{item.item_name} (Qty: {qty})</li>"
            items_section += "</ul>"

        time_value = ""
        if self.expected_checkin and self.expected_checkout:
            time_value = f"{self.expected_checkin} &ndash; {self.expected_checkout}"

        td_label = "padding: 8px; border: 1px solid #ddd; width: 30%;"
        td_value = "padding: 8px; border: 1px solid #ddd;"

        details_rows = [
            ("Date", self.visit_date or ""),
            ("Time", time_value),
            # The visitor reading this has no idea what "HR-EMP-00001" means, and
            # it leaks an internal identifier outside the organisation. Show who
            # they are actually meeting, and how to reach them.
            ("Host", self._host_display()),
            ("Purpose", self.purpose_of_visit or ""),
            ("Pass ID", self.name or ""),
        ]
        # Where to go is the single most useful thing a visitor needs on arrival,
        # and the site already configured it per Visitor Type.
        arrival_gate = self._arrival_gate()
        if arrival_gate:
            details_rows.insert(2, ("Entry Gate", arrival_gate))
        details_html = (
            "<table style='border-collapse: collapse; width: 100%; margin: 0 0 12px 0;'>"
        )
        for i, (label, value) in enumerate(details_rows):
            bg = "background: #f4f5f7;" if i % 2 == 0 else ""
            details_html += (
                f"<tr style='{bg}'>"
                f"<td style='{td_label}'><b>{label}</b></td>"
                f"<td style='{td_value}'>{value}</td>"
                f"</tr>"
            )
        details_html += "</table>"

        contractor_li = ""
        if (self.visitor_type_layout or "") == "Contractor":
            contractor_li = (
                "<li>Ensure you have completed the required safety induction and are "
                "wearing provided PPE.</li>"
            )

        attachments = []
        if qr_content:
            attachments.append({
                "fname": f"QR_{self.name}.png",
                "fcontent": qr_content
            })

        self._send_approval_mail(details_html, items_section, contractor_li, attachments)

    def _arrival_gate(self):
        """The gate this visitor should actually report to.

        Every Visitor Type names its own entry point — a VIP is met at the
        executive lobby, a contractor at the goods entrance — and the gate
        officer's Security Log already auto-assigns from this same field. So the
        site has already decided where each visitor goes; telling them all to
        find "the security gate" throws that away and sends a VIP to the wrong
        door.
        """
        if not self.visitor_type:
            return None
        return frappe.db.get_value("Visitor Type", self.visitor_type, "default_gate") or None

    def _host_display(self):
        """The host as a person: name, and email when we have one.

        `person_to_visit` stores an Employee ID. It is the correct value to keep
        on the record, but it is the wrong thing to print in a message — a
        visitor cannot act on "HR-EMP-00001", and it exposes an internal
        identifier to an external recipient.

        Falls back through name, then email, then the ID, so the row is never
        blank on an Employee with incomplete data.
        """
        name = (self.host_name or "").strip()
        email = (self.host_email or "").strip()

        if not name and self.person_to_visit:
            name = frappe.db.get_value("Employee", self.person_to_visit, "employee_name") or ""

        if name and email:
            return f"{name} ({email})"
        return name or email or (self.person_to_visit or "")

    def _send_approval_mail(self, details_html, items_section, contractor_li, attachments):
        """Deliver the visitor's approval mail.

        Wrapped so a missing/misconfigured Email Account cannot block the
        approval itself — without this, `on_submit` raises "Please setup default
        outgoing Email Account" and the whole workflow transition rolls back, so
        no pass can ever be approved on a site that has not configured SMTP yet.
        Matches how the food-dept and host-arrival notifications already behave.
        """
        try:
            self._deliver_approval_mail(details_html, items_section, contractor_li, attachments)
        except Exception as exc:
            frappe.log_error(
                f"Approval email failed for {self.name}: {exc}", "VMS Approval Email"
            )

    def _deliver_approval_mail(self, details_html, items_section, contractor_li, attachments):
        # Name the gate the visitor is actually expected at. Falls back to the
        # generic wording only when a Visitor Type has no default gate set, so a
        # half-configured site still gets a sensible sentence.
        arrival_gate = self._arrival_gate()
        gate_phrase = f"<b>{arrival_gate}</b>" if arrival_gate else "the security gate"
        badge_phrase = (
            f"at the security desk at <b>{arrival_gate}</b>"
            if arrival_gate
            else "at the security desk"
        )

        frappe.sendmail(
            recipients=[self.email_id],
            # Without a reference, frappe.sendmail leaves no Communication behind,
            # so an approval that really was emailed shows nothing in the pass's
            # Activity timeline and there is no record of what went to whom. The
            # send itself worked; it was simply unauditable after the fact.
            reference_doctype=self.doctype,
            reference_name=self.name,
            subject=f"Visit Approved: {self.visit_date} — Pass {self.name}",
            message=(
                f"<div style='font-family: Arial, sans-serif; font-size: 14px; color: #1f2933; line-height: 1.5;'>"
                f"<p>Dear <b>{self.visitor_full_name}</b>,</p>"
                f"<p>Your visit has been <b style='color: #28a745;'>APPROVED</b>.</p>"
                f"<h3 style='margin: 16px 0 6px; font-size: 14px;'>Visit Details</h3>"
                f"{details_html}"
                f"{items_section}"
                f"<h3 style='margin: 16px 0 6px; font-size: 14px;'>On Arrival</h3>"
                f"<ul style='margin: 0 0 12px 20px; padding: 0;'>"
                f"<li>Please carry a valid photo ID matching the one you registered with.</li>"
                f"<li>Scan the <b>QR code attached to this email</b> at {gate_phrase}.</li>"
                f"<li>Your physical badge will be issued {badge_phrase} after item verification.</li>"
                f"{contractor_li}"
                f"</ul>"
                f"<p>We look forward to welcoming you.</p>"
                f"<p style='margin-top: 20px; color: #64748b; font-size: 12px;'>"
                f"This is an automated email. Please contact your host for any changes or queries."
                f"</p>"
                f"</div>"
            ),
            attachments=attachments,
        )

    # ─────────────────────────────────────────────────────────
    # PRIVATE: NOTIFY FOOD DEPT
    # ─────────────────────────────────────────────────────────
    def _notify_food_dept(self):
        """Tell the kitchen what they actually need to serve this visitor.

        This used to send only "Meal Type: Lunch / Visitor Pass: VP-…", which
        does not let anyone prepare anything: no headcount, no service time, and
        — the reason this matters — no allergies. `dietary_allergies` is a
        visible, editable field on the Hospitality Request, so a host can record
        "severe nut allergy" in good faith and it would reach nobody. A form that
        asks and then discards the answer is worse than one that never asked.
        """
        food_email = frappe.db.get_single_value("VMS Settings", "food_dept_email")
        if not food_email:
            return

        allergies = accessibility = None
        if self.hospitality_request:
            allergies, accessibility = frappe.db.get_value(
                "Hospitality Request",
                self.hospitality_request,
                ["dietary_allergies", "accessibility_requirements"],
            ) or (None, None)

        rows = [
            ("Visitor", self.visitor_full_name),
            ("Visit Date", self.visit_date),
            ("Meal", self.meal_type),
            ("Serving Slots", self.assigned_meal_slots),
            ("Service Time", self.service_time),
            ("Number of People", self.number_of_people or 1),
            ("Dietary Preference", self.special_diet),
            ("Allergies", allergies),
            ("Accessibility", accessibility),
            ("Notes", self.hospitality_notes),
            ("Visitor Pass", self.name),
        ]
        body = "".join(
            # Allergies are the one line a cook must not miss, so it is called out
            # rather than left to blend into the table.
            f"<tr><td style='padding:6px 10px;border:1px solid #ddd;'><b>{label}</b></td>"
            f"<td style='padding:6px 10px;border:1px solid #ddd;"
            f"{'color:#b42318;font-weight:bold;' if label == 'Allergies' and value else ''}'>"
            f"{value}</td></tr>"
            for label, value in rows
            if value not in (None, "")
        )

        try:
            frappe.sendmail(
                recipients=[food_email],
                reference_doctype=self.doctype,
                reference_name=self.name,
                subject=f"Meal Required: {self.visitor_full_name} — {self.visit_date}",
                message=(
                    "<div style='font-family:Arial,sans-serif;font-size:13px;color:#1f2933;"
                    "background-color:#ffffff;padding:16px;border-radius:6px;'>"
                    "<h3 style='margin:0 0 10px;color:#102a43;'>Meal Request for Visitor</h3>"
                    "<table style='border-collapse:collapse;width:100%;'>"
                    f"{body}"
                    "</table></div>"
                ),
            )
        except Exception as exc:
            frappe.log_error(f"Food dept notification failed for {self.name}: {exc}", "VMS Food Dept Notification")

@frappe.whitelist()
def search_existing_by_phone(phone):
    if not phone:
        return []
    # frappe.get_list (unlike raw SQL / get_all) enforces the Visitor Pass
    # row-level permission model (visitormanagement/permissions.py), so a caller
    # can only find passes they are authorised to read — no cross-visitor PII scan.
    return frappe.get_list(
        "Visitor Pass",
        filters={"mobile_number": phone},
        fields=["name", "visitor_full_name", "visitor_type"],
        order_by="creation desc",
        limit=10,
    )

@frappe.whitelist()
def search_existing_by_id(id_number):
    if not id_number:
        return []
    return frappe.get_list(
        "Visitor Pass",
        filters={"id_proof_number": id_number},
        fields=["name", "visitor_full_name", "visitor_type"],
        order_by="creation desc",
        limit=10,
    )


def _normalized_digits(value):
    return "".join(ch for ch in (value or "") if ch.isdigit())


@frappe.whitelist()
def get_existing_visitor_matches(visitor_type=None, id_proof_number=None, mobile_number=None, exclude_name=None):
    id_proof_number = (id_proof_number or "").strip()
    mobile_number = (mobile_number or "").strip()
    exclude_name = (exclude_name or "").strip()
    visitor_type = (visitor_type or "").strip()

    if not id_proof_number and not mobile_number:
        return {"best_match": None, "matches": []}

    # Authorisation: require doctype read, then constrain rows to the caller's
    # Visitor Pass permission scope so this cannot enumerate other visitors' PII.
    frappe.has_permission("Visitor Pass", "read", throw=True)
    from visitormanagement.permissions import get_visitor_pass_permission_query_conditions

    _perm = get_visitor_pass_permission_query_conditions()
    perm_filter = f" AND ({_perm})" if _perm else ""

    matches = []
    seen = set()

    def _push(rows):
        for row in rows:
            if row.name in seen:
                continue
            seen.add(row.name)
            matches.append(row)

    type_filter = "AND visitor_type = %(visitor_type)s" if visitor_type else ""
    exclude_filter = "AND name != %(exclude_name)s" if exclude_name else ""
    params = {
        "visitor_type": visitor_type,
        "exclude_name": exclude_name,
        "id_proof_number": id_proof_number,
    }

    if id_proof_number:
        by_id = frappe.db.sql(
            """
            SELECT name, visitor_full_name, visitor_type, mobile_number, id_proof_number
            FROM `tabVisitor Pass`
            WHERE id_proof_number = %(id_proof_number)s
            """
            + type_filter
            + exclude_filter
            + perm_filter
            + """
            ORDER BY modified DESC
            LIMIT 10
            """,
            params,
            as_dict=True,
        )
        _push(by_id)

    if mobile_number and len(matches) < 10:
        phone_digits = _normalized_digits(mobile_number)
        # Match on the stored digit form in SQL rather than fetching rows and
        # comparing in Python.
        #
        # This query used to select any pass with a non-empty mobile_number,
        # `ORDER BY modified DESC LIMIT 100`, and compare digits afterwards. It
        # had no phone predicate at all, so it only ever examined the 100
        # most-recently-touched passes on the site: past roughly that many rows
        # the duplicate check stopped finding anyone who last visited more than
        # a few hours ago, and returned "no match" — a silent wrong answer at
        # the gate rather than a visible failure. `mobile_digits` is maintained
        # in before_save and indexed, so the match is now both complete and a
        # single indexed lookup.
        if phone_digits:
            by_phone = frappe.db.sql(
                """
                SELECT name, visitor_full_name, visitor_type, mobile_number, id_proof_number
                FROM `tabVisitor Pass`
                WHERE mobile_digits = %(phone_digits)s
                """
                + type_filter
                + exclude_filter
                + perm_filter
                + """
                ORDER BY modified DESC
                LIMIT 10
                """,
                {
                    "visitor_type": visitor_type,
                    "exclude_name": exclude_name,
                    "phone_digits": phone_digits,
                },
                as_dict=True,
            )

            # Pushed one at a time to keep the original cap: `_push` dedupes
            # against `seen` but does not bound the list.
            for row in by_phone:
                _push([row])
                if len(matches) >= 10:
                    break

    return {"best_match": matches[0] if matches else None, "matches": matches}


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def search_visitor_passes(doctype, txt, searchfield, start, page_len, filters):
    frappe.has_permission("Visitor Pass", "read", throw=True)
    filters = filters or {}
    visitor_type = filters.get("visitor_type")

    conditions = []
    params = []
    if visitor_type:
        conditions.append("visitor_type = %s")
        params.append(visitor_type)
    if txt:
        like = f"%{txt}%"
        conditions.append(
            "(name LIKE %s OR visitor_full_name LIKE %s OR mobile_number LIKE %s OR email_id LIKE %s OR visitor_summary LIKE %s)"
        )
        params.extend([like] * 5)

    where = " AND ".join(conditions) if conditions else "1=1"
    # Constrain rows to the caller's Visitor Pass permission scope.
    from visitormanagement.permissions import get_visitor_pass_permission_query_conditions

    _perm = get_visitor_pass_permission_query_conditions()
    if _perm:
        where += f" AND ({_perm})"
    # Compute the dropdown description live (visitor_full_name · mobile_number) so
    # reception can spot + search by phone, regardless of how stale the cached
    # `visitor_summary` is on legacy/demo records.
    # Compute the dropdown description live (visitor_full_name · mobile_number) so reception
    # can spot + search by phone, regardless of how stale the cached visitor_summary is.

    return frappe.db.sql(
        """SELECT
                name,
                CONCAT_WS(' · ', NULLIF(visitor_full_name, ''), NULLIF(mobile_number, '')) AS description
            FROM `tabVisitor Pass`
            WHERE """
        + where
        + """
            ORDER BY modified DESC
            LIMIT %s OFFSET %s""",
        params + [int(page_len), int(start)],
    )


@frappe.whitelist()
def get_existing_visitor_pass_details(visitor_pass, visitor_type=None):
    if not visitor_pass:
        frappe.throw("Visitor Pass is required.")

    doc = frappe.get_doc("Visitor Pass", visitor_pass)
    # IDOR guard: this returns ID proof number/scan + photo. Enforce the app's
    # own row-level access model (the same model used by the search endpoints and
    # list views) so a user cannot fetch a pass outside their scope. We use the
    # app's hook rather than doc.check_permission() so this stays consistent with
    # the search results and is not skewed by deployment-specific User Permissions.
    from visitormanagement.permissions import has_visitor_pass_permission

    if frappe.session.user != "Administrator" and not has_visitor_pass_permission(
        doc, frappe.session.user, "read"
    ):
        raise frappe.PermissionError("You are not permitted to access this Visitor Pass.")
    if visitor_type and doc.visitor_type != visitor_type:
        frappe.throw("Selected record type does not match current Visitor Type.")

    common_fields = [
        "name",
        "visitor_type",
        "visitor_type_layout",
        "visitor_full_name",
        "mobile_number",
        "email_id",
        "company__organisation",
        "id_proof_type",
        "id_proof_number",
        "id_proof_scan",
        "visitor_photo",
        "purpose_of_visit",
        "person_to_visit",
        "host_department",
        "visit_date",
        "expected_checkin",
        "expected_checkout",
    ]

    fields = common_fields + LAYOUT_FIELDS.get(doc.visitor_type_layout or "", [])
    return {field: doc.get(field) for field in fields}


@frappe.whitelist()
def sync_badge_number(visitor_pass):
    """Generate badge number for a visitor pass if not already set.

    Issuing a badge also moves the pass to "Items Verified", so this is a write
    on the gate workflow — not a read. Only staff who may record a gate event
    (Security / System Manager, i.e. `create` on Security Log) can call it.
    `frappe.get_doc` performs no permission check of its own, so without this
    guard any authenticated user could mint a badge for any pass and push it
    into "Items Verified".
    """
    if not frappe.has_permission("Security Log", "create"):
        frappe.throw(
            _("You are not permitted to issue visitor badges. Security role required."),
            frappe.PermissionError,
        )

    vp = frappe.get_doc("Visitor Pass", visitor_pass)
    if vp.badge_number:
        return vp.badge_number

    # Minting a badge also advances the pass to "Items Verified", which is one
    # of the two states Security Log's check-in gate accepts — so an unguarded
    # call here hands the gate a pass the workflow never approved. The client
    # fires this the moment a pass is selected on a new Security Log, so the
    # caller need not save anything, and a deep link or QR scan reaches passes
    # the link dropdown would never have offered.
    #
    # Corroborate against what the workflow engine actually recorded, exactly as
    # visitor_gate.visitor_checkin does, rather than trusting `status` alone:
    # `status` is written here with db_set, which skips validation entirely.
    if not (cint(vp.docstatus) == 1 and vp.workflow_state in APPROVED_STATES):
        frappe.throw(
            _("Visitor Pass {0} is not approved (currently {1}), so no badge can be issued.").format(
                visitor_pass, vp.workflow_state or _("Draft")
            ),
            frappe.PermissionError,
        )

    vp.generate_badge_number()
    vp.reload()
    return vp.badge_number
