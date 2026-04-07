import frappe


def _set_notification_values(name, values):
    if not frappe.db.exists("Notification", name):
        return

    frappe.db.set_value("Notification", name, values, update_modified=False)


def execute():
    _set_notification_values(
        "Gate Security Alert",
        {
            "enabled": 1,
            "send_system_notification": 0,
        },
    )
    _set_notification_values(
        "VMS Approval Email",
        {
            "enabled": 0,
            "send_system_notification": 0,
        },
    )
    _set_notification_values(
        "VMS Gate Security Alert",
        {
            "enabled": 0,
            "send_system_notification": 0,
        },
    )
    _set_notification_values(
        "VMS Host Alert",
        {
            "enabled": 0,
            "send_system_notification": 0,
        },
    )
