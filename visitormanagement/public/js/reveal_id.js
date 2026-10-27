// "Reveal Full ID": the one way to see a whole ID number on a Visitor Pass or a
// Visitor Blacklist entry. Nobody is shown one by default, Security and System
// Manager included; they can ask for it here. The server checks the role and the
// right to read the record again, and records who revealed it and when, never
// the number (visitor_management/id_numbers.py: reveal_id). The number is shown
// in a dialog that closes on its own and is never written into the form, so it
// cannot be saved, copied to another record or left on screen.
// Loaded on both forms through doctype_js in hooks.py.
/* global visitormanagement */
frappe.provide("visitormanagement.id_reveal");

visitormanagement.id_reveal.METHOD = "visitormanagement.visitor_management.id_numbers.reveal_id";
// Mirrors id_numbers.REVEAL_ROLES. Only hides a button nobody else could use.
visitormanagement.id_reveal.ROLES = ["Security", "System Manager"];
visitormanagement.id_reveal.SHOW_SECONDS = 20;

visitormanagement.id_reveal.add_button = function (frm) {
	const reveal = visitormanagement.id_reveal;
	if (frm.is_new() || !reveal.ROLES.some((role) => frappe.user.has_role(role))) {
		return;
	}
	if (!reveal.people(frm).length) {
		return;
	}
	frm.add_custom_button(__("Reveal Full ID"), () => reveal.ask(frm));
};

// Whose number can be revealed: the visitor (or the barred person) and, on a
// pass, each saved accompanying visitor. `row` is the group member row name.
visitormanagement.id_reveal.people = function (frm) {
	const people = [];
	if (frm.doc.id_proof_masked) {
		const name = frm.doc.visitor_full_name || frm.doc.visitor_name || frm.doc.name;
		people.push({ value: "", label: `${name} · ${frm.doc.id_proof_masked}` });
	}
	(frm.doc.group_members || []).forEach((row) => {
		if (row.id_proof_masked && !row.__islocal) {
			people.push({
				value: row.name,
				label: __("{0} (accompanying visitor, row {1}) · {2}", [
					row.visitor_name || "",
					row.idx,
					row.id_proof_masked,
				]),
			});
		}
	});
	return people;
};

visitormanagement.id_reveal.ask = function (frm) {
	const reveal = visitormanagement.id_reveal;
	const people = reveal.people(frm);
	const fields = [];
	if (people.length > 1) {
		fields.push({
			fieldtype: "Select",
			fieldname: "row",
			label: __("Whose ID"),
			options: people,
			default: people[0].value,
		});
	}
	fields.push(
		{
			fieldtype: "Data",
			fieldname: "reason",
			label: __("Reason (optional)"),
			length: 140,
		},
		{
			fieldtype: "HTML",
			fieldname: "note",
			options: `<div class="text-muted small">${__(
				"The reveal is recorded with your name and the time."
			)}</div>`,
		}
	);
	const dialog = new frappe.ui.Dialog({
		title: people.length > 1 ? __("Reveal Full ID") : people[0].label,
		fields: fields,
		primary_action_label: __("Reveal"),
		primary_action: (values) => {
			dialog.hide();
			const row = people.length > 1 ? values.row || "" : people[0].value;
			const person = people.find((p) => p.value === row) || people[0];
			frappe
				.call({
					method: reveal.METHOD,
					type: "POST",
					args: {
						doctype: frm.doctype,
						name: frm.doc.name,
						row: row || null,
						reason: values.reason || null,
					},
					freeze: true,
				})
				.then((r) => {
					if (r && r.message) {
						reveal.show(frm, person.label, r.message);
					}
				});
		},
	});
	dialog.show();
};

visitormanagement.id_reveal.show = function (frm, who, number) {
	const reveal = visitormanagement.id_reveal;
	let left = reveal.SHOW_SECONDS;
	const dialog = new frappe.ui.Dialog({
		title: __("Full ID Number"),
		fields: [{ fieldtype: "HTML", fieldname: "full_id" }],
		primary_action_label: __("Close"),
		primary_action: () => dialog.hide(),
	});
	const $wrapper = dialog.fields_dict.full_id.$wrapper;
	$wrapper.html(
		`<div class="text-muted small">${frappe.utils.escape_html(who)}</div>
		<div class="vms-full-id" style="font-size: 22px; font-family: monospace; letter-spacing: 1px; margin: 8px 0;">${frappe.utils.escape_html(
			number
		)}</div>
		<div class="text-muted small vms-full-id-timer"></div>`
	);
	const tick = () => {
		$wrapper
			.find(".vms-full-id-timer")
			.text(__("Closes in {0} seconds. This reveal has been recorded.", [left]));
		left -= 1;
		if (left < 0) {
			dialog.hide();
		}
	};
	tick();
	const timer = setInterval(tick, 1000);
	dialog.onhide = () => {
		clearInterval(timer);
		$wrapper.empty();
		// The timeline now carries the note; only reload a form with nothing unsaved.
		if (!frm.is_dirty()) {
			frm.reload_doc();
		}
	};
	dialog.show();
};

["Visitor Pass", "Visitor Blacklist"].forEach((doctype) => {
	frappe.ui.form.on(doctype, {
		refresh(frm) {
			visitormanagement.id_reveal.add_button(frm);
		},
	});
});
