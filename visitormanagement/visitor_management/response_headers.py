"""Security response headers for the guest-facing visitor portal.

Frappe emits a `frame-ancestors` policy for a Web Form only when that form
declares `allowed_embedding_domains`; with the field empty it sends nothing, so
the pre-registration page can be framed by any site on the internet. That page
asks an unauthenticated visitor to upload a photo of their government ID, which
makes it a worthwhile clickjacking target.

The invitation token also travels in the query string, so a referrer policy
matters here specifically: without one, the token would be attached to the
`Referer` of any outbound request the page makes.

Scoped to this app's own routes — other apps and the desk keep whatever the site
already sends.
"""

import frappe

PORTAL_PATHS = ("/visitor-pre-registration-form",)

HEADERS = {
	# Deny framing outright. Nothing in this flow is meant to be embedded.
	"X-Frame-Options": "DENY",
	"Content-Security-Policy": "frame-ancestors 'none'",
	# The page serves user-uploaded images back to staff; stop the browser
	# second-guessing the declared content type.
	"X-Content-Type-Options": "nosniff",
	# The invitation token is in the URL, so it must never leave this origin.
	#
	# `same-origin` and not `no-referrer`: the portal's own upload call is a
	# same-origin request, and `guard_guest_upload` identifies a legitimate
	# upload by its Referer. `no-referrer` suppresses that header on every
	# request including our own, so the guard rejected the visitor's ID upload
	# and the form could not be completed. `same-origin` still sends nothing
	# cross-origin — the token stays put — while keeping the guard working.
	"Referrer-Policy": "same-origin",
}


def set_portal_security_headers(response=None, request=None):
	request = request or getattr(frappe.local, "request", None)
	if response is None or request is None:
		return

	path = getattr(request, "path", "") or ""
	if not path.startswith(PORTAL_PATHS):
		return

	for header, value in HEADERS.items():
		# Never clobber a stricter policy already set by the site or a proxy.
		response.headers.setdefault(header, value)
