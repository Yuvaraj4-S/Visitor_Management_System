"""Upload policy for the visitor portal, and a guard on guest file uploads.

On Frappe 15 this app does NOT need, and never turns on, the System Setting
`allow_guests_to_upload_files`. The pre-registration form reads the ID scan,
photo and visa copy in the browser and sends them inside the
`portal.submit_pre_registration` payload as data URIs; the server checks them
against the constants below and stores them as private Files attached to the
new Visitor Pass. No anonymous `upload_file` call is involved.

That setting is still site-wide, though, and another app (or an administrator)
may switch it on. Frappe's `upload_file` then treats it as blanket authority for
a guest: it skips `check_write_permission` and takes the target record straight
from the request (frappe/handler.py). `guard_guest_upload` keeps such anonymous
uploads off this app's records. Guest uploads to anything else, and every
logged-in upload, are left exactly as Frappe handles them.
"""

import io
import ipaddress
import os
import re

import frappe
from frappe import _

# What the portal actually asks a visitor for: a photo of an ID document and a
# photo of themselves. PDF is allowed because plenty of digital IDs are issued
# that way. This is the single definition of the app's upload policy — portal.py
# validates submitted attachments against these three constants, and the web
# form script mirrors them for its client-side checks.
ALLOWED_EXTENSIONS = (".jpg", ".jpeg", ".png", ".pdf")

# Matches the "max 5 MB" the portal prints under the upload fields. Kept as a
# constant rather than a setting: it is a statement about what the form asks
# for, not a policy knob, and the field descriptions would drift out of sync.
MAX_BYTES = 5 * 1024 * 1024

# Trust neither the extension nor the client-sent MIME type: the bytes must
# actually begin like the format they claim to be.
SIGNATURES = (
	b"\xff\xd8\xff",  # JPEG
	b"\x89PNG\r\n\x1a\n",  # PNG
	b"%PDF-",  # PDF
)

# The signature decides what a file IS; the name it is stored under follows.
_KIND_BY_SIGNATURE = (
	(b"\xff\xd8\xff", "JPEG"),
	(b"\x89PNG\r\n\x1a\n", "PNG"),
	(b"%PDF-", "PDF"),
)
_EXTENSIONS_BY_KIND = {
	"JPEG": (".jpg", ".jpeg"),
	"PNG": (".png",),
	"PDF": (".pdf",),
}
# What Pillow calls them. MPO is the multi-picture JPEG many phone cameras
# write under a .jpg name; it starts with the JPEG signature and saves as one.
_PILLOW_FORMATS = {"JPEG": ("JPEG", "MPO"), "PNG": ("PNG",)}

# Width x height an uploaded picture may have. A 5 MB file can declare far more
# pixels than it has bytes (a "decompression bomb"); Frappe re-encodes JPEGs to
# strip EXIF data, which decodes every one of them into memory. 50 megapixels
# covers a current phone camera and a 600 dpi A5 scan.
MAX_IMAGE_PIXELS = 50_000_000

# The smallest file that can be a real PDF (header, one object, trailer).
_MIN_PDF_BYTES = 64
_PDF_HEADER = re.compile(rb"%PDF-[12]\.\d")

_RATE_WINDOW_SECONDS = 60 * 60

# Source addresses only infrastructure can have: the private ranges (RFC 1918,
# IPv6 unique local, RFC 4193) and link-local ones. A reverse proxy or ingress in
# another container reaches gunicorn from one of these; a client on the public
# internet cannot. See `_peer_is_proxy`.
#
# Listed here rather than asked of `ipaddress.is_private`, which answers a
# different question ("not globally reachable"): it is also true for the
# documentation and benchmarking ranges, 0.0.0.0/8, 240.0.0.0/4 and, for IPv6,
# 2001::/23 — which contains Teredo, real clients — and what it covers has
# changed between Python patch releases. Who is believed about X-Forwarded-For
# must not depend on the interpreter.
_INFRASTRUCTURE_NETWORKS = tuple(
	ipaddress.ip_network(network)
	for network in (
		"10.0.0.0/8",
		"172.16.0.0/12",
		"192.168.0.0/16",
		"169.254.0.0/16",
		"fc00::/7",
		"fe80::/10",
	)
)

# Modules whose DocTypes are this app's records.
APP_MODULES = ("Visitor Management", "Conference Room")


def guard_guest_upload(doc, method=None):
	"""Refuse anonymous uploads that target one of this app's records.

	Only a Guest request is inspected. Server-side code creating a File while the
	session happens to be Guest (a scheduled job, a test, the portal submission
	below) has no request, or flags itself, and is not an anonymous caller.
	"""
	if frappe.session.user != "Guest" or not frappe.request:
		return

	if doc.flags.get("vms_portal_submission"):
		# portal._store_file: the visitor's own document, already checked against
		# the policy above and attached to their pass by the same request.
		return

	target_doctype = (doc.attached_to_doctype or "").strip()
	if not target_doctype:
		# Unattached: another page's guest upload, not this app's business.
		return

	if frappe.db.get_value("DocType", target_doctype, "module") in APP_MODULES:
		frappe.throw(
			_("Files cannot be uploaded to visitor records without signing in."),
			frappe.PermissionError,
		)


# -- content checks -----------------------------------------------------------


def _invalid_file():
	frappe.throw(
		_("The uploaded file is not a valid JPG, PNG or PDF. Please re-upload a genuine image or PDF.")
	)


def detect_kind(content):
	"""What the first bytes announce: "JPEG", "PNG" or "PDF". None for anything else."""
	for signature, kind in _KIND_BY_SIGNATURE:
		if content.startswith(signature):
			return kind
	return None


def verify_upload(filename, content):
	"""Check a guest's attachment and return the file name to store it under.

	Every refusal is a plain validation message (HTTP 417): nothing a visitor
	sends here may surface as a server error.

	The first bytes alone are not enough. A file that merely STARTS like a JPEG
	passed the old signature check and then crashed inside Frappe's own image
	handling (`File.save_file` -> `strip_exif_data` -> `PIL.Image.open`) with an
	unhandled UnidentifiedImageError — an HTTP 500 on a public endpoint, and the
	Werkzeug debugger page on a developer-mode site. So the content has to
	really be what it claims:

	- JPEG / PNG must parse and decode with Pillow, within MAX_IMAGE_PIXELS;
	- a PDF must have a proper header and an end-of-file marker.

	The stored name gets the extension of what the file IS. Frappe picks its
	image handling from the file NAME (mimetypes), so a PDF called `scan.jpg`
	crashed the same way as the polyglot; and a PNG a phone saved as `.jpg` is
	a visitor's honest mistake, not a reason to send them away.
	"""
	stem, ext = os.path.splitext(filename or "")
	if ext.lower() not in ALLOWED_EXTENSIONS:
		frappe.throw(_("Only JPG, PNG or PDF files are allowed for ID proof and photo."))
	if len(content) > MAX_BYTES:
		frappe.throw(_("File is too large. The maximum allowed size is 5 MB."))

	kind = detect_kind(content)
	if not kind:
		_invalid_file()

	if kind == "PDF":
		_verify_pdf(content)
	else:
		_verify_image(content, kind)

	if ext.lower() not in _EXTENSIONS_BY_KIND[kind]:
		ext = _EXTENSIONS_BY_KIND[kind][0]
	return f"{stem or 'upload'}{ext}"


def _verify_image(content, kind):
	"""Make Pillow read the picture the way Frappe is about to.

	`Image.open` must recognise the format the signature announced: that is what
	turns away the polyglot, a file with a picture's first bytes and something
	else behind them. `verify()` then checks the container — for a PNG every
	chunk and its CRC, so a damaged or cut-off PNG is refused — without decoding
	pixels; the object cannot be used afterwards, so the file is opened again.

	A JPEG has no such structure to check. It is decoded at the smallest scale
	libjpeg offers (`draft`), which reads the stream at a fraction of the
	memory. A photo that was merely cut short in transfer still gets through:
	Frappe switches Pillow to tolerate those for the whole process
	(`ImageFile.LOAD_TRUNCATED_IMAGES` in core's file.py) and its own handling
	copes with them, which is the point of this check.
	"""
	from PIL import Image

	too_many_pixels = False
	try:
		with Image.open(io.BytesIO(content)) as image:
			if image.format not in _PILLOW_FORMATS[kind]:
				raise ValueError("signature and content disagree")
			width, height = image.size
			image.verify()

		if width < 1 or height < 1:
			raise ValueError("empty image")
		too_many_pixels = width * height > MAX_IMAGE_PIXELS

		if kind == "JPEG" and not too_many_pixels:
			with Image.open(io.BytesIO(content)) as image:
				image.draft("RGB", (160, 160))
				image.load()
	except Exception:
		# UnidentifiedImageError, OSError (truncated), SyntaxError (broken PNG),
		# DecompressionBombError, struct.error ... Pillow raises many types for
		# "this is not a picture". None of them is the visitor's to read.
		_invalid_file()

	if too_many_pixels:
		frappe.throw(
			_("This picture is too large in width and height. Please upload a smaller photo or scan.")
		)


def _verify_pdf(content):
	if (
		len(content) < _MIN_PDF_BYTES
		or not _PDF_HEADER.match(content[:16])
		or b"%%EOF" not in content[-2048:]
	):
		_invalid_file()


# -- rate limiting ------------------------------------------------------------


def rate_limit_identity():
	"""The part of the request that identifies the caller for rate limiting.

	Frappe's own limiter keys on `frappe.local.request_ip`: the first
	`X-Forwarded-For` entry when the header is present, else the socket peer
	(frappe/auth.py set_request_ip). Nothing in a production stack validates that
	header for Frappe — gunicorn's REMOTE_ADDR is always the socket peer, its
	`forwarded_allow_ips` only governs the scheme headers, and werkzeug's
	ProxyFix is applied by `bench serve --proxy` alone (frappe/app.py). So
	`request_ip` is right exactly when a reverse proxy in front of gunicorn sets
	the header, and is whatever the client typed when there is none.

	This function returns the same identity Frappe uses whenever the request
	came through a proxy, and the socket peer otherwise:

	  - the peer is loopback, or not an IP address at all (a UNIX socket): the
	    bench nginx on the same host, which REPLACES the header
	    (`proxy_set_header X-Forwarded-For $remote_addr;`);
	  - the peer has a private / link-local address: nginx or an ingress in
	    another container (Docker, Kubernetes, Frappe Cloud). A client on the
	    public internet cannot have such a source address, so a private peer is
	    infrastructure. This used to key on the peer here, which gave every
	    visitor of the site one shared bucket: twenty submissions an hour for
	    the whole company, and one machine could use them all up;
	  - the operator told gunicorn which peers are proxies
	    (FORWARDED_ALLOW_IPS / --forwarded-allow-ips, `*` or a list);
	  - the site declared its proxies, as addresses or networks:

	        # site_config.json
	        "trusted_proxy_ips": ["10.0.0.4", "172.18.0.0/16"]

	    When this key is present it is authoritative: only loopback and the
	    listed peers are believed. That is the setting for a site whose gunicorn
	    is reachable directly from an office network, where "private address"
	    does not mean "proxy" and the header could be forged by a colleague.

	A public peer is never believed: on a directly exposed server the header is
	client-controlled, and rotating it would otherwise reset the limit.
	"""
	request_ip = getattr(frappe.local, "request_ip", None)
	peer_ip = getattr(frappe.request, "remote_addr", None) if frappe.request else None
	if not frappe.request:
		# No request (CLI, scheduler, tests): nothing to abuse in that context.
		return request_ip or "unknown"

	if _peer_is_proxy(peer_ip):
		return request_ip or peer_ip or "unknown"
	return peer_ip


# Kept under its old name for callers written against it.
_rate_limit_identity = rate_limit_identity


def _peer_is_proxy(peer_ip):
	address = _as_address(peer_ip)
	if address is None or address.is_loopback:
		# Not an IP at all is a local socket.
		return True

	declared = frappe.conf.get("trusted_proxy_ips")
	if declared is not None:
		return _in_networks(address, declared)

	allowed = (os.environ.get("FORWARDED_ALLOW_IPS") or "").strip()
	if allowed:
		entries = [entry.strip() for entry in allowed.split(",") if entry.strip()]
		if "*" in entries or _in_networks(address, entries):
			return True

	return any(
		address.version == network.version and address in network for network in _INFRASTRUCTURE_NETWORKS
	)


def _as_address(value):
	try:
		address = ipaddress.ip_address((value or "").strip())
	except ValueError:
		return None
	# ::ffff:10.0.0.4 is 10.0.0.4.
	return getattr(address, "ipv4_mapped", None) or address


def _in_networks(address, entries):
	if isinstance(entries, str):
		entries = [entries]
	for entry in entries or []:
		try:
			if address in ipaddress.ip_network(str(entry).strip(), strict=False):
				return True
		except ValueError:
			continue
	return False


def _is_loopback(ip):
	address = _as_address(ip)
	return bool(address and address.is_loopback)


def _counter_key(key):
	# `incrby` / `expire` / `get` are raw redis calls: unlike `set_value` /
	# `get_value` they do not add the site prefix, so the key goes through
	# `make_key` (as Frappe's own `rate_limiter.rate_limit` does). Without it
	# every site on the bench shared one counter per IP.
	return frappe.cache().make_key(key)


def _count_or_throw(key, ceiling, message=None, exc=None):
	"""Increment a rolling hourly counter and refuse once it passes `ceiling`.

	The message and the error class belong to the caller (the portal's
	submission limiter, the meal preview's anonymous budget). Both classes used
	answer HTTP 429.
	"""
	if count_event(key) > ceiling:
		frappe.throw(
			message or _("Too many requests from this connection. Please wait a while and try again."),
			exc or frappe.TooManyRequestsError,
		)


def count_event(key):
	"""Add one to an hourly counter and return the new total."""
	cache = frappe.cache()
	key = _counter_key(key)
	count = cache.incrby(key, 1)
	if count == 1:
		# Start the clock on the first increment only, so a burst cannot keep
		# pushing the expiry out and hold the window open indefinitely.
		cache.expire(key, _RATE_WINDOW_SECONDS)
	return count


def events_counted(key):
	"""The current total of an hourly counter, without adding to it."""
	try:
		return int(frappe.cache().get(_counter_key(key)) or 0)
	except (TypeError, ValueError):
		return 0
