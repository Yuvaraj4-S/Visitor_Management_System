import frappe


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
		"name": "Cab Assigned to Driver",
		"subject": "Cab Assignment - Visitor Pass {{ doc.visitor_pass }}",
		"document_type": "Hospitality Request",
		"event": "Value Change",
		"value_changed": "cab_status",
		"condition": "doc.cab_required and doc.cab_status == 'Assigned'",
		"channel": "Email",
		"message": (
			"<p>Hi,</p>"
			"<p>You have been assigned visitor pickup/drop.</p>"
			"<ul>"
			"<li><b>Visitor Pass:</b> {{ doc.visitor_pass }}</li>"
			"<li><b>Type:</b> {{ doc.cab_type }}</li>"
			"<li><b>Pickup:</b> {{ doc.pickup_location or '-' }} at {{ doc.pickup_datetime or '-' }}</li>"
			"<li><b>Drop:</b> {{ doc.drop_location or '-' }} at {{ doc.drop_datetime or '-' }}</li>"
			"<li><b>Flight/Train:</b> {{ doc.flight_train_no or '-' }}</li>"
			"</ul>"
		),
		"recipient_field": "cab_vendor",
	},
	{
		"name": "Cab Info to Visitor",
		"subject": "Your Cab Details - {{ doc.visitor_pass }}",
		"document_type": "Hospitality Request",
		"event": "Value Change",
		"value_changed": "cab_status",
		"condition": "doc.cab_required and doc.cab_status == 'Assigned'",
		"channel": "Email",
		"roles": ["Visitor Manager"],
		"message": (
			"<p>Cab details for your visit:</p>"
			"<ul>"
			"<li><b>Driver:</b> {{ doc.driver_name or '-' }} ({{ doc.driver_phone or '-' }})</li>"
			"<li><b>Vehicle:</b> {{ doc.vehicle_number or '-' }}</li>"
			"<li><b>Pickup Time:</b> {{ doc.pickup_datetime or '-' }}</li>"
			"</ul>"
		),
	},
	{
		"name": "Hotel Booking Confirmed",
		"subject": "Hotel Booking Confirmed - {{ doc.visitor_pass }}",
		"document_type": "Hospitality Request",
		"event": "Value Change",
		"value_changed": "hotel_status",
		"condition": "doc.hotel_required and doc.hotel_status == 'Confirmed'",
		"channel": "Email",
		"roles": ["Host Employee", "Front Office Executive"],
		"message": (
			"<p>Hotel booking confirmed.</p>"
			"<ul>"
			"<li><b>Hotel:</b> {{ doc.hotel_name or '-' }}</li>"
			"<li><b>Check-in:</b> {{ doc.check_in }}</li>"
			"<li><b>Check-out:</b> {{ doc.check_out }} ({{ doc.nights }} nights)</li>"
			"<li><b>Room Type:</b> {{ doc.room_type or '-' }} x {{ doc.no_of_rooms or 1 }}</li>"
			"<li><b>Booking Ref:</b> {{ doc.booking_reference or '-' }}</li>"
			"</ul>"
		),
	},
	{
		"name": "Factory Tour Scheduled",
		"subject": "Factory Tour Scheduled - {{ doc.tour_date }}",
		"document_type": "Hospitality Request",
		"event": "Value Change",
		"value_changed": "tour_status",
		"condition": "doc.factory_tour_required and doc.tour_status == 'Scheduled'",
		"channel": "Email",
		"message": (
			"<p>You have been assigned as tour guide.</p>"
			"<ul>"
			"<li><b>Date:</b> {{ doc.tour_date }}</li>"
			"<li><b>Time:</b> {{ doc.tour_start_time }} - {{ doc.tour_end_time }}</li>"
			"<li><b>Visitor Pass:</b> {{ doc.visitor_pass }}</li>"
			"<li><b>Safety Briefing:</b> {{ 'Done' if doc.safety_briefing_done else 'Pending' }}</li>"
			"</ul>"
		),
		"recipient_field": "tour_guide",
	},
	{
		"name": "Factory Tour Day Reminder",
		"subject": "Reminder: Factory Tour Tomorrow",
		"document_type": "Hospitality Request",
		"event": "Days Before",
		"date_changed": "tour_date",
		"days_in_advance": 1,
		"condition": "doc.factory_tour_required and doc.tour_status in ('Scheduled', 'Pending')",
		"channel": "Email",
		"roles": ["Factory Tour Coordinator"],
		"message": "<p>Factory tour scheduled for tomorrow ({{ doc.tour_date }}) for visitor pass {{ doc.visitor_pass }}. Ensure PPE and safety briefing ready.</p>",
	},
	{
		"name": "Buggy Assigned",
		"subject": "Buggy Assignment - {{ doc.visitor_pass }}",
		"document_type": "Hospitality Request",
		"event": "Value Change",
		"value_changed": "buggy_status",
		"condition": "doc.buggy_required and doc.buggy_status == 'Assigned'",
		"channel": "Email",
		"message": (
			"<p>Buggy assignment:</p>"
			"<ul>"
			"<li><b>Pickup:</b> {{ doc.buggy_pickup_point }}</li>"
			"<li><b>Drop:</b> {{ doc.buggy_drop_point }}</li>"
			"<li><b>Time:</b> {{ doc.buggy_datetime }}</li>"
			"<li><b>Buggy No:</b> {{ doc.buggy_number or '-' }}</li>"
			"</ul>"
		),
		"recipient_field": "buggy_driver",
	},
	{
		"name": "Greeting Planned",
		"subject": "Greeting Arrangement - {{ doc.greeting_type }}",
		"document_type": "Hospitality Request",
		"event": "Value Change",
		"value_changed": "greeting_status",
		"condition": "doc.greeting_required and doc.greeting_status == 'Planned'",
		"channel": "Email",
		"message": (
			"<p>Greeting arrangement assigned to you.</p>"
			"<ul>"
			"<li><b>Type:</b> {{ doc.greeting_type }}</li>"
			"<li><b>Occasion:</b> {{ doc.greeting_occasion or '-' }}</li>"
			"<li><b>Delivery Time:</b> {{ doc.greeting_delivery_time }}</li>"
			"<li><b>Delivery Point:</b> {{ doc.greeting_delivery_point or '-' }}</li>"
			"</ul>"
		),
		"recipient_field": "greeting_assigned_to",
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
		"message": "<p>All hospitality arrangements completed for visitor pass {{ doc.visitor_pass }}.</p>",
	},
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
	doc.channel = cfg.get("channel", "Email")
	doc.message = cfg["message"]
	doc.enabled = 1
	doc.send_system_notification = 0
	doc.message_type = "HTML"

	if cfg.get("value_changed"):
		doc.value_changed = cfg["value_changed"]
	if cfg.get("date_changed"):
		doc.date_changed = cfg["date_changed"]
	if cfg.get("days_in_advance"):
		doc.days_in_advance = cfg["days_in_advance"]
	if cfg.get("condition"):
		doc.condition = cfg["condition"]

	doc.set("recipients", [])
	for role in cfg.get("roles", []):
		doc.append("recipients", {"receiver_by_role": role})
	if cfg.get("recipient_field"):
		doc.append("recipients", {"receiver_by_document_field": cfg["recipient_field"]})

	if doc.is_new():
		doc.insert(ignore_permissions=True)
	else:
		doc.save(ignore_permissions=True)


def execute():
	for cfg in NOTIFICATIONS:
		try:
			_upsert_notification(cfg)
		except Exception:
			frappe.log_error(
				title=f"Hospitality Notification Sync Failed: {cfg['name']}",
				message=frappe.get_traceback(),
			)
