import frappe

# The Visitor Pass workflow is now one simple stage: Draft → Submit → Pending Approval
# → Approve / Reject (System Manager, or the mapped host with the Host Employee role).
# Passes still waiting in an old per-role lane move to Pending Approval.
OLD_LANES = (
	"Pending System Manager",
	"Pending Sales Manager",
	"Pending HR Manager",
	"Pending HOD",
	"Pending CEO",
)


def execute():
	frappe.db.sql(
		"""update `tabVisitor Pass` set workflow_state = 'Pending Approval'
		where docstatus = 0 and workflow_state in %(lanes)s""",
		{"lanes": OLD_LANES},
	)
