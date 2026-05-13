import frappe


VIP_ALERT_MESSAGE = """<div style="font-family: Arial, sans-serif; font-size: 13px; color: #1f2933; line-height: 1.5;">
<h3 style="margin: 0 0 12px; color: #102a43;">VIP Visitor Notification</h3>
<p style="margin: 0 0 12px;"><strong>Current Stage:</strong> {{ doc.workflow_state or doc.status }}</p>
<table style="width: 100%; border-collapse: collapse; margin-bottom: 16px;">
<tr><td style="padding: 6px; border: 1px solid #d9e2ec;"><strong>Visitor Name</strong></td><td style="padding: 6px; border: 1px solid #d9e2ec;">{{ doc.visitor_full_name }}</td></tr>
<tr><td style="padding: 6px; border: 1px solid #d9e2ec;"><strong>Company / Organisation</strong></td><td style="padding: 6px; border: 1px solid #d9e2ec;">{{ doc.company__organisation or 'N/A' }}</td></tr>
<tr><td style="padding: 6px; border: 1px solid #d9e2ec;"><strong>Purpose of Visit</strong></td><td style="padding: 6px; border: 1px solid #d9e2ec;">{{ doc.purpose_of_visit or 'N/A' }}</td></tr>
<tr><td style="padding: 6px; border: 1px solid #d9e2ec;"><strong>Host Person</strong></td><td style="padding: 6px; border: 1px solid #d9e2ec;">{{ doc.person_to_visit or 'N/A' }}</td></tr>
<tr><td style="padding: 6px; border: 1px solid #d9e2ec;"><strong>Visit Date</strong></td><td style="padding: 6px; border: 1px solid #d9e2ec;">{{ doc.visit_date }} | {{ doc.expected_checkin or 'N/A' }} - {{ doc.expected_checkout or 'N/A' }}</td></tr>
<tr><td style="padding: 6px; border: 1px solid #d9e2ec;"><strong>Dedicated Meeting Room</strong></td><td style="padding: 6px; border: 1px solid #d9e2ec;">{{ doc.dedicated_meeting_room or doc.conference_room or 'N/A' }}</td></tr>
<tr><td style="padding: 6px; border: 1px solid #d9e2ec;"><strong>Interpreter</strong></td><td style="padding: 6px; border: 1px solid #d9e2ec;">{{ 'Required' if doc.interpreter_required else 'Not Required' }}{% if doc.interpreter_required and doc.interpreter_language %} ({{ doc.interpreter_language }}){% endif %}</td></tr>
<tr><td style="padding: 6px; border: 1px solid #d9e2ec;"><strong>Meal / Number of People</strong></td><td style="padding: 6px; border: 1px solid #d9e2ec;">{{ doc.meal_type or 'N/A' }} / {{ doc.number_of_people or 'N/A' }}</td></tr>
<tr><td style="padding: 6px; border: 1px solid #d9e2ec;"><strong>Protocol Notes</strong></td><td style="padding: 6px; border: 1px solid #d9e2ec;">{{ doc.protocol_notes or 'N/A' }}</td></tr>
</table>
{% if doc.visitor_items %}<p style="margin: 0 0 6px;"><strong>Declared Items</strong></p><ul>{% for item in doc.visitor_items %}<li>{{ item.item_name }}{% if item.quantity %} | Qty: {{ item.quantity }}{% endif %}{% if item.serial_number %} | S/N: {{ item.serial_number }}{% endif %}</li>{% endfor %}</ul>{% endif %}
<p style="margin: 12px 0 0;">This is a formal VIP notification for approval readiness and protocol planning.</p>
</div>"""


def execute():
	if not frappe.db.exists("Notification", "VMS VIP Alert"):
		return

	frappe.db.set_value(
		"Notification",
		"VMS VIP Alert",
		{
			"condition": "doc.visitor_type == 'VIP' and (doc.workflow_state in ['Pending HOD', 'Pending CEO', 'Approved'] or doc.status == 'Approved')",
			"message": VIP_ALERT_MESSAGE,
			"subject": "VIP {{ doc.workflow_state or doc.status }} | {{ doc.visitor_full_name }} | {{ doc.visit_date }}",
			"send_system_notification": 0,
			"enabled": 0,
			"channel": "Email",
			"message_type": "HTML",
		},
		update_modified=False,
	)

	frappe.db.delete("Notification Recipient", {"parent": "VMS VIP Alert", "parenttype": "Notification"})

	for idx, role in enumerate(["HOD", "CEO"], start=1):
		frappe.get_doc(
			{
				"doctype": "Notification Recipient",
				"parent": "VMS VIP Alert",
				"parenttype": "Notification",
				"parentfield": "recipients",
				"idx": idx,
				"receiver_by_role": role,
			}
		).insert(ignore_permissions=True)
