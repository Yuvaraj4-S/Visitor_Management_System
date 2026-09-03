"""Constraints on guest file uploads.

The visitor portal needs `allow_guests_to_upload_files` (see
`setup._allow_portal_uploads`), and that System Setting is all-or-nothing: once
on, any anonymous request may call `upload_file`. This module narrows it back
down so enabling the portal does not hand the site an open file drop.

Only Guest sessions are inspected. Anyone logged in — in this app or any other
— goes through Frappe's normal permission path untouched.
"""

import os

import frappe
from frappe import _

# What the portal actually asks a visitor for: a photo of an ID document and a
# photo of themselves. PDF is allowed because plenty of digital IDs are issued
# that way. This is the single definition of the app's upload policy — portal.py
# validates submitted attachments against the same three constants, so the
# gate-keeping at upload time and at submit time cannot drift apart.
ALLOWED_EXTENSIONS = (".jpg", ".jpeg", ".png", ".pdf")

# Matches the "max 5 MB" the portal prints under both upload fields. Kept as a
# constant rather than a setting: it is a statement about what the form asks
# for, not a policy knob, and the field descriptions would drift out of sync.
MAX_BYTES = 5 * 1024 * 1024

# Trust neither the extension nor the client-sent MIME type: the bytes must
# actually begin like the format they claim to be.
SIGNATURES = (
	b"\xff\xd8\xff",        # JPEG
	b"\x89PNG\r\n\x1a\n",   # PNG
	b"%PDF-",               # PDF
)

# A visitor uploads two files, and may redo them a few times. Frappe's
# `upload_file` has no rate limit of its own, so without this an anonymous
# caller can write files until the disk fills — the size cap only bounds one
# request. Counted per IP over a rolling hour, matching the 20/hour already
# applied to portal.submit_pre_registration.
MAX_GUEST_UPLOADS_PER_HOUR = 30
_RATE_WINDOW_SECONDS = 60 * 60


def guard_guest_upload(doc, method=None):
	"""Hold anonymous uploads to what the visitor portal advertises."""
	if frappe.session.user != "Guest":
		return

	# No HTTP request means no upload: this is server-side code creating a File
	# while the session happens to be Guest — a scheduled job, a test, another
	# app's own logic. The whole point of this guard is to constrain what an
	# anonymous *caller* can POST to `upload_file`, and there is no such caller
	# here. Failing closed on this path made the app reject every Guest-context
	# File insert on the whole site, which broke Frappe's own core File tests
	# (test_list_private_guest_single_file, test_list_private_guest_attachment)
	# and would break any other app that creates a File for a website visitor.
	if not frappe.request:
		return

	# A guest who is not filling in the portal has no business uploading at all.
	# Files arrive before the Visitor Pass exists, so they are unattached at this
	# point — the portal is identified by the form the request came from.
	_reject_unless_portal_request()
	_enforce_rate_limit()
	_reject_foreign_attachment_target(doc)

	extension = os.path.splitext(doc.file_name or "")[1].lower()
	if extension not in ALLOWED_EXTENSIONS:
		frappe.throw(
			_("Upload a {0} file.").format(", ".join(e.lstrip(".").upper() for e in ALLOWED_EXTENSIONS)),
			title=_("Unsupported File Type"),
		)

	size = _content_length(doc)
	if size and size > MAX_BYTES:
		frappe.throw(
			_("This file is {0} MB. Please upload a file under {1} MB.").format(
				round(size / 1024 / 1024, 1), MAX_BYTES // 1024 // 1024
			),
			title=_("File Too Large"),
		)

	# An extension check alone lets anything through under a .png name, which on
	# a site with guest uploads enabled means an anonymous caller can write
	# arbitrary bytes into the file store. Require the content to match.
	# Fail closed. This used to read `if head and not any(...)`, so a request that
	# carried no readable bytes — a remote `file_url` upload, for instance —
	# produced an empty head and skipped the content check altogether. A security
	# check that cannot see what it is inspecting must refuse, not wave it
	# through.
	head = _content_head(doc)
	if not head or not any(head.startswith(signature) for signature in SIGNATURES):
		frappe.throw(
			_("That file is not a genuine JPG, PNG or PDF. Please upload a real photo or scan."),
			title=_("Unsupported File Type"),
		)

	# Visitor IDs and photographs are personal documents; they must not be
	# readable by URL guessing, whatever the caller asked for.
	doc.is_private = 1


# The only fields on the only doctype this portal ever asks a visitor to attach
# something to. Everything else is somebody else's record.
PORTAL_ATTACH_DOCTYPE = "Visitor Pass"
PORTAL_ATTACH_FIELDS = ("id_proof_scan", "visitor_photo")


def _reject_foreign_attachment_target(doc):
	"""Refuse to let an anonymous upload land on an arbitrary record.

	`allow_guests_to_upload_files` is what makes the portal work, and Frappe's
	`upload_file` handler reads that setting as blanket authority: for a Guest it
	sets `ignore_permissions = True` and then *skips `check_write_permission`
	entirely* (frappe/handler.py) — not relaxes it, skips it. The target itself is
	pure form data:

	    doctype  = frappe.form_dict.doctype
	    docname  = frappe.form_dict.docname
	    fieldname = frappe.form_dict.fieldname

	So turning this setting on for the visitor portal handed the whole bench an
	unauthenticated write primitive: any caller could POST a genuine JPG with
	`doctype=Employee&docname=HR-EMP-00060` and have it attach, with no permission
	check anywhere in the chain. Confirmed against this site — the file landed on
	the Employee record. The checks above did not stop it: they inspect the file's
	*content*, never where it is going, and the Referer check says in its own
	docstring that it is not a boundary.

	The portal itself never needs this. Frappe's attach control only sends a
	doctype/docname when it has a `frm` (frappe/public/js/frappe/form/controls/
	attach.js) and a web form has none, so a visitor's ID scan and photo arrive
	unattached and are linked afterwards by `_attach_file_to_pass` — which writes
	through `db.set_value` and never reaches this hook. Whitelisting the target
	therefore costs the legitimate flow nothing.
	"""
	target_doctype = (doc.attached_to_doctype or "").strip()
	target_field = (doc.attached_to_field or "").strip()

	# Unattached is the normal case: the pass does not exist yet.
	if not target_doctype and not (doc.attached_to_name or "").strip():
		return

	if target_doctype == PORTAL_ATTACH_DOCTYPE and (not target_field or target_field in PORTAL_ATTACH_FIELDS):
		return

	frappe.throw(
		_("Files uploaded from the visitor form cannot be attached to that record."),
		frappe.PermissionError,
	)


def _rate_limit_identity():
	"""The part of the request that identifies the caller for rate limiting.

	`frappe.local.request_ip` is read straight from `X-Forwarded-For` with no
	trusted-proxy validation (frappe/auth.py), so on a directly-exposed site it
	is simply whatever the client typed — rotating it defeated this limit
	entirely. A forwarded-for header is only meaningful when something we trust
	put it there, so it is honoured only when the socket peer is a proxy the site
	has declared:

	    # site_config.json
	    "trusted_proxy_ips": ["10.0.0.4"]

	Otherwise the socket peer is used, which the caller cannot rewrite.
	"""
	peer_ip = getattr(frappe.request, "remote_addr", None) if frappe.request else None
	if not peer_ip:
		# No socket information (CLI, tests). Fall back to the claimed value —
		# there is no request to abuse in that context.
		return getattr(frappe.local, "request_ip", None) or "unknown"

	trusted = frappe.conf.get("trusted_proxy_ips") or []
	if peer_ip in trusted:
		return getattr(frappe.local, "request_ip", None) or peer_ip

	return peer_ip


def _enforce_rate_limit():
	"""Cap how many files one anonymous caller may upload per hour."""
	_count_or_throw(
		f"vms:guest-upload:{_rate_limit_identity()}",
		MAX_GUEST_UPLOADS_PER_HOUR,
		_("Too many uploads from this connection. Please wait a while and try again."),
	)


def _count_or_throw(key, ceiling, message=None):
	"""Increment a rolling hourly counter and refuse once it passes `ceiling`.

	The message belongs to the caller. This helper is shared with the portal's
	submission limiter, and a visitor who had just finished filling in the form
	was being told they had made "too many uploads" — which is not what they
	did, and sends them looking in the wrong place.
	"""
	cache = frappe.cache()
	count = cache.incrby(key, 1)
	if count == 1:
		# Start the clock on the first increment only, so a burst cannot keep
		# pushing the expiry out and hold the window open indefinitely.
		cache.expire(key, _RATE_WINDOW_SECONDS)

	if count > ceiling:
		frappe.throw(
			message or _("Too many requests from this connection. Please wait a while and try again."),
			frappe.TooManyRequestsError,
		)


def _reject_unless_portal_request():
	"""Only accept uploads that came from this site's own portal page.

	The previous check asked whether the string "visitor-pre-registration-form"
	appeared *anywhere* in the Referer, which any origin satisfies —
	`https://evil.example/visitor-pre-registration-form` passed, as did
	`https://evil.example/?x=visitor-pre-registration-form`. Both the host and the
	path have to be checked, against this site's own URL.

	This is a defence-in-depth layer, not the boundary: a non-browser client sets
	Referer freely. The controls that actually bound the damage are the content,
	size and rate checks around it.
	"""
	if not frappe.request:
		frappe.throw(
			_("File uploads are only accepted from the visitor pre-registration form."),
			frappe.PermissionError,
		)

	referrer = frappe.request.headers.get("Referer", "") or ""
	try:
		from urllib.parse import urlparse

		referrer_url = urlparse(referrer)
	except Exception:
		referrer_url = None

	# Compare against the host this request actually arrived on, not the site's
	# configured URL. A site is commonly reached by more than one name — an IP
	# and port on a gate terminal, a LAN hostname, the canonical domain — and
	# `get_url()` only ever knows the last of those. Matching the request's own
	# Host is what "same origin" means from the browser's point of view, and it
	# is the browser's view that decides what it puts in Referer.
	request_host = (frappe.request.host or "").lower()
	same_origin = bool(referrer_url and referrer_url.netloc and referrer_url.netloc.lower() == request_host)
	on_portal_path = bool(referrer_url and referrer_url.path.startswith("/visitor-pre-registration-form"))

	if same_origin and on_portal_path:
		return

	frappe.throw(
		_("File uploads are only accepted from the visitor pre-registration form."),
		frappe.PermissionError,
	)


def _content_head(doc):
	"""First few bytes of the incoming file, without consuming the stream."""
	content = getattr(doc, "content", None)
	if content:
		return content[:8] if isinstance(content, bytes) else content[:8].encode("latin-1", "ignore")

	files = getattr(frappe.request, "files", None) if frappe.request else None
	uploaded = files.get("file") if files else None
	if not uploaded:
		return b""

	stream = uploaded.stream
	position = stream.tell()
	stream.seek(0)
	head = stream.read(8)
	stream.seek(position)
	return head


def _content_length(doc):
	"""Size of what the caller actually sent.

	The request stream is checked first, on purpose. Frappe may re-encode an
	uploaded image before this hook runs, so `doc.content` can be far smaller
	than what arrived — measuring that would let a caller push any amount of
	data through a limit the portal advertises as 5 MB.
	"""
	files = getattr(frappe.request, "files", None) if frappe.request else None
	uploaded = files.get("file") if files else None
	if uploaded:
		stream = uploaded.stream
		position = stream.tell()
		stream.seek(0, os.SEEK_END)
		size = stream.tell()
		stream.seek(position)
		if size:
			return size

	content = getattr(doc, "content", None)
	return len(content) if content else 0
