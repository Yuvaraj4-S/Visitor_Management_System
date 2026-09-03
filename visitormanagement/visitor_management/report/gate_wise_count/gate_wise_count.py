# For license information, please see license.txt

import frappe
from frappe.utils import today


def execute(filters=None):
	filters = filters or {}
	columns = get_columns()
	data = get_data(filters)
	summary = get_summary(data)
	chart = get_chart(data)
	return columns, data, None, chart, summary


def get_columns():
	return [
		{"label": "Gate", "fieldname": "gate_name", "fieldtype": "Data", "width": 180},
		{"label": "Check-Ins", "fieldname": "checkins", "fieldtype": "Int", "width": 110},
		{"label": "Check-Outs", "fieldname": "checkouts", "fieldtype": "Int", "width": 110},
		{"label": "Currently Inside", "fieldname": "inside", "fieldtype": "Int", "width": 140},
		{"label": "Items Pending", "fieldname": "pending_verify", "fieldtype": "Int", "width": 130},
		{"label": "% of On-Site", "fieldname": "occupancy_pct", "fieldtype": "Percent", "width": 110},
	]


def get_data(filters):
	date_val = filters.get("date") or today()

	# The report has always shown one row per gate that has ever recorded a
	# Security Log event — including a zero row for a gate with no traffic on
	# the selected date — not just the gates active that particular day.
	# Pushing the date straight into the aggregate query's WHERE (below) would
	# silently drop any gate with zero matching rows for that date, quietly
	# turning "gate had no traffic today" into "gate doesn't exist" — a
	# different, wrong answer, not just a faster one. So the gate universe is
	# fetched separately, unfiltered by date: an index-only scan on gate_name,
	# cheap regardless of table size.
	gate_rows = frappe.db.sql(
		"""
        SELECT DISTINCT sl.gate_name
        FROM `tabSecurity Log` sl
        WHERE sl.gate_name IS NOT NULL AND sl.gate_name != ''
        """,
		as_dict=True,
	)
	gates = [r.gate_name for r in gate_rows]

	# The date is pushed into WHERE as a half-open range on the *unwrapped*
	# datetime column (`check_in_date_time`/`check_out_date_time`), never
	# `DATE(col) = %(d)s` — wrapping the column in a function makes the
	# comparison non-sargable and forces a full scan of Security Log no
	# matter how narrow the requested day is. A composite index on
	# (event_type, check_in_date_time) (or check_out_date_time) can only be
	# used when the column itself is compared directly, which this now does.
	count_rows = frappe.db.sql(
		"""
        SELECT
            sl.gate_name,
            SUM(CASE WHEN sl.event_type='Check-In' THEN 1 ELSE 0 END) AS checkins,
            SUM(CASE WHEN sl.event_type='Check-Out' THEN 1 ELSE 0 END) AS checkouts,
            SUM(CASE WHEN sl.event_type='Check-In' AND sl.all_items_confirmed=0
                THEN 1 ELSE 0 END) AS pending_verify
        FROM `tabSecurity Log` sl
        WHERE sl.gate_name IS NOT NULL AND sl.gate_name != ''
            AND (
                (sl.event_type = 'Check-In'
                    AND sl.check_in_date_time >= %(d)s
                    AND sl.check_in_date_time < %(d)s + INTERVAL 1 DAY)
                OR
                (sl.event_type = 'Check-Out'
                    AND sl.check_out_date_time >= %(d)s
                    AND sl.check_out_date_time < %(d)s + INTERVAL 1 DAY)
            )
        GROUP BY sl.gate_name
        """,
		{"d": date_val},
		as_dict=True,
	)
	counts_by_gate = {r.gate_name: r for r in count_rows}

	rows = []
	for gate in gates:
		c = counts_by_gate.get(gate)
		rows.append(
			{
				"gate_name": gate,
				"checkins": c.checkins if c else 0,
				"checkouts": c.checkouts if c else 0,
				"pending_verify": c.pending_verify if c else 0,
			}
		)
	rows.sort(key=lambda r: r["checkins"], reverse=True)

	# "Currently Inside" is live occupancy, not a same-day figure — a visitor
	# who checked in yesterday and has not checked out is still on site today.
	# `checkins - checkouts` on the *selected date* silently zeroed this out for
	# every gate whenever the day's own traffic didn't happen to include that
	# backlog. Instead, count each still-open Visitor Pass at the gate of its
	# own (latest) Check-In event — the same definition Active Visitors' own
	# "Currently Inside" summary uses — so the two never disagree.
	#
	# This used to be a correlated MAX() subquery re-run once per outer row
	# (EXPLAIN showed both sides type=ALL, effectively O(n^2) over Security
	# Log). It is now a derived table that computes the latest Check-In per
	# visitor_pass in a single grouped pass, then joins back once — Security
	# Log is scanned a constant number of times regardless of row count.
	inside_rows = frappe.db.sql(
		"""
        SELECT sl.gate_name, COUNT(*) AS inside
        FROM `tabVisitor Pass` vp
        INNER JOIN (
            SELECT visitor_pass, MAX(check_in_date_time) AS latest_checkin
            FROM `tabSecurity Log`
            WHERE event_type = 'Check-In'
            GROUP BY visitor_pass
        ) latest ON latest.visitor_pass = vp.name
        INNER JOIN `tabSecurity Log` sl
            ON sl.visitor_pass = vp.name
            AND sl.event_type = 'Check-In'
            AND sl.check_in_date_time = latest.latest_checkin
        WHERE vp.status = 'Checked-In'
            AND sl.gate_name IS NOT NULL AND sl.gate_name != ''
        GROUP BY sl.gate_name
        """,
		as_dict=True,
	)
	inside_by_gate = {r.gate_name: r.inside for r in inside_rows}

	for r in rows:
		r["inside"] = inside_by_gate.get(r["gate_name"], 0)

	# This percentage used to be `inside / checkins`, which divided a LIVE,
	# all-date occupancy count by a count of check-ins on the SELECTED DATE —
	# two different scopes, so the result meant nothing in either direction.
	# Observed on screen: Loading Dock showed Currently Inside 1 but 0.00%,
	# because nobody happened to check in there on the chosen date; the mirror
	# case, one check-in today against five people still on site from earlier
	# days, would have read 500%.
	#
	# Both sides are now the live figure: of everyone currently in the building,
	# what share came through this gate. That is a question the number can
	# actually answer, it is consistent with the "Currently Inside" column
	# beside it (which is deliberately live rather than same-day — see above),
	# and it always totals 100% across the gates.
	total_inside = sum(r["inside"] for r in rows)
	for r in rows:
		r["occupancy_pct"] = round((r["inside"] / total_inside) * 100, 1) if total_inside else 0
	return rows


def get_summary(data):
	total_in = sum(r.get("checkins") or 0 for r in data)
	total_out = sum(r.get("checkouts") or 0 for r in data)
	currently_inside = sum(r.get("inside") or 0 for r in data)
	pending = sum(r.get("pending_verify") or 0 for r in data)

	return [
		{"value": total_in, "label": "Total Check-Ins", "indicator": "Green"},
		{"value": total_out, "label": "Total Check-Outs", "indicator": "Grey"},
		{"value": currently_inside, "label": "Total Currently Inside", "indicator": "Blue"},
		{"value": pending, "label": "Total Items Pending", "indicator": "Orange"},
	]


def get_chart(data):
	if not data:
		return None
	return {
		"type": "bar",
		"data": {
			"labels": [r["gate_name"] for r in data],
			"datasets": [
				{"name": "Check-Ins", "values": [r.get("checkins") or 0 for r in data]},
				{"name": "Currently Inside", "values": [r.get("inside") or 0 for r in data]},
			],
		},
		"colors": ["#28a745", "#007bff"],
	}
