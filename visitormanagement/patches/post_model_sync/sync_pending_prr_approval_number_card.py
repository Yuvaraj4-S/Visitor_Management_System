import frappe


def execute():
	card_name = "Pending Visitor Approvals"
	if not frappe.db.exists("Number Card", card_name):
		return

	card = frappe.get_doc("Number Card", card_name)
	changed = False

	if card.document_type != "Pre-Registration Request":
		card.document_type = "Pre-Registration Request"
		changed = True

	filters_json = '[[\"Pre-Registration Request\",\"status\",\"=\",\"Pending Approval\",false]]'
	if card.filters_json != filters_json:
		card.filters_json = filters_json
		changed = True

	if changed:
		card.save(ignore_permissions=True)
