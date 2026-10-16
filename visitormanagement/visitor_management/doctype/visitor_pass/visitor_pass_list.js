// After approval the workflow state stays "Approved" while the gate moves the
// pass on (Items Verified, Checked-In, Checked-Out) and the no-show job marks a
// visitor who never came. The "Approved" state is set to leave the indicator to
// the document (Workflow Document State "avoid_status_override", written by
// workflow_builder), and Frappe then asks this function (frappe.get_indicator) —
// in this list and in the form's header alike. Every other state (the approval
// lanes, Rejected, Cancelled) is shown by the workflow as before.
const VISITOR_PASS_GATE_INDICATORS = {
	Approved: "green",
	"Items Verified": "blue",
	"Checked-In": "green",
	"Checked-Out": "blue",
};

frappe.listview_settings["Visitor Pass"] = {
	add_fields: ["status", "no_show", "workflow_state", "request_channel"],

	onload(listview) {
		// The gate's short form for a visitor who has just arrived (public/js/walk_in.js).
		if (
			frappe.model.can_create("Visitor Pass") &&
			typeof vms_when_walk_in_enabled === "function"
		) {
			vms_when_walk_in_enabled(() =>
				listview.page.add_inner_button(__("New Walk-In"), () => vms_open_walk_in_dialog())
			);
		}
	},

	get_indicator(doc) {
		if (doc.docstatus !== 1) return;
		if (cint(doc.no_show) && ["Approved", "Items Verified"].includes(doc.status)) {
			return [__("No-Show"), "red", "no_show,=,1"];
		}
		const colour = VISITOR_PASS_GATE_INDICATORS[doc.status];
		if (colour) {
			return [__(doc.status), colour, "status,=," + doc.status];
		}
	},
};
