# Copyright (c) 2026, Harthesh and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import flt, get_first_day, get_last_day, getdate, nowdate


def execute(filters=None):
	filters = filters or {}
	month_ref = getdate(filters.get("month") or nowdate())
	from_date = get_first_day(month_ref)
	to_date = get_last_day(month_ref)

	columns = [
		{"label": "Service Type", "fieldname": "service", "fieldtype": "Data", "width": 160},
		{"label": "Requests", "fieldname": "count", "fieldtype": "Int", "width": 100},
		{"label": "Total Cost", "fieldname": "total_cost", "fieldtype": "Currency", "width": 150},
		{"label": "Avg Cost", "fieldname": "avg_cost", "fieldtype": "Currency", "width": 150},
	]

	rows = frappe.get_all(
		"Hospitality Request",
		filters={
			"modified": ("between", [from_date, to_date]),
			"status": ("!=", "Cancelled"),
		},
		fields=[
			"hotel_required", "hotel_cost",
			"greeting_required", "greeting_cost",
			"cab_required", "factory_tour_required",
			"buggy_required",
		],
	)

	services = {
		"Hotel": {"count": 0, "total": 0.0, "flag": "hotel_required", "cost_field": "hotel_cost"},
		"Greeting": {"count": 0, "total": 0.0, "flag": "greeting_required", "cost_field": "greeting_cost"},
		"Cab": {"count": 0, "total": 0.0, "flag": "cab_required"},
		"Factory Tour": {"count": 0, "total": 0.0, "flag": "factory_tour_required"},
		"Buggy": {"count": 0, "total": 0.0, "flag": "buggy_required"},
	}

	for r in rows:
		for name, cfg in services.items():
			if r.get(cfg["flag"]):
				cfg["count"] += 1
				if "cost_field" in cfg:
					cfg["total"] += flt(r.get(cfg["cost_field"]) or 0)

	data = []
	for name, cfg in services.items():
		count = cfg["count"]
		total = cfg["total"]
		data.append({
			"service": name,
			"count": count,
			"total_cost": total,
			"avg_cost": (total / count) if count and total else 0,
		})

	return columns, data
