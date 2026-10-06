"""Upgrade steps: one-time and idempotent data changes, one module per area.

`setup.setup_visitor_management()` (install and every migrate) calls `run_all()`,
which runs `run()` of each module below that exists, in this order:

    pass_steps      Visitor Pass, group members, visitor items
    gate_steps      Security Log, gate API, reports
    portal_steps    portal, invitations, hospitality, rooms
    platform_steps  settings, permissions, blacklist, masters

Rules for a step module:

* expose `def run():`;
* be idempotent — it runs on every migrate;
* guard a one-time part with a `frappe.db.get_default` / `set_default` marker
  named `vms_...` (uninstall forgets every `vms_` marker);
* print one line per action taken, as the setup steps do.

A step that fails stops the migrate: the error is logged and raised again with
the step's name, so the operator sees which area failed.
"""

import importlib
import importlib.util

import frappe

STEP_MODULES = ("pass_steps", "gate_steps", "portal_steps", "platform_steps")

# Set by setup.before_install (1.1.0 onwards) before DocType sync. A site that
# was installed by 1.0.0 has no such default.
_INSTALLED_BY_THIS_VERSION_MARKER = "vms_preexisting:Role"


class UpgradeStepError(Exception):
	"""An upgrade step failed; the message names the step."""


def is_fresh_install() -> bool:
	"""True on a site first installed with 1.1.0 or later; False on one upgraded from 1.0.0.

	Two signs, either is enough: the code is running inside `bench install-app`
	(`frappe.flags.in_install`), or `setup.before_install` left its record —
	1.0.0 had no before_install hook, so a site it installed has none.
	"""
	if frappe.flags.in_install:
		return True
	return frappe.db.get_default(_INSTALLED_BY_THIS_VERSION_MARKER) is not None


def _run_step(step):
	module_name = f"{__name__}.{step}"
	if importlib.util.find_spec(module_name) is None:
		return False  # this area has no steps
	try:
		module = importlib.import_module(module_name)
		module.run()
	except Exception as exc:
		print(f"  FAILED upgrade step {step}: {exc}")
		try:
			frappe.log_error(
				title=f"VMS upgrade step failed: {step}",
				message=frappe.get_traceback(with_context=True),
			)
		except Exception:
			pass  # the database may be the thing that failed; the raise below still reports it
		raise UpgradeStepError(f"Visitor Management upgrade step '{step}' failed: {exc}") from exc
	return True


def run_all():
	"""Run every area's steps. Returns the names of the modules that ran."""
	return [step for step in STEP_MODULES if _run_step(step)]
