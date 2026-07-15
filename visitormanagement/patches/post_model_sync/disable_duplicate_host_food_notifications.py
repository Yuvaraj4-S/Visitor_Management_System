import frappe

# "VMS Host Alert" and "VMS Food Dept Alert" duplicate emails that the app code
# already sends (SecurityLog._send_host_checkin_email and
# VisitorPass._notify_food_dept). Disable the notifications so each email is sent
# once. `enabled` is in import_file.py's PRESERVE list for Notification, so once
# it is 0 in the DB, `bench migrate` keeps it 0 — this disable is durable.
DUPLICATE_NOTIFICATIONS = ["VMS Host Alert", "VMS Food Dept Alert"]


def execute():
	for name in DUPLICATE_NOTIFICATIONS:
		if frappe.db.exists("Notification", name):
			frappe.db.set_value("Notification", name, "enabled", 0, update_modified=False)
