app_name = "visitormanagement"
app_title = "Visitor Management"
app_publisher = "Finstein"
app_description = "Visitor Management System"
app_email = "yuvaraj.s@finstein.ai"
app_license = "MIT"
app_icon = "octicon octicon-organization"
app_color = "#1A56DB"
# No `app_logo_url`: Frappe's navbar_settings.get_app_logo picks the site-wide
# navbar / login logo from that hook across all installed apps, so declaring one
# changed the logo for the whole site. The app's tile keeps its logo through
# add_to_apps_screen below.
source_link = "https://github.com/Yuvaraj4-S/Visitor_Management_System"
# README.md is the authoritative install reference. docs/README.pdf is the printable
# manual from the 1.0.0 release and does not cover everything README.md now does.
documentation = "https://github.com/Yuvaraj4-S/Visitor_Management_System/blob/main/README.md"

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
		# Frappe 15 serves the desk at /app (v16 moved it to /desk).
		"route": "/app/visitor-management",
	}
]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# Deliberately none. Anything included here loads on every desk page of the site,
# for every app, and this app does not change core desk behaviour or styling.
# (Earlier builds shipped core_fixes.js, which patched Frappe's Phone control and
# app switcher, and visitormanagement.css, which recoloured every chart legend.)

# include js, css files in header of web template
# web_include_css = "/assets/visitormanagement/css/visitormanagement.css"
# web_include_js = "/assets/visitormanagement/js/visitormanagement.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "visitormanagement/public/scss/website"

# include js, css files in header of web form
# Keyed by the web form's DocType, not its route: Frappe 15's
# WebForm.add_custom_context_and_script looks this hook up with `context.doc_type`
# (web_form.py, get_code_files_via_hooks). Under the old route key the file was
# never included. It is appended to the form script and rendered through Jinja,
# and is a self-contained IIFE that only defines window.VMS_IDValidators.
webform_include_js = {
	"Visitor Pass": "public/js/id_validators.js",
}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# core_link_pickers.js: pickers for this app's own Link fields that point at HRMS /
# ERPNext DocTypes (see the file). Only this app's DocTypes load it.
doctype_js = {
	"Job Applicant": "public/js/job_applicant.js",
	"Visitor Pass": "public/js/core_link_pickers.js",
	"Visitor Invitation": "public/js/core_link_pickers.js",
	"Security Log": "public/js/core_link_pickers.js",
	"Hospitality Request": "public/js/core_link_pickers.js",
	"Conference Room Booking": "public/js/core_link_pickers.js",
}
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

# Records which shared records (roles, workflow states and actions) the site
# already had, before DocType sync creates any, so uninstall removes only ours.
before_install = "visitormanagement.setup.before_install"

# All setup lives in visitormanagement/setup.py — idempotent, and run on both
# install and migrate. The app deliberately ships no patches: `bench install-app`
# marks patches complete without running them, so a fresh site would get none of
# the master data.
after_install = "visitormanagement.setup.after_install"

# Uninstallation
# ------------

# Removes what `bench uninstall-app` leaves behind (see uninstall.py). This build
# never changes the System Setting `allow_guests_to_upload_files`; uninstall only
# reverts it on a site where the Frappe 16 build recorded switching it on.
before_uninstall = "visitormanagement.uninstall.before_uninstall"
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
	"Hospitality Request": "visitormanagement.permissions.get_hospitality_request_permission_query_conditions",
	# Bookings were readable company-wide by every Employee — meeting_title included,
	# so "Board interview — CFO candidate" was visible to anyone. Scoped to the owner,
	# the booked_by employee and Facility/System Manager. The calendar feed and the
	# Daily Booking Schedule keep every slot visible but show "Busy" for bookings the
	# viewer may not open (see permissions.py).
	"Conference Room Booking": "visitormanagement.permissions.get_conference_room_booking_permission_query_conditions",
}

has_permission = {
	"Visitor Pass": "visitormanagement.permissions.has_visitor_pass_permission",
	"Visitor Invitation": "visitormanagement.permissions.has_visitor_invitation_permission",
	"Hospitality Request": "visitormanagement.permissions.has_hospitality_request_permission",
	"Conference Room Booking": "visitormanagement.permissions.has_conference_room_booking_permission",
	# File is Frappe's DocType; nothing about it is changed. Frappe asks every app's
	# hook in turn and a hook can only refuse (frappe/permissions.py
	# has_controller_permissions). This one refuses an ID scan or visa copy attached
	# to one of this app's records to a user outside its audience, and gives no
	# answer (None) for every other file, so Frappe's own rule decides those exactly
	# as before.
	"File": "visitormanagement.permissions.has_id_document_file_permission",
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
	# The visitor portal does not use guest uploads (its files travel inside the
	# submission). If a site turns allow_guests_to_upload_files on for another
	# app, this refuses anonymous uploads aimed at this app's records; other
	# guest uploads and logged-in users of any app are untouched.
	"File": {
		"before_insert": "visitormanagement.visitor_management.portal_upload.guard_guest_upload",
	},
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"cron": {
		"0 7 * * *": ["visitormanagement.visitor_management.tasks.send_daily_hospitality_digest"],
		# Off-peak, and independent of the digest/no-show/overstay jobs below. This is
		# OFF in effect on every site until an administrator turns on VMS Settings ->
		# Enable Data Retention Purge: the function re-checks that flag first and
		# returns immediately when it is unset, so scheduling it here cannot delete
		# anything on a site that has not explicitly opted in.
		"0 3 * * *": ["visitormanagement.visitor_management.tasks.purge_expired_visitor_data"],
		# Always on: desk uploads left on an unsaved "new-..." form (see the
		# function). Unlike the retention purge it touches no saved record, and it
		# never touches guest uploads.
		"30 3 * * *": ["visitormanagement.visitor_management.tasks.purge_abandoned_uploads"],
	},
	"hourly": [
		"visitormanagement.visitor_management.tasks.flag_no_show_passes",
		# The mirror of the no-show job: that one catches the visitor who never
		# arrived, this one the visitor who arrived and never left. Nothing chased
		# the second case, so the building's own answer to "who is inside" drifted.
		"visitormanagement.visitor_management.tasks.flag_overstaying_visitors",
		# Marks invitations whose expiry has passed as "Expired" (one conditional
		# UPDATE, idempotent). Without it an unused invitation stayed "Sent" for ever,
		# and the retention purge, which takes only finished invitations, never
		# reached it.
		"visitormanagement.visitor_management.doctype.visitor_invitation.visitor_invitation.expire_due_invitations",
	],
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

# What Frappe's own "Personal Data Download Request" and "Personal Data Deletion
# Request" cover for a person whose email address is on a visitor record. Frappe
# finds a person's rows by one column holding their email (`filter_by`), and on a
# deletion request overwrites `redact_fields` with placeholders
# (frappe/website/doctype/personal_data_deletion_request); the row itself stays,
# so the visit count and the gate's audit trail survive. Only this app's own
# DocTypes are listed.
#
# Not listed, because they hold no email address to match on: Security Log,
# Hospitality Request, Conference Room Booking, Contact Trace Record and group
# member rows. The retention purge (VMS Settings > Data Retention & Purge) is
# what clears those, together with the files. Visitor Blacklist is deliberately
# never listed: an entry must keep identifying the person it bars.
# `invitation_token` is left out as well: it is a unique column, and Frappe would
# write the same placeholder into every invitation of that person.
user_data_fields = [
	{
		"doctype": "Visitor Pass",
		"filter_by": "email_id",
		"redact_fields": [
			"visitor_full_name",
			"mobile_number",
			"mobile_digits",
			"id_proof_number",
			"id_proof_number_masked",
			"vehicle_number",
			"company__organisation",
			"visitor_summary",
			"id_proof_scan",
			"visitor_photo",
			"gate_verified_photo",
			"custom_visa_copy",
			"qr_code_image",
		],
	},
	{
		"doctype": "Visitor Invitation",
		"filter_by": "visitor_email",
		"redact_fields": [
			"visitor_full_name",
			"visitor_mobile",
			"purpose_of_visit",
			"portal_submission_url",
		],
	},
]

# Authentication and authorization
# --------------------------------

# Runs at the end of Frappe's own authentication of each request, when the user is
# known whichever way they signed in (session, API key or token). It only looks at
# requests for a private file, and only refuses one that is an ID scan or visa copy
# of this app's records, to a user outside its audience (see permissions.py). Such
# a request is served by code that does not consult `has_permission` hooks
# (frappe/utils/response.py download_private_file), which is why the File hook
# above is not enough on its own.
auth_hooks = ["visitormanagement.permissions.guard_id_document_request"]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

# No fixtures. This app used to ship its Roles, Workflow States and Workflow Action
# Masters as fixtures, and `import_fixtures` force-imports every file in
# `fixtures/` on every migrate. Those records are shared by name across apps:
# "Draft", "Approved", "Rejected", "Cancelled", "Pending Approval" are every
# workflow's states, "Approve"/"Reject"/"Cancel" every workflow's actions, and a
# role called "HOD", "CEO" or "Security" may well belong to another app. Each
# migrate reset their colours, icons and role settings to this app's copy.
#
# All of them are now created only when missing, and never updated:
#   - roles by `setup._ensure_roles`;
#   - states and actions by `workflow_builder._ensure_workflow_state` /
#     `_ensure_workflow_action`, called from `build_workflow` and from
#     `setup._ensure_seed_workflow_dependencies`.
#
# Workflows are not fixtures either: "Visitor Pass Approval" is generated from the
# Visitor Type masters (workflow_builder.py) and the other two are seeded once from
# `workflow_seed.json` (setup._seed_static_workflows). Custom Fields on Job
# Applicant ship as customisations (visitor_management/custom/*.json).
fixtures = []

# before_migrate clears Custom Fields that have since been promoted into their
# DocType JSON; after_migrate re-asserts the whole configuration.
before_migrate = "visitormanagement.setup.before_migrate"
after_migrate = "visitormanagement.setup.after_migrate"
