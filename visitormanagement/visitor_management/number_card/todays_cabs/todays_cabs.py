# For license information, please see license.txt
"""Custom-type source for the "Today's Cabs" Number Card.

A cab booked for a visitor is one or two trips: a pickup, a drop, or both. The
card was a plain Document Type card filtered on `pickup_datetime` alone, so a
drop-only trip — the visitor makes their own way in and is driven back — never
counted, on the very day the driver was needed. A Number Card's filters are
ANDed, and "pickup today OR drop today" cannot be written that way, which is
why this card has a method.

It also counted every request with a cab ticked, whatever had become of it: a
draft nobody submitted, a request the Hospitality Manager rejected. Only the
states the daily digest lists are counted here (tasks.LIVE_HOSPITALITY_STATES),
so the card and the 07:00 mail agree on how many cabs today has.

The requests are read through `frappe.get_list`, so the count follows the same
row scope as the Hospitality Request list: the people who run hospitality see
every cab, a host sees the cabs for their own visitors.
"""

import frappe
from frappe.utils import getdate, nowdate

from visitormanagement.visitor_management.tasks import LIVE_HOSPITALITY_STATES


@frappe.whitelist()
def get_data(filters: str | dict | list | None = None, **kwargs) -> dict:
	frappe.has_permission("Hospitality Request", "read", throw=True)

	today = nowdate()
	day = [f"{today} 00:00:00", f"{today} 23:59:59"]
	trips_today = frappe.get_list(
		"Hospitality Request",
		filters={"cab_required": 1, "workflow_state": ("in", LIVE_HOSPITALITY_STATES)},
		or_filters=[["pickup_datetime", "between", day], ["drop_datetime", "between", day]],
		fields=["name", "pickup_datetime"],
		order_by=None,
	)

	return {
		"value": len(trips_today),
		"fieldtype": "Int",
		"route": ["List", "Hospitality Request", "List"],
		"route_options": _list_filters(trips_today, today),
	}


def _list_filters(trips_today, today):
	"""Filters for the list the card opens: the requests it counted, and no others.

	A list's filters are ANDed like a Number Card's, so "pickup today OR drop
	today" cannot be handed to the list either. The route used to carry no date
	at all and opened every live cab request, whatever its day: 6 on the card, 15
	in the list.

	When every counted request has its pickup today, a filter on the pickup date
	describes exactly those requests (and none on a day without cabs). A drop-only
	trip has no pickup date to filter on, so on a day that has one the list is
	given the counted requests by name.
	"""
	picked_up_today = [
		row for row in trips_today if row.pickup_datetime and getdate(row.pickup_datetime) == getdate(today)
	]
	if len(picked_up_today) != len(trips_today):
		return {"name": ["in", [row.name for row in trips_today]]}

	return {
		"cab_required": 1,
		"workflow_state": ["in", list(LIVE_HOSPITALITY_STATES)],
		"pickup_datetime": ["between", [today, today]],
	}
