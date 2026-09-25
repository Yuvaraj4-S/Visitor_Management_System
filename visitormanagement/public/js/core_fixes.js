// Workarounds for two Frappe core (16.18) desk bugs, applied from this app so no
// core file is edited. Each is a no-op once core fixes it.

// Frappe's Phone control builds itself asynchronously: make_input() awaits the
// country list (localforage, or a server call on a browser's first visit) before
// it creates the flag, the ISD label and the country picker. The form does not
// wait — it refreshes every field straight away, so set_formatted_input() runs on
// a control whose `$isd` / `selected_icon` do not exist yet and throws
// "Cannot read properties of undefined (reading 'text')" on every new form with
// a Phone field (traced on Visitor Pass: Form.refresh_fields -> Layout.refresh ->
// ControlPhone.refresh -> set_formatted_input, all core frames).
//
// Fixed here, from the app, rather than in core: formatting waits until the
// control has finished building. set_formatted_input() is already async, so no
// caller relies on it having finished when it returns.
(function () {
	const Phone = frappe.ui && frappe.ui.form && frappe.ui.form.ControlPhone;
	if (!Phone || Phone.prototype.__vms_waits_for_build) return;
	Phone.prototype.__vms_waits_for_build = true;

	const make_input = Phone.prototype.make_input;
	Phone.prototype.make_input = function (...args) {
		this.__vms_built = make_input.apply(this, args);
		return this.__vms_built;
	};

	const set_formatted_input = Phone.prototype.set_formatted_input;
	Phone.prototype.set_formatted_input = async function (...args) {
		if (this.__vms_built) await this.__vms_built;
		return set_formatted_input.apply(this, args);
	};
})();

// The desk sidebar's app switcher renders every menu entry with add_app_item(),
// dividers included. A divider has neither `icon` nor `icon_url`, so each one
// became <img src="undefined"> and the browser requested /undefined (a 404 on
// every full desk load, for every user). Traced: SidebarHeader.add_app_item ->
// jQuery.parseHTML. A divider has nothing to show there, so it is skipped.
(function () {
	const Header = frappe.ui && frappe.ui.SidebarHeader;
	if (!Header || Header.prototype.__vms_skips_dividers) return;
	Header.prototype.__vms_skips_dividers = true;

	const add_app_item = Header.prototype.add_app_item;
	Header.prototype.add_app_item = function (item) {
		if (item && item.is_divider) return;
		return add_app_item.call(this, item);
	};
})();
