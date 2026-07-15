import frappe

# The Visitor Pass badge_colour Select originally allowed 5 colours. The Visitor
# Type master was later extended to 8 (adding Blue/Red/Grey). before_save copies
# the linked type's colour onto the pass, so a pass of a Blue/Red/Grey type would
# fail core Select validation. Widen the Visitor Pass field to match the type.
BADGE_COLOURS = "Orange\nPurple\nGreen\nTeal\nGold\nBlue\nRed\nGrey"


def execute():
	if not frappe.db.exists(
		"Property Setter",
		{"doc_type": "Visitor Pass", "field_name": "badge_colour", "property": "options"},
	):
		frappe.make_property_setter(
			{
				"doctype": "Visitor Pass",
				"fieldname": "badge_colour",
				"property": "options",
				"value": BADGE_COLOURS,
				"property_type": "Text",
			}
		)
	else:
		frappe.db.set_value(
			"Property Setter",
			{"doc_type": "Visitor Pass", "field_name": "badge_colour", "property": "options"},
			"value",
			BADGE_COLOURS,
		)
