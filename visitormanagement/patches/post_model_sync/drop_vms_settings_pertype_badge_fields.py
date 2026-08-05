import frappe

# The per-visitor-type badge prefix/colour settings on VMS Settings
# (badge_prefix_contractor, badge_colour_contractor, … for all 5 built-in types)
# were dead duplication: badge prefix/colour is defined per type on the Visitor
# Type master, which is the single source of truth read by the badge generator.
# The fields were removed from the doctype; VMS Settings is a Single doctype, so
# their leftover values live as orphan rows in `tabSingles`. Delete them.
ORPHAN_FIELDS = [
	"badge_prefix_contractor",
	"badge_colour_contractor",
	"badge_prefix_candidate",
	"badge_colour_candidate",
	"badge_prefix_customer",
	"badge_colour_customer",
	"badge_prefix_supplier",
	"badge_colour_supplier",
	"badge_prefix_vip",
	"badge_colour_vip",
	"column_break_badge",
]


def execute():
	frappe.db.delete("Singles", {"doctype": "VMS Settings", "field": ("in", ORPHAN_FIELDS)})
