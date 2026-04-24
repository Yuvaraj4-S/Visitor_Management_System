import frappe


# Per-service status fields were removed. Only the main Hospitality Request
# `status` drives notifications now: Approved → Completed.
# ("Submitted" was a duplicate of the richer "VMS Hospitality Request New"
# fixture — retired below and now listed in OBSOLETE_NOTIFICATIONS.)
NOTIFICATIONS = [
	{
		"name": "Hospitality Request Approved",
		"subject": "Hospitality Approved — {{ doc.visitor_pass }}",
		"document_type": "Hospitality Request",
		"event": "Value Change",
		"value_changed": "status",
		"condition": "doc.status == 'Approved'",
		"channel": "Email",
		"roles": ["Host Employee"],
		"message": (
			"<p>Hospitality arrangements for your visitor have been approved. "
			"The hospitality team will now begin preparing the services requested.</p>"
			"<table cellpadding=\"6\" cellspacing=\"0\" border=\"0\" style=\"margin:8px 0 16px;border-collapse:collapse;\">"
			"<tr><td><b>Request</b></td><td style=\"padding-left:14px;\">{{ doc.name }}</td></tr>"
			"<tr><td><b>Visitor Pass</b></td><td style=\"padding-left:14px;\">{{ doc.visitor_pass }}</td></tr>"
			"<tr><td><b>Visitor</b></td><td style=\"padding-left:14px;\">{{ doc.visitor_name_display or '-' }}</td></tr>"
			"<tr><td><b>Visit Window</b></td><td style=\"padding-left:14px;\">{{ doc.visit_start_time or '-' }} — {{ doc.visit_end_time or '-' }}</td></tr>"
			"<tr><td><b>Services</b></td><td style=\"padding-left:14px;\">"
			"{% set parts = [] %}"
			"{% if doc.meal_required %}{% set _ = parts.append('Meal') %}{% endif %}"
			"{% if doc.cab_required %}{% set _ = parts.append('Cab') %}{% endif %}"
			"{% if doc.hotel_required %}{% set _ = parts.append('Hotel') %}{% endif %}"
			"{% if doc.buggy_required %}{% set _ = parts.append('Buggy') %}{% endif %}"
			"{% if doc.factory_tour_required %}{% set _ = parts.append('Factory Tour') %}{% endif %}"
			"{% if doc.greeting_required %}{% set _ = parts.append('Greeting') %}{% endif %}"
			"{{ parts | join(' &middot; ') or 'None' }}"
			"</td></tr>"
			"</table>"
			"<p><a href=\"{{ frappe.utils.get_url() }}/app/hospitality-request/{{ doc.name }}\" "
			"style=\"display:inline-block;background:#0d6b3e;color:#fff;padding:8px 16px;border-radius:4px;text-decoration:none;\">"
			"View Request</a></p>"
		),
	},
	{
		"name": "Hospitality Completed",
		"subject": "Hospitality Completed — {{ doc.visitor_pass }}",
		"document_type": "Hospitality Request",
		"event": "Value Change",
		"value_changed": "status",
		"condition": "doc.status == 'Completed'",
		"channel": "Email",
		"roles": ["Hospitality Manager", "Host Employee"],
		"message": (
			"<p>All hospitality arrangements have been completed for visitor pass "
			"<b>{{ doc.visitor_pass }}</b>. Below is a summary of the services delivered.</p>"
			"<table cellpadding=\"6\" cellspacing=\"0\" border=\"0\" style=\"margin:8px 0 16px;border-collapse:collapse;\">"
			"<tr><td><b>Request</b></td><td style=\"padding-left:14px;\">{{ doc.name }}</td></tr>"
			"<tr><td><b>Visitor Pass</b></td><td style=\"padding-left:14px;\">{{ doc.visitor_pass }}</td></tr>"
			"<tr><td><b>Visitor</b></td><td style=\"padding-left:14px;\">{{ doc.visitor_name_display or '-' }}</td></tr>"
			"<tr><td><b>Visit Window</b></td><td style=\"padding-left:14px;\">{{ doc.visit_start_time or '-' }} — {{ doc.visit_end_time or '-' }}</td></tr>"
			"</table>"
			"<p><b>Services Delivered:</b></p>"
			"<ul>"
			"{% if doc.meal_required %}<li>Meal arrangement</li>{% endif %}"
			"{% if doc.cab_required %}<li>Cab / transport</li>{% endif %}"
			"{% if doc.hotel_required %}<li>Hotel booking</li>{% endif %}"
			"{% if doc.buggy_required %}<li>Buggy / site transport</li>{% endif %}"
			"{% if doc.factory_tour_required %}<li>Factory tour</li>{% endif %}"
			"{% if doc.greeting_required %}<li>Greeting / lobby escort</li>{% endif %}"
			"{% if not (doc.meal_required or doc.cab_required or doc.hotel_required or doc.buggy_required or doc.factory_tour_required or doc.greeting_required) %}"
			"<li>No additional services were required</li>"
			"{% endif %}"
			"</ul>"
			"<p><a href=\"{{ frappe.utils.get_url() }}/app/hospitality-request/{{ doc.name }}\">View Request</a></p>"
		),
	},
]


# Notifications removed when per-service statuses were dropped, plus
# "Hospitality Request Submitted" which duplicated the richer
# "VMS Hospitality Request New" fixture (same event, same recipient role).
# The patch deletes them if they still exist.
OBSOLETE_NOTIFICATIONS = [
	"Cab Assigned to Driver",
	"Cab Info to Visitor",
	"Hotel Booking Confirmed",
	"Factory Tour Scheduled",
	"Factory Tour Day Reminder",
	"Buggy Assigned",
	"Greeting Planned",
	"Hospitality Request Submitted",
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
