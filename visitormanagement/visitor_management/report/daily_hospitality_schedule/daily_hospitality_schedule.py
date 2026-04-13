# Copyright (c) 2026, Harthesh and contributors
# For license information, please see license.txt

import frappe
from frappe.utils import getdate, nowdate


def execute(filters=None):
	filters = filters or {}
	target_date = getdate(filters.get("date") or nowdate())
	service_filter = filters.get("service") or "All"

	columns = _get_columns()
	data = _get_data(target_date, service_filter)
	return columns, data


def _get_columns():
	return [
		{"label": "Service", "fieldname": "service", "fieldtype": "Data", "width": 110},
		{"label": "Time", "fieldname": "time", "fieldtype": "Data", "width": 140},
		{"label": "Visitor Pass", "fieldname": "visitor_pass", "fieldtype": "Link", "options": "Visitor Pass", "width": 130},
		{"label": "Hospitality Request", "fieldname": "hospitality_request", "fieldtype": "Link", "options": "Hospitality Request", "width": 160},
		{"label": "Details", "fieldname": "details", "fieldtype": "Data", "width": 260},
		{"label": "Assignee", "fieldname": "assignee", "fieldtype": "Data", "width": 160},
		{"label": "Status", "fieldname": "status", "fieldtype": "Data", "width": 110},
	]


def _get_data(target_date, service_filter):
	day_start = f"{target_date} 00:00:00"
	day_end = f"{target_date} 23:59:59"

	rows = frappe.get_all(
		"Hospitality Request",
		filters={"status": ("!=", "Cancelled")},
		fields=[
			"name", "visitor_pass",
			"cab_required", "cab_type", "pickup_location", "pickup_datetime",
			"drop_location", "drop_datetime", "driver_name", "cab_status",
			"hotel_required", "hotel_name", "check_in", "booking_reference", "hotel_status",
			"factory_tour_required", "tour_date", "tour_start_time", "tour_guide", "tour_status",
			"buggy_required", "buggy_pickup_point", "buggy_datetime", "buggy_driver", "buggy_status",
			"greeting_required", "greeting_type", "greeting_delivery_time", "greeting_assigned_to", "greeting_status",
		],
	)

	data = []
	show_all = service_filter == "All"

	for r in rows:
		if (show_all or service_filter == "Cab") and r.cab_required:
			if r.pickup_datetime and day_start <= str(r.pickup_datetime) <= day_end:
				data.append({
					"service": "Cab (Pickup)", "time": str(r.pickup_datetime),
					"visitor_pass": r.visitor_pass, "hospitality_request": r.name,
					"details": f"{r.pickup_location or '-'}",
					"assignee": r.driver_name or "-",
					"status": r.cab_status or "Pending",
				})
			if r.drop_datetime and day_start <= str(r.drop_datetime) <= day_end:
				data.append({
					"service": "Cab (Drop)", "time": str(r.drop_datetime),
					"visitor_pass": r.visitor_pass, "hospitality_request": r.name,
					"details": f"{r.drop_location or '-'}",
					"assignee": r.driver_name or "-",
					"status": r.cab_status or "Pending",
				})
		if (show_all or service_filter == "Hotel") and r.hotel_required and r.check_in and getdate(r.check_in) == target_date:
			data.append({
				"service": "Hotel Check-in", "time": str(r.check_in),
				"visitor_pass": r.visitor_pass, "hospitality_request": r.name,
				"details": f"{r.hotel_name or '-'} | Ref: {r.booking_reference or '-'}",
				"assignee": "Front Office",
				"status": r.hotel_status or "Pending",
			})
		if (show_all or service_filter == "Factory Tour") and r.factory_tour_required and r.tour_date and getdate(r.tour_date) == target_date:
			data.append({
				"service": "Factory Tour", "time": str(r.tour_start_time or "-"),
				"visitor_pass": r.visitor_pass, "hospitality_request": r.name,
				"details": "Plant tour",
				"assignee": r.tour_guide or "-",
				"status": r.tour_status or "Pending",
			})
		if (show_all or service_filter == "Buggy") and r.buggy_required and r.buggy_datetime and day_start <= str(r.buggy_datetime) <= day_end:
			data.append({
				"service": "Buggy", "time": str(r.buggy_datetime),
				"visitor_pass": r.visitor_pass, "hospitality_request": r.name,
				"details": f"{r.buggy_pickup_point or '-'}",
				"assignee": r.buggy_driver or "-",
				"status": r.buggy_status or "Pending",
			})
		if (show_all or service_filter == "Greeting") and r.greeting_required and r.greeting_delivery_time and day_start <= str(r.greeting_delivery_time) <= day_end:
			data.append({
				"service": "Greeting", "time": str(r.greeting_delivery_time),
				"visitor_pass": r.visitor_pass, "hospitality_request": r.name,
				"details": r.greeting_type or "-",
				"assignee": r.greeting_assigned_to or "-",
				"status": r.greeting_status or "Planned",
			})

	data.sort(key=lambda x: x["time"])
	return data
