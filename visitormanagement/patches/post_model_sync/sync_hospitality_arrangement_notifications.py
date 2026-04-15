import frappe


# Per-service status fields were removed. Only the main Hospitality Request
# `status` drives notifications now: Submitted → Approved → Completed.
NOTIFICATIONS = [
	{
		"name": "Hospitality Request Submitted",
		"subject": "Hospitality Request {{ doc.name }} - Pending Approval",
		"document_type": "Hospitality Request",
		"event": "New",
		"channel": "Email",
		"roles": ["Hospitality Manager"],
		"message": "<p>New hospitality request {{ doc.name }} for visitor pass {{ doc.visitor_pass }} needs your approval.</p>",
	},
	{
		"name": "Hospitality Request Approved",
		"subject": "Hospitality Request {{ doc.name }} Approved",
		"document_type": "Hospitality Request",
		"event": "Value Change",
		"value_changed": "status",
		"condition": "doc.status == 'Approved'",
		"channel": "Email",
		"roles": ["Host Employee"],
		"message": "<p>Hospitality request {{ doc.name }} has been approved.</p>",
	},
	{
		"name": "Hospitality Completed",
		"subject": "Hospitality Completed - {{ doc.visitor_pass }}",
		"document_type": "Hospitality Request",
		"event": "Value Change",
		"value_changed": "status",
		"condition": "doc.status == 'Completed'",
		"channel": "Email",
		"roles": ["Hospitality Manager", "Host Employee"],
		"message": (
			"<p>All hospitality arrangements completed for visitor pass {{ doc.visitor_pass }}.</p>"
			"<ul>"
			"<li><b>Cab Needed:</b> {{ 'Yes' if doc.cab_required else 'No' }}</li>"
			"<li><b>Hotel Needed:</b> {{ 'Yes' if doc.hotel_required else 'No' }}</li>"
			"<li><b>Factory Tour:</b> {{ 'Yes' if doc.factory_tour_required else 'No' }}</li>"
			"<li><b>Buggy:</b> {{ 'Yes' if doc.buggy_required else 'No' }}</li>"
			"<li><b>Greeting:</b> {{ 'Yes' if doc.greeting_required else 'No' }}</li>"
			"</ul>"
		),
	},
]


# Notifications removed when per-service statuses were dropped. The patch
# deletes them if they still exist from older installs.
OBSOLETE_NOTIFICATIONS = [
	"Cab Assigned to Driver",
	"Cab Info to Visitor",
	"Hotel Booking Confirmed",
	"Factory Tour Scheduled",
	"Factory Tour Day Reminder",
	"Buggy Assigned",
	"Greeting Planned",
]


def _upsert_notification(cfg):
	name = cfg["name"]
	doc = (
		frappe.get_doc("Notification", name)
		if frappe.db.exists("Notification", name)
		else frappe.new_doc("Notification")
	)
	doc.name = name
	doc.subject = cfg["subject"]
	doc.document_type = cfg["document_type"]
	doc.event = cfg["event"]
	doc.channel = cfg["channel"]
	doc.message = cfg["message"]
	doc.enabled = 1

	if "value_changed" in cfg:
		doc.value_changed = cfg["value_changed"]
	if "condition" in cfg:
		doc.condition = cfg["condition"]
	if "date_changed" in cfg:
		doc.date_changed = cfg["date_changed"]
	if "days_in_advance" in cfg:
		doc.days_in_advance = cfg["days_in_advance"]

	# Recipients
	doc.recipients = []
	for role in cfg.get("roles", []):
		doc.append("recipients", {"receiver_by_role": role})
	if cfg.get("recipient_field"):
		doc.append("recipients", {"receiver_by_document_field": cfg["recipient_field"]})

	if doc.is_new():
		doc.insert(ignore_permissions=True)
	else:
		doc.save(ignore_permissions=True)


def _delete_obsolete():
	for name in OBSOLETE_NOTIFICATIONS:
		if frappe.db.exists("Notification", name):
			try:
				frappe.delete_doc("Notification", name, ignore_permissions=True, force=1)
			except Exception:
				frappe.log_error(
					title=f"Obsolete Notification Delete Failed: {name}",
					message=frappe.get_traceback(),
				)


def execute():
	_delete_obsolete()
	for cfg in NOTIFICATIONS:
		try:
			_upsert_notification(cfg)
		except Exception:
			frappe.log_error(
				title=f"Hospitality Notification Sync Failed: {cfg['name']}",
				message=frappe.get_traceback(),
			)
