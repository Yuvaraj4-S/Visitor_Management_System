import frappe


def get_context(context):
	context.no_cache = 1
	context.title = "Visitor Pre-Registration"
	context.host_options = frappe.get_all(
		"Employee",
		filters={"status": "Active"},
		fields=["name", "employee_name"],
		order_by="employee_name asc",
		limit=200,
	)
	return context
