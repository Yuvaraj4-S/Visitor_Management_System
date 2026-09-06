"""Undo the one site-wide side effect this app makes outside its own tables.

Everything this app owns — its DocTypes, roles, workflows, Custom DocPerm rows
— goes away with `bench uninstall-app` on its own. The one thing that does not
is `setup._allow_portal_uploads()` switching the site-wide System Setting
`allow_guests_to_upload_files` ON at install time: the visitor pre-registration
portal cannot accept an ID scan or photo without it, and Frappe ships that
setting off by default with no per-web-form equivalent. A customer who trials
this app and removes it would otherwise keep that changed security setting
forever, with nothing in the UI explaining why anonymous uploads are still
possible site-wide.
"""

import frappe

from visitormanagement.setup import _PORTAL_UPLOADS_SELF_ENABLED_MARKER


def before_uninstall():
	_revert_portal_uploads_setting()


def _revert_portal_uploads_setting():
	"""Turn `allow_guests_to_upload_files` back off — but only when this app is
	the one that turned it on, and only when it is still on now.

	`setup._PORTAL_UPLOADS_SELF_ENABLED_MARKER` is set in exactly the branch of
	`_allow_portal_uploads()` that performs the flip (the setting was off, and
	this app switched it on). It is deliberately a different fact from the
	older `_PORTAL_UPLOADS_MARKER`, which only records "setup has considered
	this setting" and is set whether setup found the flag already on or turned
	it on itself — that one cannot tell "we enabled it" apart from "another app,
	or an administrator, already had it on for their own reason", so it is not
	safe to act on here.

	A site running a version of this app from before the self-enabled marker
	existed has no such marker to find. That is read the same way as "we did
	not do this" — nothing is touched, and the administrator is told why, per
	the same "if in doubt, leave it alone" rule as every other one-time marker
	in setup.py. The alternative — guessing, or reverting unconditionally on
	every uninstall — risks silently breaking guest uploads for some other app
	on the same site, which is a worse outcome than this app's own portal
	leaving the flag on for a former customer to review by hand.
	"""
	if not frappe.db.get_default(_PORTAL_UPLOADS_SELF_ENABLED_MARKER):
		print(
			"  Visitor Management: allow_guests_to_upload_files was left as-is. This "
			"install has no record of having switched it on itself (it may already "
			"have been on before this app was installed, for another app or by an "
			"administrator's own choice), so it is not safe to assume this app owns "
			"turning it back off. Review System Settings -> Allow Guests to Upload "
			"Files by hand if it should be disabled now."
		)
		return

	currently_enabled = frappe.db.get_single_value("System Settings", "allow_guests_to_upload_files")
	if not currently_enabled:
		# Already off — an administrator's own change since install, most likely.
		# Nothing to revert, and nothing to warn about.
		return

	frappe.db.set_single_value("System Settings", "allow_guests_to_upload_files", 0)
	print(
		"  Visitor Management: turned allow_guests_to_upload_files back off "
		"(this install had switched it on for the visitor portal)."
	)
