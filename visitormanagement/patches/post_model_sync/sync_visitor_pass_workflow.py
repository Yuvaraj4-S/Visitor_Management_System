import frappe


def execute():
    old_workflows = [
        "VMS Contractor Approval",
        "CUSTOMER FLOW",
        "CANDIDATE FLOW",
        "VIP FLOW",
    ]

    for workflow_name in old_workflows:
        if frappe.db.exists("Workflow", workflow_name):
            frappe.db.set_value("Workflow", workflow_name, "is_active", 0, update_modified=False)

    if frappe.db.exists("Workflow", "Visitor Pass Approval"):
        frappe.db.set_value("Workflow", "Visitor Pass Approval", "is_active", 1, update_modified=False)
