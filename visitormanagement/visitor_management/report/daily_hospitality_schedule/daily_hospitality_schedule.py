# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import cstr, escape_html, getdate, nowdate


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
		{
			"label": _("Visitor Pass"),
			"fieldname": "visitor_pass",
			"fieldtype": "Link",
			"options": "Visitor Pass",
			"width": 130,
		},
		{
			"label": _("Hospitality Request"),
			"fieldname": "hospitality_request",
			"fieldtype": "Link",
			"options": "Hospitality Request",
			"width": 160,
		},
		{"label": _("Details"), "fieldname": "details", "fieldtype": "Data", "width": 260},
		{"label": _("Assignee"), "fieldname": "assignee", "fieldtype": "Data", "width": 160},
		{"label": _("Approval"), "fieldname": "workflow_state", "fieldtype": "Data", "width": 130},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 110},
	]


# Work the hospitality team has on its plate: requests waiting for the
# Hospitality Manager and requests approved. A Draft has not been sent in (its
# pass may never be approved) and a Rejected or Cancelled one has been called
# off. `status` alone could not tell these apart — the workflow never moved it,
# so rejected and never-submitted requests were listed as "Pending" work.
LIVE_WORKFLOW_STATES = ("Pending Approval", "Approved")


def _text(value, default="-"):
	"""A value a person typed, made safe for the report grid.

	The grid renders a Data cell as HTML. Frappe strips scripts when a record is
	saved but keeps plain markup, so a pickup location typed as "<b>Gate 3</b>"
	or a driver name carrying an <img> was drawn as markup. Every free-text
	value goes through here and is shown as text.
	"""
	if value is None or value == "":
		return default
	return escape_html(cstr(value))


def _get_data(target_date, service_filter):
	day_start = f"{target_date} 00:00:00"
	day_end = f"{target_date} 23:59:59"

	# get_list, not get_all: these rows carry drivers' phones, hotel references
	# and pickup points, and get_all ignores the row scope permissions.py puts on
	# Hospitality Request — so a Hospitality User would see every host's
	# arrangements. Overseers (Hospitality Manager, System Manager) still see all.
	rows = frappe.get_list(
		"Hospitality Request",
		filters={
			"workflow_state": ("in", LIVE_WORKFLOW_STATES),
			"docstatus": ("<", 2),
			"status": ("!=", "Cancelled"),
		},
		fields=[
			"name",
			"visitor_pass",
			"status",
			"workflow_state",
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
		],
		# get_list pages at 20 by default; a day's schedule must be complete.
		limit_page_length=0,
	)

	data = []
	show_all = service_filter == "All"

	def add(r, service, time, details, assignee):
		data.append(
			{
				"service": service,
				"time": time,
				"visitor_pass": r.visitor_pass,
				"hospitality_request": r.name,
				# `details` and `assignee` arrive already escaped (see _text).
				"details": details,
				"assignee": assignee,
				"workflow_state": _(r.workflow_state),
				"status": _(r.status or "Pending"),
			}
		)

	for r in rows:
		if (show_all or service_filter == "Cab") and r.cab_required:
			if r.pickup_datetime and day_start <= str(r.pickup_datetime) <= day_end:
				add(
					r,
					_("Cab (Pickup)"),
					str(r.pickup_datetime),
					_text(r.pickup_location),
					_text(r.driver_name),
				)
			if r.drop_datetime and day_start <= str(r.drop_datetime) <= day_end:
				add(r, _("Cab (Drop)"), str(r.drop_datetime), _text(r.drop_location), _text(r.driver_name))
		if (
			(show_all or service_filter == "Hotel")
			and r.hotel_required
			and r.check_in
			and getdate(r.check_in) == target_date
		):
			add(
				r,
				_("Hotel Check-in"),
				str(r.check_in),
				_("{0} | Ref: {1}").format(_text(r.hotel_name), _text(r.booking_reference)),
				_("Front Office"),
			)
		if (
			(show_all or service_filter == "Factory Tour")
			and r.factory_tour_required
			and r.tour_date
			and getdate(r.tour_date) == target_date
		):
			add(r, _("Factory Tour"), str(r.tour_start_time or "-"), _("Plant tour"), _text(r.tour_guide))
		if (
			(show_all or service_filter == "Buggy")
			and r.buggy_required
			and r.buggy_datetime
			and day_start <= str(r.buggy_datetime) <= day_end
		):
			add(r, _("Buggy"), str(r.buggy_datetime), _text(r.buggy_pickup_point), _text(r.buggy_driver))
		if (
			(show_all or service_filter == "Greeting")
			and r.greeting_required
			and r.greeting_delivery_time
			and day_start <= str(r.greeting_delivery_time) <= day_end
		):
			add(
				r,
				_("Greeting"),
				str(r.greeting_delivery_time),
				_text(r.greeting_type),
				_text(r.greeting_assigned_to),
			)

	data.sort(key=lambda x: x["time"])
	return data
