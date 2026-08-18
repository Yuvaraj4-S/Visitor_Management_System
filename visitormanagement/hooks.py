app_name = "visitormanagement"
app_title = "Visitor Management"
app_publisher = "Finstein"
app_description = "Visitor Management System"
app_email = "yuvaraj.s@finstein.ai"
app_license = "MIT"
app_icon = "octicon octicon-organization"
app_color = "#1A56DB"
app_logo_url = "/assets/visitormanagement/images/logo.png"
source_link = "https://github.com/Yuvaraj4-S/Visitor_Management_System"
documentation = "https://github.com/Yuvaraj4-S/Visitor_Management_System/blob/main/docs/README.pdf"

# Apps
# ------------------

required_apps = ["erpnext", "hrms"]

# Shown as a tile on the /apps screen. Without this the app has no entry point
# there at all — the workspace was only reachable by typing its URL.
add_to_apps_screen = [
    {
        "name": "visitormanagement",
        "logo": "/assets/visitormanagement/images/logo-128.png",
        "title": "Visitor Management",
        # v16 serves the desk at /desk; erpnext/hrms/india_compliance all use
        # that prefix here, and /app only works via a redirect.
        "route": "/desk/visitor-management",
    }
]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# Desk CSS. Currently one rule: a chart legend variable Frappe leaves
# undefined in dark mode, which made donut chart values invisible.
app_include_css = "/assets/visitormanagement/css/visitormanagement.css"
# app_include_js = "/assets/visitormanagement/js/visitormanagement.js"

# include js, css files in header of web template
# web_include_css = "/assets/visitormanagement/css/visitormanagement.css"
# web_include_js = "/assets/visitormanagement/js/visitormanagement.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "visitormanagement/public/scss/website"

# include js, css files in header of web form
webform_include_js = {
    "visitor-pre-registration-form": "public/js/id_validators.js",
}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
doctype_js = {"Job Applicant": "public/js/job_applicant.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "visitormanagement/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "visitormanagement.utils.jinja_methods",
# 	"filters": "visitormanagement.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "visitormanagement.install.before_install"

# All setup lives in visitormanagement/setup.py — idempotent, and run on both
# install and migrate. The app deliberately ships no patches: `bench install-app`
# marks patches complete without running them, so a fresh site would get none of
# the master data.
after_install = "visitormanagement.setup.after_install"

# Uninstallation
# ------------

# before_uninstall = "visitormanagement.uninstall.before_uninstall"
# after_uninstall = "visitormanagement.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "visitormanagement.utils.before_app_install"
# after_app_install = "visitormanagement.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "visitormanagement.utils.before_app_uninstall"
# after_app_uninstall = "visitormanagement.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "visitormanagement.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }
permission_query_conditions = {
	"Visitor Pass": "visitormanagement.permissions.get_visitor_pass_permission_query_conditions",
	"Visitor Invitation": "visitormanagement.permissions.get_visitor_invitation_permission_query_conditions",
}

has_permission = {
	"Visitor Pass": "visitormanagement.permissions.has_visitor_pass_permission",
	"Visitor Invitation": "visitormanagement.permissions.has_visitor_invitation_permission",
}

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }
doc_events = {
	"Job Applicant": {
		"after_insert": "visitormanagement.visitor_management.candidate_flow.maybe_create_invitation",
		"on_update": "visitormanagement.visitor_management.candidate_flow.maybe_create_invitation",
	},
	# The visitor portal requires allow_guests_to_upload_files, which is a
	# site-wide switch. This keeps anonymous uploads to what the portal asks
	# for; logged-in users of any app are untouched.
	"File": {
		"before_insert": "visitormanagement.visitor_management.portal_upload.guard_guest_upload",
	},
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"cron": {
		"0 7 * * *": [
			"visitormanagement.visitor_management.tasks.send_daily_hospitality_digest"
		]
	},
	"hourly": [
		"visitormanagement.visitor_management.tasks.flag_no_show_passes"
	]
}

# Testing
# -------

# before_tests = "visitormanagement.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "visitormanagement.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "visitormanagement.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["visitormanagement.utils.before_request"]

# The visitor portal is a guest-facing page that collects ID documents, and the
# invitation token rides in the query string — see response_headers.py for what
# this sets and why. Scoped to this app's own routes.
after_request = [
	"visitormanagement.visitor_management.response_headers.set_portal_security_headers",
]

# Job Events
# ----------
# before_job = ["visitormanagement.utils.before_job"]
# after_job = ["visitormanagement.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"visitormanagement.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

# `import_fixtures` walks the fixtures directory in filename order, so a Workflow
# would otherwise be imported before the states and actions it links to.
# fixture_auto_order makes `bench export-fixtures` number each file by its
# position in the list below, which makes that filename order match this order.
fixture_auto_order = True

fixtures = [
    # 1. Roles first — DocType permissions, workflow transitions and notification
    #    recipients all reference them.
    {
        "doctype": "Role",
        "filters": [
            ["name", "in", [
                "CEO",
                "Facility Manager",
                "Factory Tour Coordinator",
                "Front Office Executive",
                "Greeting Staff",
                "HOD",
                "Host Employee",
                "Hospitality Manager",
                "Hospitality User",
                "Security",
                "Transport Coordinator",
            ]]
        ]
    },
    # 2. Workflow states.
    {
        "doctype": "Workflow State",
        "filters": [
            ["name", "in", [
                "Draft",
                "Pending Approval",
                "Pending System Manager",
                "Pending Sales Manager",
                "Pending HR Manager",
                "Pending HOD",
                "Pending CEO",
                "Approved",
                "Rejected",
                "Cancelled",
                "Items Verified",
                "Checked-In",
                "Checked-Out",
            ]]
        ]
    },
    # 3. Workflow Action Master holds the action *names* the transitions link to.
    #    (The similarly-named "Workflow Action" doctype holds per-document pending
    #    approvals — transactional rows that must never be shipped as fixtures.)
    {
        "doctype": "Workflow Action Master",
        "filters": [
            ["name", "in", [
                "Submit",
                "Approve",
                "Reject",
                "Reapply",
                "Cancel",
            ]]
        ]
    },
    # 4. Workflows last, once every state, action and role they reference exists.
    #    "Visitor Pass Approval" is deliberately NOT shipped — its lanes are
    #    generated from the Visitor Type masters by
    #    visitor_management/workflow_builder.py, so a fixture would overwrite
    #    whatever approver roles the site has configured.
    {
        "doctype": "Workflow",
        "filters": [
            ["name", "in", [
                "Conference Room Booking Approval",
                "Hospitality Request Approval",
            ]]
        ]
    },
    # Note: Custom Fields on Job Applicant, and the Visitor Pass property setters,
    # ship as customisations (visitor_management/custom/*.json) and are applied by
    # `sync_customizations` — they are deliberately not fixtures.
]

# before_migrate clears Custom Fields that have since been promoted into their
# DocType JSON; after_migrate re-asserts the whole configuration.
before_migrate = "visitormanagement.setup.before_migrate"
after_migrate = "visitormanagement.setup.after_migrate"
