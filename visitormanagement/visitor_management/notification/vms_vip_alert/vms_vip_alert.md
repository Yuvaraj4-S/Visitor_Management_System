VIP Visitor Notification

Current Stage: {{ doc.workflow_state or doc.status }}

- Visitor Name: {{ doc.visitor_full_name }}
- Company / Organisation: {{ doc.company__organisation or 'N/A' }}
- Purpose of Visit: {{ doc.purpose_of_visit or 'N/A' }}
- Host Person: {{ doc.person_to_visit or 'N/A' }}
- Visit Date: {{ doc.visit_date }} | {{ doc.expected_checkin or 'N/A' }} - {{ doc.expected_checkout or 'N/A' }}
- Risk / SLA: {{ doc.risk_level or 'N/A' }} / {{ doc.approval_sla_minutes or 'N/A' }} mins
- Priority Lane: {{ 'Yes' if doc.priority_lane else 'No' }}
- Dedicated Meeting Room: {{ doc.dedicated_meeting_room or doc.conference_room or 'N/A' }}
- Interpreter: {{ 'Required' if doc.interpreter_required else 'Not Required' }}{% if doc.interpreter_required and doc.interpreter_language %} ({{ doc.interpreter_language }}){% endif %}
- Welcome Gift: {{ doc.welcome_gift or 'N/A' }}
- Meal / Number of People: {{ doc.meal_type or 'N/A' }} / {{ doc.number_of_people or 'N/A' }}
- Items Carried: {{ doc.items_carried or 'No items declared' }}
- Protocol Notes: {{ doc.protocol_notes or 'N/A' }}
