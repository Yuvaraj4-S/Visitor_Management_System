// Pickers for this app's own Link fields that point at another app's DocType:
// Employee and Job Applicant (HRMS), Supplier and Maintenance Visit (ERPNext).
//
// The people who fill these fields hold this app's roles and no permission on
// those DocTypes, and the app does not grant any: it never changes the
// permissions of a DocType it does not own. Two things make the fields work
// without that:
//   - the dropdown searches through this app's own query
//     (link_details.link_query), which returns a name and a label to anyone who
//     may create or edit the app record the field sits on;
//   - Frappe's per-pick check (frappe.client.validate_link, which requires
//     Select on the target DocType) is skipped for these fields only. The link
//     is still checked when the record is saved (BaseDocument._validate_links).
//
// Loaded through `doctype_js` for this app's DocTypes only, so no other form,
// app or core control is affected.
const VMS_LINK_QUERY = "visitormanagement.visitor_management.link_details.link_query";

// pickers: { fieldname | "table_field.fieldname": filters | (frm) => filters }
// Call from onload and refresh: the form keeps a per-record copy of each
// docfield, so the validation flag has to be set on the open record's copy.
function vms_setup_core_link_pickers(frm, pickers) {
	Object.entries(pickers).forEach(([key, filters]) => {
		const [table_field, fieldname] = key.includes(".") ? key.split(".") : [null, key];
		// `link_fieldname` tells link_details.link_query which Link field is searching: only that
		// field's own "Ignore User Permissions" mark is honoured (VAPT F2). It is not a filter on
		// the searched DocType; the server drops it from the filters.
		const query = () => ({
			query: VMS_LINK_QUERY,
			filters: {
				...((typeof filters === "function" ? filters(frm) : filters) || {}),
				link_fieldname: fieldname,
			},
		});

		if (table_field) {
			const grid = frm.fields_dict[table_field] && frm.fields_dict[table_field].grid;
			if (!grid) {
				return;
			}
			frm.set_query(fieldname, table_field, query);
			grid.update_docfield_property(fieldname, "ignore_link_validation", 1);
			return;
		}

		if (!frm.fields_dict[fieldname]) {
			return;
		}
		frm.set_query(fieldname, query);
		const df = frappe.meta.get_docfield(frm.doctype, fieldname, frm.docname);
		if (df) {
			df.ignore_link_validation = 1;
		}
	});
}
