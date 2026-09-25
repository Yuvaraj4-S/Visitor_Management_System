# For license information, please see license.txt

import frappe
from frappe import _

# Default and hard-cap window for the mandatory date filter (see _enforce_date_range).
# Measured before this fix: a filterless run pulled all 502,200 Visitor Pass rows
# (7,695 ms) straight into the worker's memory.
DEFAULT_RANGE_DAYS = 30
MAX_RANGE_DAYS = 90

# A duplicate-key group above this size is skipped rather than pair-matched. The
# pairing below is O(k^2): a group of 5,000 (a shared reception placeholder number,
# or a long-running contractor) produces 12.5 million dicts and OOMs the worker
# before the query even times out. The current data already has a mobile number
# shared by 15 passes, so this is a real, not hypothetical, shape.
MAX_GROUP_SIZE = 200


def _visitor_types():
	"""Active Visitor Types, read from the master rather than a frozen tuple."""
	return frappe.get_all("Visitor Type", filters={"is_active": 1}, pluck="name", order_by="name asc")


def execute(filters=None):
	filters = filters or {}
	_enforce_date_range(filters)

	columns = get_columns()
	data, skipped_groups = get_data(filters)
	report_summary = get_report_summary(data, skipped_groups)
	chart = get_chart(data)

	_notify_skipped_groups(skipped_groups)

	return columns, data, None, chart, report_summary


def _enforce_date_range(filters):
	"""Make the date range mandatory and bounded, in the server, not just the UI.

	The report's `.js` marks from_date/to_date `reqd: 1`, but that is a UI
	convenience only — it does nothing for a direct call to `execute()` (e.g. from
	the report API, a console, or a future caller) with empty filters. That direct
	call is the actual attack/DoS path, so the boundary has to live here: default
	an empty range to the last DEFAULT_RANGE_DAYS days, then clamp anything wider
	than MAX_RANGE_DAYS days back down, so the underlying query can never scan the
	whole `tabVisitor Pass` table regardless of how it was invoked.
	"""
	today = frappe.utils.getdate(frappe.utils.nowdate())

	to_date = frappe.utils.getdate(filters["to_date"]) if filters.get("to_date") else today
	from_date = (
		frappe.utils.getdate(filters["from_date"])
		if filters.get("from_date")
		else frappe.utils.add_days(to_date, -DEFAULT_RANGE_DAYS)
	)

	if from_date > to_date:
		from_date, to_date = to_date, from_date

	if (to_date - from_date).days > MAX_RANGE_DAYS:
		clamped_from = frappe.utils.add_days(to_date, -MAX_RANGE_DAYS)
		frappe.msgprint(
			_(
				"The date range was capped to the last {0} days ({1} to {2}) so this report cannot scan "
				"the entire Visitor Pass table. Narrow the range further if you need fewer rows."
			).format(MAX_RANGE_DAYS, clamped_from, to_date),
			title=_("Date Range Capped"),
			indicator="orange",
		)
		from_date = clamped_from

	filters["from_date"] = str(from_date)
	filters["to_date"] = str(to_date)


def get_columns():
	return [
		{"label": _("Type Comparison"), "fieldname": "match_scope", "fieldtype": "Data", "width": 130},
		{"label": _("Matched On"), "fieldname": "match_basis", "fieldtype": "Data", "width": 150},
		{"label": _("Primary Pass"), "fieldname": "primary_pass", "fieldtype": "Link", "options": "Visitor Pass", "width": 130},
		{"label": _("Primary Visitor"), "fieldname": "primary_visitor", "fieldtype": "Data", "width": 170},
		{"label": _("Primary Type"), "fieldname": "primary_type", "fieldtype": "Data", "width": 105},
		{"label": _("Primary Visit"), "fieldname": "primary_visit_date", "fieldtype": "Date", "width": 105},
		{"label": _("Matched Pass"), "fieldname": "matched_pass", "fieldtype": "Link", "options": "Visitor Pass", "width": 130},
		{"label": _("Matched Visitor"), "fieldname": "matched_visitor", "fieldtype": "Data", "width": 170},
		{"label": _("Matched Type"), "fieldname": "matched_type", "fieldtype": "Data", "width": 105},
		{"label": _("Matched Visit"), "fieldname": "matched_visit_date", "fieldtype": "Date", "width": 105},
		{"label": _("ID Proof"), "fieldname": "id_proof_number", "fieldtype": "Data", "width": 140},
		{"label": _("Mobile"), "fieldname": "mobile_number", "fieldtype": "Data", "width": 130},
		{"label": _("Email"), "fieldname": "email_id", "fieldtype": "Data", "width": 200},
	]


def get_data(filters):
	records = get_records(filters)
	pairs = {}
	indexes = {"id": {}, "mobile": {}, "email": {}}

	for record in records:
		if record.id_proof_number:
			indexes["id"].setdefault(record.id_proof_number.strip(), []).append(record)

		mobile_digits = normalize_mobile(record.mobile_number)
		if mobile_digits:
			indexes["mobile"].setdefault(mobile_digits, []).append(record)

		email = (record.email_id or "").strip().lower()
		if email:
			indexes["email"].setdefault(email, []).append(record)

	skipped_groups = []
	for basis, groups in indexes.items():
		for key, rows in groups.items():
			if not key or len(rows) < 2:
				continue
			if len(rows) > MAX_GROUP_SIZE:
				# Do not silently truncate: record it so the caller can tell "no
				# matches" apart from "too many to show" (see _notify_skipped_groups
				# and get_report_summary).
				skipped_groups.append({"basis": display_basis(basis), "size": len(rows)})
				continue
			add_pair_matches(pairs, rows, basis)

	data = []
	for row in pairs.values():
		match_scope = "Same Type" if row["primary_type"] == row["matched_type"] else "Different Type"
		if not match_scope_allowed(filters.get("match_scope"), match_scope):
			continue
		if not matched_type_allowed(filters.get("matched_visitor_type"), row["matched_type"]):
			continue
		row["match_scope"] = match_scope
		row["match_basis"] = ", ".join(sorted(row["match_basis"]))
		data.append(row)

	data.sort(
		key=lambda row: (
			0 if row["match_scope"] == "Different Type" else 1,
			row["primary_visit_date"] or "",
			row["primary_pass"],
			row["matched_pass"],
		),
		reverse=True,
	)
	return data, skipped_groups


def get_records(filters):
	conditions = ["vp.visitor_type in %(visitor_types)s"]
	values = {"visitor_types": _visitor_types()}

	# from_date/to_date are guaranteed present by _enforce_date_range before this
	# runs, but the checks stay conditional (rather than assuming the keys exist)
	# so this function is still safe to call on its own, e.g. from a test.
	if filters.get("from_date"):
		conditions.append("vp.visit_date >= %(from_date)s")
		values["from_date"] = filters["from_date"]

	if filters.get("to_date"):
		conditions.append("vp.visit_date <= %(to_date)s")
		values["to_date"] = filters["to_date"]

	if filters.get("visitor_type"):
		conditions.append("vp.visitor_type = %(visitor_type)s")
		values["visitor_type"] = filters["visitor_type"]

	scope = _visitor_pass_scope("vp")
	if scope:
		conditions.append(scope)

	where_clause = " AND ".join(conditions)

	return frappe.db.sql(
		"""
		SELECT
			vp.name AS name,
			vp.visitor_type,
			vp.visitor_full_name,
			vp.visit_date,
			vp.id_proof_number,
			vp.mobile_number,
			vp.email_id,
			vp.status
		FROM `tabVisitor Pass` vp
		WHERE """
		+ where_clause
		+ """
		ORDER BY vp.visit_date DESC, vp.modified DESC
		""",
		values,
		as_dict=True,
	)


def add_pair_matches(pairs, rows, basis):
	for index, left in enumerate(rows):
		for right in rows[index + 1 :]:
			pair_key = tuple(sorted((left.name, right.name)))
			entry = pairs.get(pair_key)
			if not entry:
				primary, matched = sort_pair(left, right)
				entry = {
					"primary_pass": primary.name,
					"primary_type": primary.visitor_type,
					"primary_visitor": primary.visitor_full_name,
					"primary_visit_date": primary.visit_date,
					"matched_pass": matched.name,
					"matched_type": matched.visitor_type,
					"matched_visitor": matched.visitor_full_name,
					"matched_visit_date": matched.visit_date,
					"id_proof_number": primary.id_proof_number or matched.id_proof_number,
					"mobile_number": primary.mobile_number or matched.mobile_number,
					"email_id": primary.email_id or matched.email_id,
					"primary_status": primary.status,
					"matched_status": matched.status,
					"match_basis": set(),
				}
				pairs[pair_key] = entry
			entry["match_basis"].add(display_basis(basis))


def sort_pair(left, right):
	left_key = (
		left.visit_date or "",
		left.name,
	)
	right_key = (
		right.visit_date or "",
		right.name,
	)
	return (left, right) if left_key >= right_key else (right, left)


def normalize_mobile(value):
	return "".join(ch for ch in (value or "") if ch.isdigit())


def display_basis(basis):
	return {
		"id": "ID Proof",
		"mobile": "Mobile",
		"email": "Email",
	}[basis]


def match_scope_allowed(filter_value, actual_value):
	if not filter_value:
		return True
	return filter_value == actual_value


def matched_type_allowed(filter_value, actual_value):
	if not filter_value:
		return True
	return filter_value == actual_value


def _notify_skipped_groups(skipped_groups):
	"""Tell the user, in the UI, that some duplicate-key groups were too large to
	pair-match — so a report with zero rows because nothing matched is never
	confused with a report with zero rows because the real matches were too many
	to show. Never surfaces the actual ID/mobile/email value, only the basis and
	the group size.
	"""
	if not skipped_groups:
		return

	skipped_groups = sorted(skipped_groups, key=lambda g: g["size"], reverse=True)
	shown = skipped_groups[:5]
	lines = ", ".join(f"{g['basis']} group of {g['size']} passes" for g in shown)
	remainder = len(skipped_groups) - len(shown)
	if remainder:
		lines += _(" and {0} more group(s)").format(remainder)

	frappe.msgprint(
		_(
			"{0} duplicate-key group(s) had more than {1} matching passes and were skipped to avoid "
			"exhausting server memory: {2}. Narrow the date range to see matches within these groups."
		).format(len(skipped_groups), MAX_GROUP_SIZE, lines),
		title=_("Some Matches Not Shown"),
		indicator="orange",
	)


def get_report_summary(data, skipped_groups=None):
	same_type = sum(1 for row in data if row["match_scope"] == "Same Type")
	different_type = sum(1 for row in data if row["match_scope"] == "Different Type")
	skipped_count = len(skipped_groups or [])

	summary = [
		{"value": len(data), "label": _("Matched Pairs"), "indicator": "Blue"},
		{"value": same_type, "label": _("Same Type"), "indicator": "Green"},
		{"value": different_type, "label": _("Different Type"), "indicator": "Orange"},
	]
	# Only shown when non-zero, so a normal run's summary is unchanged from before
	# this fix — this is purely the "too many to show" signal from defect 2b.
	if skipped_count:
		summary.append({"value": skipped_count, "label": _("Groups Skipped (Too Large)"), "indicator": "Red"})
	return summary


def get_chart(data):
	same_type = sum(1 for row in data if row["match_scope"] == "Same Type")
	different_type = sum(1 for row in data if row["match_scope"] == "Different Type")
	return {
		"data": {
			"labels": ["Same Type", "Different Type"],
			"datasets": [{"name": "Matches", "values": [same_type, different_type]}],
		},
		"type": "donut",
	}


def _visitor_pass_scope(alias="vp"):
	"""The caller's Visitor Pass row scope, as a SQL fragment for `alias`.

	Mirrors `_visitor_pass_scope` in the sibling reports
	(report/active_visitors/active_visitors.py and
	report/daily_visitor_log/daily_visitor_log.py) verbatim, on purpose: this
	report was the one Visitor Pass report that never called it, so it leaked
	ID proof numbers, mobile numbers and email addresses for every pass on the
	site to anyone who could open it, with no row-level scoping at all.

	The same function exists in all three reports; a change to one must be made
	in the other two.

	Script reports build their rows with raw SQL, which bypasses
	`permission_query_conditions` entirely — so the row filter the list view
	applies has to be re-applied here by hand. Without it the report is a way to
	read every visitor's ID proof regardless of who you are; the roles on the
	report are the only thing standing in the way, and those are one JSON edit
	from changing.

	The shared helper writes conditions against the real table name, so they are
	rewritten to whatever this report aliased it to.
	"""
	from visitormanagement.permissions import get_visitor_pass_permission_query_conditions

	condition = get_visitor_pass_permission_query_conditions()
	if not condition:
		return None
	return condition.replace("`tabVisitor Pass`", f"`{alias}`")
