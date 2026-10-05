import frappe

FIELDS = ("validate_aadhaar_number", "validate_pan_number")


def execute():
	"""New VMS Settings checkboxes default to ticked so existing sites keep validating
	Aadhaar / PAN. Only fills a value that was never saved — never overrides a choice."""
	for fieldname in FIELDS:
		saved = frappe.db.sql(
			"select 1 from `tabSingles` where doctype = 'VMS Settings' and field = %s", fieldname
		)
		if not saved:
			frappe.db.set_single_value("VMS Settings", fieldname, 1)
	frappe.clear_cache(doctype="VMS Settings")
