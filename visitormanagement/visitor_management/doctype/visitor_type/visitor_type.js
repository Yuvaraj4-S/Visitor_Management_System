// Copyright (c) 2026, Finstein and contributors
// For license information, please see license.txt

frappe.ui.form.on("Visitor Type", {
	refresh(frm) {
		apply_badge_config_visibility(frm);
	},
});

// Badge prefix/colour only make sense when badges are switched on globally.
// When VMS Settings → Enable Badge is off, hide them here too, so the Badge
// Configuration is controlled from one place and never shown when disabled.
// (Mirrors apply_badge_visibility() on Visitor Pass.)
function apply_badge_config_visibility(frm) {
	const BADGE_FIELDS = ["badge_prefix", "badge_colour"];
	const setHidden = (hide) => {
		BADGE_FIELDS.forEach((fn) => {
			frm.set_df_property(fn, "hidden", hide ? 1 : 0);
			frm.toggle_display(fn, !hide);
			frm.refresh_field(fn);
		});
	};
	// Hide first so the fields never flash on before the async fetch resolves.
	setHidden(true);
	frappe.db.get_value("VMS Settings", "VMS Settings", "enable_badge").then((r) => {
		const on = !!cint(((r && r.message) || {}).enable_badge);
		setHidden(!on);
	});
}
