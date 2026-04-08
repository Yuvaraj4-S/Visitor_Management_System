# Copyright (c) 2026, Harthesh and contributors
# For license information, please see license.txt

import frappe


VISITOR_TYPES = ("Contractor", "Supplier", "Candidate", "Customer", "VIP")


def execute(filters=None):
	filters = filters or {}

	columns = get_columns()
	data = get_data(filters)
	report_summary = get_report_summary(data)
	chart = get_chart(data)

	return columns, data, None, chart, report_summary


def get_columns():
	return [
		{"label": "Primary Pass", "fieldname": "primary_pass", "fieldtype": "Link", "options": "Visitor Pass", "width": 140},
		{"label": "Primary Type", "fieldname": "primary_type", "fieldtype": "Data", "width": 110},
		{"label": "Primary Visitor", "fieldname": "primary_visitor", "fieldtype": "Data", "width": 180},
		{"label": "Primary Visit Date", "fieldname": "primary_visit_date", "fieldtype": "Date", "width": 115},
		{"label": "Matched Pass", "fieldname": "matched_pass", "fieldtype": "Link", "options": "Visitor Pass", "width": 140},
		{"label": "Matched Type", "fieldname": "matched_type", "fieldtype": "Data", "width": 110},
		{"label": "Matched Visitor", "fieldname": "matched_visitor", "fieldtype": "Data", "width": 180},
		{"label": "Matched Visit Date", "fieldname": "matched_visit_date", "fieldtype": "Date", "width": 115},
		{"label": "Match Scope", "fieldname": "match_scope", "fieldtype": "Data", "width": 110},
		{"label": "Match Basis", "fieldname": "match_basis", "fieldtype": "Data", "width": 160},
		{"label": "ID Proof", "fieldname": "id_proof_number", "fieldtype": "Data", "width": 140},
		{"label": "Mobile", "fieldname": "mobile_number", "fieldtype": "Data", "width": 130},
		{"label": "Email", "fieldname": "email_id", "fieldtype": "Data", "width": 180},
		{"label": "Primary Status", "fieldname": "primary_status", "fieldtype": "Data", "width": 120},
		{"label": "Matched Status", "fieldname": "matched_status", "fieldtype": "Data", "width": 120},
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

	for basis, groups in indexes.items():
		for key, rows in groups.items():
			if not key or len(rows) < 2:
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
	return data


def get_records(filters):
	conditions = ["visitor_type in %(visitor_types)s"]
	values = {"visitor_types": VISITOR_TYPES}

	if filters.get("from_date"):
		conditions.append("visit_date >= %(from_date)s")
		values["from_date"] = filters["from_date"]

	if filters.get("to_date"):
		conditions.append("visit_date <= %(to_date)s")
		values["to_date"] = filters["to_date"]

	if filters.get("visitor_type"):
		conditions.append("visitor_type = %(visitor_type)s")
		values["visitor_type"] = filters["visitor_type"]

	where_clause = " AND ".join(conditions)

	return frappe.db.sql(
		f"""
		SELECT
			name,
			visitor_type,
			visitor_full_name,
			visit_date,
			id_proof_number,
			mobile_number,
			email_id,
			status
		FROM `tabVisitor Pass`
		WHERE {where_clause}
		ORDER BY visit_date DESC, modified DESC
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


def get_report_summary(data):
	same_type = sum(1 for row in data if row["match_scope"] == "Same Type")
	different_type = sum(1 for row in data if row["match_scope"] == "Different Type")

	return [
		{"value": len(data), "label": "Matched Pairs", "indicator": "Blue"},
		{"value": same_type, "label": "Same Type", "indicator": "Green"},
		{"value": different_type, "label": "Different Type", "indicator": "Orange"},
	]


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

