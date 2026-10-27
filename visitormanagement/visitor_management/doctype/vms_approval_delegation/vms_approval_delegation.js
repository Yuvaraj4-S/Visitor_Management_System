// For license information, please see license.txt

frappe.ui.form.on("VMS Approval Delegation", {
	refresh(frm) {
		// Only a System Manager hands over someone else's approvals; the server
		// refuses it too.
		frm.toggle_enable("approver", frappe.user.has_role("System Manager"));
	},
});
