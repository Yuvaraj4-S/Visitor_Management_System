import frappe

from visitormanagement.visitor_management.time_utils import is_auto_now_time

TOUR_AREA = "Factory Tour Area"


def execute():
	"""Clear tour times that are only Frappe's automatic "now" stamp.

	Frappe fills every empty Time field of a new document with the current clock,
	so every Hospitality Request created so far carries a Tour Start / Tour End of
	whenever it happened to be created — usually the moment a Visitor Pass was
	approved. The plant has been reading those as real tour times.

	Hospitality times are not guessed from the visit window any more — the VMS User
	schedules each one — so these are cleared rather than replaced. Times anyone
	actually picked carry no microseconds and are left untouched.
	"""
	for request in frappe.get_all(
		"Hospitality Request",
		fields=["name", "tour_start_time", "tour_end_time"],
	):
		updates = {f: None for f in ("tour_start_time", "tour_end_time") if is_auto_now_time(request.get(f))}
		if updates:
			frappe.db.set_value("Hospitality Request", request.name, updates, update_modified=False)

	for area in frappe.get_all(
		TOUR_AREA,
		filters={"parenttype": "Hospitality Request"},
		fields=["name", "from_time", "to_time"],
	):
		updates = {f: None for f in ("from_time", "to_time") if is_auto_now_time(area.get(f))}
		if updates:
			frappe.db.set_value(TOUR_AREA, area.name, updates, update_modified=False)
