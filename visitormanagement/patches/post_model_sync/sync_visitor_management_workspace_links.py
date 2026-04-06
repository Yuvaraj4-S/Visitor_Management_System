import json

import frappe


def execute():
	path = frappe.get_app_path(
		"visitormanagement",
		"visitor_management",
		"workspace",
		"visitor_management",
		"visitor_management.json",
	)
	with open(path) as handle:
		expected_links = json.load(handle).get("links", [])

	workspace = frappe.get_doc("Workspace", "Visitor Management")

	current_links = [
		{
			"label": row.label,
			"type": row.type,
			"link_to": getattr(row, "link_to", None),
			"link_type": getattr(row, "link_type", None),
			"hidden": getattr(row, "hidden", 0),
			"onboard": getattr(row, "onboard", 0),
			"is_query_report": getattr(row, "is_query_report", 0),
			"link_count": getattr(row, "link_count", 0),
			"dependencies": getattr(row, "dependencies", "") or "",
		}
		for row in workspace.links
	]

	normalized_expected = [
		{
			"label": row.get("label"),
			"type": row.get("type"),
			"link_to": row.get("link_to"),
			"link_type": row.get("link_type"),
			"hidden": row.get("hidden", 0),
			"onboard": row.get("onboard", 0),
			"is_query_report": row.get("is_query_report", 0),
			"link_count": row.get("link_count", 0),
			"dependencies": row.get("dependencies", "") or "",
		}
		for row in expected_links
	]

	if current_links == normalized_expected:
		return

	workspace.set("links", [])
	for row in expected_links:
		workspace.append("links", row)

	workspace.save(ignore_permissions=True)
