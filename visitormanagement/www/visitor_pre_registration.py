import frappe
from frappe import _
import base64
from visitormanagement.visitor_management.portal import submit_pre_registration

def get_context(context):
    context.no_cache = 1

@frappe.whitelist(allow_guest=True)
def submit_form():
    try:
        payload = dict(frappe.form_dict)

        id_proof_file = frappe.request.files.get("id_proof_scan")
        visitor_photo_file = frappe.request.files.get("visitor_photo")

        if id_proof_file:
            payload["id_proof_scan"] = base64.b64encode(id_proof_file.stream.read()).decode()
            payload["id_proof_scan_filename"] = id_proof_file.filename

        if visitor_photo_file:
            payload["visitor_photo"] = base64.b64encode(visitor_photo_file.stream.read()).decode()
            payload["visitor_photo_filename"] = visitor_photo_file.filename

        result = submit_pre_registration(payload)
        return result.get("name")

    except Exception as e:
        frappe.log_error(frappe.get_traceback(), "Pre-Registration Submit Error")
        frappe.throw(_("Error submitting form: {0}").format(str(e)))
