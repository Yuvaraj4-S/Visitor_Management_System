"""Register a walk-in visitor at the gate in one short form (public/js/walk_in.js).

A guard used to fill the full Visitor Pass: twelve required fields over two tabs,
an email address the visitor often does not have, today's date and the times
typed by hand. This takes only what a guard can ask the person in front of them,
fills the rest (today, the channel), creates the pass as the guard — with the
guard's own permissions — and sends it for approval through the Visitor Type's
normal approval step. Nothing about who approves changes.
"""

import frappe
from frappe import _
from frappe.model.workflow import apply_workflow
from frappe.utils import cstr, today

from visitormanagement.visitor_management.lifecycle import log_failure
from visitormanagement.visitor_management.settings import feature_on
from visitormanagement.visitor_management.workflow_builder import ACTION_SUBMIT

# What the short form may set on the pass, and nothing else.
WALK_IN_FIELDS = (
	"visitor_type",
	"visitor_full_name",
	"mobile_number",
	"email_id",
	"company__organisation",
	"id_proof_type",
	"id_proof_number_entry",
	"visitor_photo",
	"id_proof_scan",
	"person_to_visit",
	"purpose_of_visit",
	"expected_checkin",
	"expected_checkout",
	"vehicle_number",
	"items_carried",
)


@frappe.whitelist()
def walk_in_enabled():
	"""Does VMS Settings let the gate register walk-ins? (Shows or hides New Walk-In.)"""
	return feature_on("enable_walk_in_registration")


@frappe.whitelist(methods=["POST"])
def create_walk_in_pass(values: str | dict):
	"""Create a walk-in Visitor Pass for today and send it for approval.

	Returns the pass name and whether it went for approval. If the approval step
	refuses (say a document is missing) the pass is kept as a Draft and the reason
	is returned, so the guard can finish it on the pass itself.
	"""
	if not feature_on("enable_walk_in_registration"):
		frappe.throw(
			_("Walk-in registration is switched off in VMS Settings. Ask the host to raise a Visitor Pass."),
			title=_("Walk-Ins Switched Off"),
		)
	frappe.has_permission("Visitor Pass", "create", throw=True)
	values = frappe.parse_json(values) or {}
	doc = frappe.new_doc("Visitor Pass")
	for fieldname in WALK_IN_FIELDS:
		value = values.get(fieldname)
		if value not in (None, ""):
			doc.set(fieldname, cstr(value).strip() if isinstance(value, str) else value)
	doc.request_channel = "Walk-In"
	doc.visit_date = today()
	doc.insert()

	frappe.db.savepoint("vms_walk_in_submit")
	try:
		apply_workflow(doc, ACTION_SUBMIT)
	except Exception as exc:
		frappe.db.rollback(save_point="vms_walk_in_submit")
		frappe.clear_messages()
		if not isinstance(exc, frappe.ValidationError):
			log_failure(f"VMS Walk-In Submit: {doc.name}")
		return {
			"name": doc.name,
			"submitted": False,
			"message": frappe.utils.strip_html(cstr(exc)) or _("It could not be sent for approval."),
		}
	doc.reload()
	return {"name": doc.name, "submitted": True, "workflow_state": doc.workflow_state}
