// Copyright (c) 2026, Harthesh and contributors
// For license information, please see license.txt

frappe.ui.form.on("Supplier Visit", {
	refresh(frm) {
		frm.set_query("visitor_pass", function () {
			return {
				filters: {
					visitor_type: "Supplier",
				},
			};
		});
		apply_supplier_visit_ui(frm);
	},

	supplier_visit_mode(frm) {
		apply_supplier_visit_ui(frm);
	},
});

function apply_supplier_visit_ui(frm) {
	const is_delivery = frm.doc.supplier_visit_mode === "Delivery";
	const is_business_mode = !!frm.doc.supplier_visit_mode && frm.doc.supplier_visit_mode !== "Delivery";

	frm.toggle_display(
		[
			"purchase_order",
			"delivery_note",
			"goods_description",
			"delivery_vehicle_no",
			"driver_id_number",
			"dock__bay_assigned",
			"store_officer",
			"goods_received_by",
		],
		is_delivery
	);
	frm.toggle_display(
		[
			"meeting_subject",
			"meeting_start_time",
			"meeting_end_time",
			"meeting_room",
			"attendees",
			"refreshments_required",
			"refreshment_notes",
			"presentation_material",
			"nda_required",
			"documents_shared",
		],
		is_business_mode
	);
}
