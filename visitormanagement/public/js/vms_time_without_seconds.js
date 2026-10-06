// Expected check-in / check-out on VMS doctypes are captured to the minute.
// Show and pick them as HH:mm without changing the site-wide System Settings
// time format (which other apps on the site still rely on).
(() => {
	const VMS_TIME_FIELDS = {
		"Visitor Pass": ["expected_checkin", "expected_checkout"],
		"Visitor Invitation": ["expected_checkin", "expected_checkout"],
		"Walk In Visitor Request": ["expected_checkin", "expected_checkout"],
		"Security Log": ["expected_checkin", "expected_checkout"],
		"Visit Time Extension": ["previous_checkout", "new_checkout"],
	};

	const EXPECTED_TIME_FIELDNAMES = ["expected_checkin", "expected_checkout"];

	// Report columns (e.g. Active Visitors) carry no `parent`, so match them by fieldname.
	// Dialog fields can opt in with `hide_seconds: 1`.
	const is_vms_time_field = (df) =>
		!!df &&
		df.fieldtype === "Time" &&
		(!!df.hide_seconds ||
			(df.parent
				? (VMS_TIME_FIELDS[df.parent] || []).includes(df.fieldname)
				: EXPECTED_TIME_FIELDNAMES.includes(df.fieldname)));

	const format_without_seconds = (value) => {
		if (!value) return "";
		const parsed = moment(String(value), ["HH:mm:ss", "H:mm:ss", "HH:mm:ss.SSSSSS", "HH:mm", "H:mm"]);
		return parsed.isValid() ? parsed.format("HH:mm") : String(value);
	};

	frappe.provide("visitormanagement.utils");
	visitormanagement.utils.format_time_without_seconds = format_without_seconds;

	const BaseControlTime = frappe.ui.form.ControlTime;
	frappe.ui.form.ControlTime = class VMSControlTime extends BaseControlTime {
		set_time_options() {
			super.set_time_options();
			if (is_vms_time_field(this.df)) {
				// No "ss" in the format → Frappe hides the seconds slider.
				this.datepicker_options.timeFormat = "hh:ii";
			}
		}
		format_for_input(value) {
			if (is_vms_time_field(this.df)) {
				return format_without_seconds(value);
			}
			return super.format_for_input(value);
		}
	};

	const base_time_formatter = frappe.form.formatters.Time;
	frappe.form.formatters.Time = function (value, df, ...args) {
		if (is_vms_time_field(df)) {
			return format_without_seconds(value);
		}
		return base_time_formatter.call(this, value, df, ...args);
	};
})();
