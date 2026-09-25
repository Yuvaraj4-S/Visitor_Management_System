# For license information, please see license.txt
"""Custom-type source for the "Checked-In Visitors" Number Card.

`Visitor Pass.status = "Checked-In"` alone is not a reliable definition of
"currently on site": this site has 17 Visitor Pass rows with that status, but
only 10 of them have a matching Security Log Check-In event. The other 7 are
all "Anita Executive", all dated 2026-08-05, all with `actual_checkin` set
directly by seed data with no gate ever recording them entering — no physical
check-in event exists for them at all.

Meanwhile the "Gate Wise Count" report's "Currently Inside" column (see
visitor_management/report/gate_wise_count/gate_wise_count.py) already answers
"who is on site" a different way: a Visitor Pass counts only if its own latest
Security Log Check-In event exists. That report showed 10 for the same data
that this card, on `status` alone, showed 17 — two widgets disagreeing about
the same fact from two different sources of truth.

This card adopts the Security Log trail as the single source of truth so the
two can never disagree again: a person the gate has no record of admitting is
not counted as inside, even if a `status` field says otherwise. The query
below mirrors gate_wise_count.get_data()'s "Currently Inside" join, without
the per-gate grouping.

Visitor Pass rows are read through `frappe.get_list`, so this respects the
same row-level permission scoping as every other Visitor Pass read in this
app. The Security Log existence check that follows is a plain, parameterised
`frappe.db.sql` that reads no Security Log field and returns none to the
caller — it only confirms a Check-In event happened for a pass the caller can
already see, so it does not leak any Security Log content and needs no
`ignore_permissions` justification beyond that.
"""

import frappe


@frappe.whitelist()
def get_data(filters: str | dict | list | None = None, **kwargs) -> dict:
	checked_in_names = frappe.get_list("Visitor Pass", filters={"status": "Checked-In"}, pluck="name")

	if not checked_in_names:
		return {"value": 0}

	count = frappe.db.sql(
		"""
		SELECT COUNT(DISTINCT sl.visitor_pass)
		FROM `tabSecurity Log` sl
		WHERE sl.event_type = 'Check-In'
			AND sl.visitor_pass IN %(names)s
		""",
		{"names": checked_in_names},
	)[0][0]

	return {"value": count or 0}
