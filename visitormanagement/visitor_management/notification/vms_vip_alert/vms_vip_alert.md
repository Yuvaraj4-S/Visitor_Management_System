VIP Visitor Notification

Current Stage: {{ doc.workflow_state or doc.status }}

- Visitor Name: {{ doc.visitor_full_name }}
- Company / Organisation: {{ doc.company__organisation or 'N/A' }}
- Purpose of Visit: {{ doc.purpose_of_visit or 'N/A' }}
- Host Person: {{ doc.person_to_visit or 'N/A' }}
- Visit Date: {{ doc.visit_date }} | {{ doc.expected_checkin or 'N/A' }} - {{ doc.expected_checkout or 'N/A' }}
- Dedicated Meeting Room: {{ doc.dedicated_meeting_room or doc.conference_room or 'N/A' }}
- Interpreter: {{ 'Required' if doc.interpreter_required else 'Not Required' }}{% if doc.interpreter_required and doc.interpreter_language %} ({{ doc.interpreter_language }}){% endif %}
- Meal / Number of People: {{ doc.meal_type or 'N/A' }} / {{ doc.number_of_people or 'N/A' }}
- Protocol Notes: {{ doc.protocol_notes or 'N/A' }}
- Declared Items: {% if doc.visitor_items %}{% for item in doc.visitor_items %}{{ item.item_name }} ×{{ item.quantity|int }}{% if not loop.last %}, {% endif %}{% endfor %}{% else %}No items declared{% endif %}
