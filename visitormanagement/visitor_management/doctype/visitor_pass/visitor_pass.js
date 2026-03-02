// Copyright (c) 2026, Harthesh
// Visitor Pass Client Script (Final Clean Combined Version)

frappe.ui.form.on('Visitor Pass', {

  onload(frm) {
    vms_apply(frm);
  },

  refresh(frm) {
    vms_apply(frm);
  },

  person_to_visit(frm) {
    if (!frm.doc.person_to_visit) return;

    frappe.db.get_value(
      'Employee',
      frm.doc.person_to_visit,
      'department',
      r => {
        if (r) frm.set_value('host_department', r.department);
      }
    );
  },

  visitor_type(frm) {

    const colours = {
      'Contractor': 'Orange',
      'Candidate':  'Purple',
      'Customer':   'Green',
      'Supplier':   'Teal',
      'VIP':        'Gold'
    };

    if (colours[frm.doc.visitor_type]) {
      frm.set_value('badge_colour', colours[frm.doc.visitor_type]);
    }

    vms_apply(frm);
  }

});


// CHILD TABLE — Visitor Item
frappe.ui.form.on('Visitor Item', {

  erp_item_code(frm, cdt, cdn) {

    const row = locals[cdt][cdn];
    if (!row.erp_item_code) return;

    frappe.db.get_value(
      'Item',
      row.erp_item_code,
      ['item_name', 'stock_uom'],
      function(data) {
        if (data) {
          frappe.model.set_value(cdt, cdn, 'item_name', data.item_name);
          frappe.model.set_value(cdt, cdn, 'uom', data.stock_uom);
          frappe.model.set_value(cdt, cdn, 'is_new_item', 0);

          const grid_row = frm.get_field('visitor_items')?.grid?.get_row(cdn);
          if (grid_row) {
            grid_row.set_field_property('item_name', 'read_only', 1);
            grid_row.set_field_property('uom', 'read_only', 1);
          }
        }
      }
    );
  },

  is_new_item(frm, cdt, cdn) {

    const row = locals[cdt][cdn];

    if (row.is_new_item) {
      frappe.model.set_value(cdt, cdn, 'erp_item_code', '');

      const grid_row = frm.get_field('visitor_items')?.grid?.get_row(cdn);
      if (grid_row) {
        grid_row.set_field_property('item_name', 'read_only', 0);
        grid_row.set_field_property('uom', 'read_only', 0);
      }
    }
  }

});


// MAIN UI CONTROLLER
function vms_apply(frm) {

  const status = frm.doc.status;

  // ─────────────────────────────────────────────
  // Dynamic Visitor Type Rules
  // ─────────────────────────────────────────────
  apply_visitor_type_rules(frm);

  // Hospitality Section visibility
  const approved_states = [
    'Approved',
    'Items Verified',
    'Checked-In',
    'Checked-Out'
  ];

  const approved = approved_states.includes(status);

  frm.toggle_display([
    'hospitality_section',
    'meal_required',
    'meal_type',
    'meal_count',
    'special_diet',
    'rest_area',
    'hospitality_note',
    'food_dept_employee',
    'food_status'
  ], approved);

  if (approved && status === 'Approved' && !frm.doc.__islocal) {
    frappe.show_alert({
      message: 'Visit APPROVED! Security must verify items before badge issuance.',
      indicator: 'blue'
    }, 7);
  }

  if (frm.doc.item_verification_status === 'All Verified') {
    frappe.show_alert({
      message: '✓ All visitor items verified!',
      indicator: 'green'
    }, 5);
  } else if (frm.doc.item_verification_status === 'Partial') {
    frappe.show_alert({
      message: '⚠ Partial verification — badge not issued.',
      indicator: 'orange'
    }, 5);
  }

  frm.remove_custom_button('Check In');
  frm.remove_custom_button('Check Out');

  if (status === 'Items Verified' && !frm.doc.__islocal) {

    frm.add_custom_button('Check In', function() {

      frappe.confirm(
        `Confirm CHECK-IN for <b>${frm.doc.visitor_name}</b>?`,
        function() {

          frappe.call({
            method: 'visitor_management.api.visitor_gate.visitor_checkin',
            args: { docname: frm.doc.name },
            callback: function(r) {
              if (!r.exc) {
                frm.reload_doc();
              }
            }
          });

        }
      );

    }).addClass('btn-success');
  }

  if (status === 'Checked-In' && !frm.doc.__islocal) {

    frm.add_custom_button('Check Out', function() {

      frappe.confirm(
        `Confirm CHECK-OUT for <b>${frm.doc.visitor_name}</b>?`,
        function() {

          frappe.call({
            method: 'visitor_management.api.visitor_gate.visitor_checkout',
            args: { docname: frm.doc.name },
            callback: function(r) {
              if (!r.exc) {
                frm.reload_doc();
              }
            }
          });

        }
      );

    }).addClass('btn-danger');
  }

  frm.refresh_fields();
}


// ─────────────────────────────────────────────
// Dynamic field rules based on visitor_type
// ─────────────────────────────────────────────
function apply_visitor_type_rules(frm) {

  const vt = frm.doc.visitor_type || "";

  // vehicle_number mandatory for Supplier
  frm.set_df_property('vehicle_number', 'reqd', vt === 'Supplier' ? 1 : 0);

  // Company label change
  const company_label = {
    'Contractor': 'Contractor Company',
    'Customer':   'Client Company',
    'Supplier':   'Vendor Company',
    'Candidate':  'Applied From (Optional)',
    'VIP':        'Organisation / Institution'
  };

  if (company_label[vt]) {
    frm.set_df_property('company', 'label', company_label[vt]);
  }

  // Sub-form reminder
  const sub_form_map = {
    'Contractor': 'Contractor Visit',
    'Candidate':  'Candidate Visit',
    'Customer':   'Customer Visit',
    'Supplier':   'Supplier Visit',
    'VIP':        'VIP Visit'
  };

  if (vt && sub_form_map[vt] && frm.doc.__islocal) {
    frappe.show_alert({
      message: 'Remember to also fill the ' + sub_form_map[vt] + ' form.',
      indicator: 'blue'
    }, 5);
  }
}