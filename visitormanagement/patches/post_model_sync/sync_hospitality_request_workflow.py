import frappe


WORKFLOW_NAME = "Hospitality Request Approval"
DOCTYPE = "Hospitality Request"

STATES = [
	{"state": "Draft", "doc_status": "0", "allow_edit": "Employee"},
	{"state": "Pending Manager Approval", "doc_status": "0", "allow_edit": "Hospitality Manager"},
	{"state": "Approved", "doc_status": "0", "allow_edit": "Hospitality Manager"},
	{"state": "In Progress", "doc_status": "0", "allow_edit": "Hospitality Manager"},
	{"state": "Completed", "doc_status": "0", "allow_edit": "Hospitality Manager"},
	{"state": "Rejected", "doc_status": "0", "allow_edit": "Employee"},
	{"state": "Cancelled", "doc_status": "0", "allow_edit": "Hospitality Manager"},
]

# Submit + Reapply are open to the standard `Employee` role so any host on the
# desk can route their request to the Hospitality Manager. Manager-only actions
# (Approve / Reject / Start Work / Complete / Cancel) stay with Hospitality Manager.
TRANSITIONS = [
	{"state": "Draft", "action": "Submit for Approval", "next_state": "Pending Manager Approval", "allowed": "Employee"},
	{"state": "Pending Manager Approval", "action": "Approve", "next_state": "Approved", "allowed": "Hospitality Manager"},
	{"state": "Pending Manager Approval", "action": "Reject", "next_state": "Rejected", "allowed": "Hospitality Manager"},
	{"state": "Approved", "action": "Start Work", "next_state": "In Progress", "allowed": "Hospitality Manager"},
	{"state": "In Progress", "action": "Complete", "next_state": "Completed", "allowed": "Hospitality Manager"},
	{"state": "Approved", "action": "Cancel", "next_state": "Cancelled", "allowed": "Hospitality Manager"},
	{"state": "In Progress", "action": "Cancel", "next_state": "Cancelled", "allowed": "Hospitality Manager"},
	{"state": "Rejected", "action": "Reapply", "next_state": "Draft", "allowed": "Employee"},
]


def _ensure_workflow_state(state_name):
	if frappe.db.exists("Workflow State", state_name):
		return
	frappe.get_doc({
		"doctype": "Workflow State",
		"workflow_state_name": state_name,
		"style": _style_for(state_name),
	}).insert(ignore_permissions=True)


def _style_for(state):
	mapping = {
		"Draft": "Primary",
		"Pending Manager Approval": "Warning",
		"Approved": "Success",
		"In Progress": "Inverse",
		"Completed": "Success",
		"Rejected": "Danger",
		"Cancelled": "Danger",
	}
	return mapping.get(state, "Primary")


def _ensure_workflow_action(action_name):
	if frappe.db.exists("Workflow Action Master", action_name):
		return
	frappe.get_doc({
		"doctype": "Workflow Action Master",
		"workflow_action_name": action_name,
	}).insert(ignore_permissions=True)


def _ensure_workflow_state_field():
	meta = frappe.get_meta(DOCTYPE)
	if meta.get_field("workflow_state"):
		return
	# Hospitality Request doesn't have workflow_state column; add custom field
	if frappe.db.exists("Custom Field", {"dt": DOCTYPE, "fieldname": "workflow_state"}):
		return
	frappe.get_doc({
		"doctype": "Custom Field",
		"dt": DOCTYPE,
		"fieldname": "workflow_state",
		"label": "Workflow State",
		"fieldtype": "Link",
		"options": "Workflow State",
		"read_only": 1,
		"insert_after": "status",
	}).insert(ignore_permissions=True)


def execute():
	for state in STATES:
		_ensure_workflow_state(state["state"])
	for t in TRANSITIONS:
		_ensure_workflow_action(t["action"])

	_ensure_workflow_state_field()

	workflow = (
		frappe.get_doc("Workflow", WORKFLOW_NAME)
		if frappe.db.exists("Workflow", WORKFLOW_NAME)
		else frappe.new_doc("Workflow")
	)
	workflow.workflow_name = WORKFLOW_NAME
	workflow.document_type = DOCTYPE
	workflow.is_active = 1
	workflow.override_status = 0
	workflow.send_email_alert = 0
	workflow.workflow_state_field = "workflow_state"

	workflow.set("states", [])
	for st in STATES:
		workflow.append("states", {
			"state": st["state"],
			"doc_status": st["doc_status"],
			"allow_edit": st["allow_edit"],
			"update_field": "status" if st["state"] in ("Completed", "Cancelled") else None,
			"update_value": st["state"] if st["state"] in ("Completed", "Cancelled") else None,
		})

	workflow.set("transitions", [])
	for tr in TRANSITIONS:
		workflow.append("transitions", {
			"state": tr["state"],
			"action": tr["action"],
			"next_state": tr["next_state"],
			"allowed": tr["allowed"],
		})

	if workflow.is_new():
		workflow.insert(ignore_permissions=True)
	else:
		workflow.save(ignore_permissions=True)
