# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import getdate, nowdate

from visitormanagement.visitor_management.report.utils import employee_names, link_or_data, list_scope

_FIELDS = (
	"name",
	"visitor_pass",
	"status",
	"cab_required",
	"cab_type",
	"pickup_location",
	"pickup_datetime",
	"drop_location",
	"drop_datetime",
	"driver_name",
	"hotel_required",
	"hotel_name",
	"check_in",
	"booking_reference",
	"factory_tour_required",
	"tour_date",
	"tour_start_time",
	"tour_guide",
	"buggy_required",
	"buggy_pickup_point",
	"buggy_datetime",
	"buggy_driver",
	"greeting_required",
	"greeting_type",
	"greeting_delivery_time",
	"greeting_assigned_to",
)

# Link fields to Employee: shown by name in the "Assignee" column.
_EMPLOYEE_FIELDS = ("tour_guide", "buggy_driver", "greeting_assigned_to")


def execute(filters=None):
	filters = filters or {}
	target_date = getdate(filters.get("date") or nowdate())
	service_filter = filters.get("service") or "All"

	columns = _get_columns()
	data = _get_data(target_date, service_filter)
	return columns, data


def _get_columns():
	return [
		{"label": _("Service"), "fieldname": "service", "fieldtype": "Data", "width": 110},
		{"label": _("Time"), "fieldname": "time", "fieldtype": "Data", "width": 140},
		# Links only for a viewer who can read the target. A delivery role that
		# holds just the `report` right on Hospitality Request (and nothing on
		# Visitor Pass) gets the same values as plain text; as Links, Frappe
		# refused the whole report with "No permission to read ...".
		link_or_data("Visitor Pass", label=_("Visitor Pass"), fieldname="visitor_pass", width=130),
		link_or_data(
			"Hospitality Request",
			label=_("Hospitality Request"),
			fieldname="hospitality_request",
			width=160,
		),
		{"label": _("Details"), "fieldname": "details", "fieldtype": "Data", "width": 260},
		{"label": _("Assignee"), "fieldname": "assignee", "fieldtype": "Data", "width": 160},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 110},
	]


def _row_scope():
	"""The caller's Hospitality Request rows, as SQL against the real table name.

	The report used `frappe.get_all`, which applies no permissions at all: any
	role allowed to run it saw every request on the site — dietary needs, hotel
	bookings, drivers.

	A viewer with read on the DocType gets exactly the list view's scope (the
	app's row conditions, User Permissions, shares). A viewer who only holds the
	`report` right — a delivery role — has no list to mirror, so the app's own
	row rule applies: requests they raised, are assigned to, or whose visitor
	they host (permissions.get_hospitality_request_permission_query_conditions).
	"""
	if frappe.has_permission("Hospitality Request", "read") or frappe.has_permission(
		"Hospitality Request", "select"
	):
		return list_scope("Hospitality Request")

	from visitormanagement.permissions import get_hospitality_request_permission_query_conditions

	condition = get_hospitality_request_permission_query_conditions(frappe.session.user) or ""
	return condition.replace("%", "%%")


def _requests_for_day(target_date):
	"""Requests with at least one service on `target_date`, within the caller's scope.

	The day is pushed into the query: the report used to load every request ever
	raised and pick the day's rows out in Python.
	"""
	hr = "`tabHospitality Request`"
	conditions = [
		f"ifnull({hr}.status, '') != 'Cancelled'",
		f"{hr}.docstatus < 2",
		f"""(
			({hr}.cab_required = 1 AND (
				({hr}.pickup_datetime >= %(day_start)s AND {hr}.pickup_datetime <= %(day_end)s)
				OR ({hr}.drop_datetime >= %(day_start)s AND {hr}.drop_datetime <= %(day_end)s)))
			OR ({hr}.hotel_required = 1 AND {hr}.check_in = %(day)s)
			OR ({hr}.factory_tour_required = 1 AND {hr}.tour_date = %(day)s)
			OR ({hr}.buggy_required = 1
				AND {hr}.buggy_datetime >= %(day_start)s AND {hr}.buggy_datetime <= %(day_end)s)
			OR ({hr}.greeting_required = 1
				AND {hr}.greeting_delivery_time >= %(day_start)s
				AND {hr}.greeting_delivery_time <= %(day_end)s)
		)""",
	]
	scope = _row_scope()
	if scope:
		conditions.append(f"({scope})")

	columns = ", ".join(f"{hr}.`{fieldname}`" for fieldname in _FIELDS)
	return frappe.db.sql(
		f"SELECT {columns} FROM {hr} WHERE " + " AND ".join(conditions),
		{
			"day": str(target_date),
			"day_start": f"{target_date} 00:00:00",
			"day_end": f"{target_date} 23:59:59",
		},
		as_dict=True,
	)


def _get_data(target_date, service_filter):
	day_start = f"{target_date} 00:00:00"
	day_end = f"{target_date} 23:59:59"

	rows = _requests_for_day(target_date)
	names = employee_names(r.get(f) for r in rows for f in _EMPLOYEE_FIELDS)

	def person(employee):
		if not employee:
			return "-"
		return names.get(employee) or employee

	data = []
	show_all = service_filter == "All"

	for r in rows:
		if (show_all or service_filter == "Cab") and r.cab_required:
			if r.pickup_datetime and day_start <= str(r.pickup_datetime) <= day_end:
				data.append(
					{
						"service": "Cab (Pickup)",
						"time": str(r.pickup_datetime),
						"visitor_pass": r.visitor_pass,
						"hospitality_request": r.name,
						"details": f"{r.pickup_location or '-'}",
						"assignee": r.driver_name or "-",
						"status": r.status or "Pending",
					}
				)
			if r.drop_datetime and day_start <= str(r.drop_datetime) <= day_end:
				data.append(
					{
						"service": "Cab (Drop)",
						"time": str(r.drop_datetime),
						"visitor_pass": r.visitor_pass,
						"hospitality_request": r.name,
						"details": f"{r.drop_location or '-'}",
						"assignee": r.driver_name or "-",
						"status": r.status or "Pending",
					}
				)
		if (
			(show_all or service_filter == "Hotel")
			and r.hotel_required
			and r.check_in
			and getdate(r.check_in) == target_date
		):
			data.append(
				{
					"service": "Hotel Check-in",
					"time": str(r.check_in),
					"visitor_pass": r.visitor_pass,
					"hospitality_request": r.name,
					"details": f"{r.hotel_name or '-'} | Ref: {r.booking_reference or '-'}",
					"assignee": "Front Office",
					"status": r.status or "Pending",
				}
			)
		if (
			(show_all or service_filter == "Factory Tour")
			and r.factory_tour_required
			and r.tour_date
			and getdate(r.tour_date) == target_date
		):
			data.append(
				{
					"service": "Factory Tour",
					"time": str(r.tour_start_time or "-"),
					"visitor_pass": r.visitor_pass,
					"hospitality_request": r.name,
					"details": "Plant tour",
					"assignee": person(r.tour_guide),
					"status": r.status or "Pending",
				}
			)
		if (
			(show_all or service_filter == "Buggy")
			and r.buggy_required
			and r.buggy_datetime
			and day_start <= str(r.buggy_datetime) <= day_end
		):
			data.append(
				{
					"service": "Buggy",
					"time": str(r.buggy_datetime),
					"visitor_pass": r.visitor_pass,
					"hospitality_request": r.name,
					"details": f"{r.buggy_pickup_point or '-'}",
					"assignee": person(r.buggy_driver),
					"status": r.status or "Pending",
				}
			)
		if (
			(show_all or service_filter == "Greeting")
			and r.greeting_required
			and r.greeting_delivery_time
			and day_start <= str(r.greeting_delivery_time) <= day_end
		):
			data.append(
				{
					"service": "Greeting",
					"time": str(r.greeting_delivery_time),
					"visitor_pass": r.visitor_pass,
					"hospitality_request": r.name,
					"details": r.greeting_type or "-",
					"assignee": person(r.greeting_assigned_to),
					"status": r.status or "Pending",
				}
			)

	data.sort(key=lambda x: x["time"])
	return data
