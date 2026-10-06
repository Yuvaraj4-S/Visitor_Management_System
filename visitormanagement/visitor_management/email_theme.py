"""Make notification HTML legible on any ground, light or dark.

The alert templates are written for email, where the ground is always light, so
they declare text colours and no background. The desk renders that same HTML in
a document's Activity timeline on whatever the viewer's theme paints — in dark
mode that put #1f2933 body text on a #171717 background at a contrast of
1.22:1, which is the "text not clear" operators reported.

Two things are needed, and only doing one of them makes it worse:

* paint the wrapper, so the message is a light card in either theme; and
* pin a colour on every tag the desk stylesheet colours — headings, links and
  bold text. Left bare they inherit the theme's near-white foreground, which is
  invisible on the card we just painted (measured at 1.03:1).

Inline styles are the only mechanism that survives both an email client and the
timeline, so the colours are written onto the elements themselves.

This lives apart from setup.py because it is applied in two places: to the
shipped templates, and to alerts already sitting in timelines, which store the
HTML as rendered at send time and so are not fixed by editing the templates.
"""

import re

CARD_STYLE = "background-color: #ffffff; padding: 16px; border-radius: 6px;"

# Every tag the desk stylesheet gives a colour to, and what it should be on the
# painted card. Bold takes the body colour: it is emphasis, not a heading.
TAG_COLOURS = {
	"h1": "#102a43",
	"h2": "#102a43",
	"h3": "#102a43",
	"h4": "#102a43",
	"h5": "#102a43",
	"h6": "#102a43",
	"a": "#1e40af",
	"b": "#1f2933",
	"strong": "#1f2933",
}

# Accent colours that fall below 4.5:1 once the card is painted white, mapped to
# the nearest tone the app already uses that clears it.
LOW_CONTRAST_ACCENTS = {
	"#fd7e14": "#b45309",  # 2.57:1 -> 4.9:1
}

_WRAPPER_RE = re.compile(r'^\s*<div style="([^"]*)">')
_COLOURABLE_RE = re.compile(r"<(" + "|".join(TAG_COLOURS) + r")\b([^>]*)>", re.IGNORECASE)
_HAS_COLOUR_RE = re.compile(r"(^|;)\s*color\s*:", re.IGNORECASE)


def repaint_email_html(html):
	"""Return `html` with a painted ground and explicit colours. Idempotent."""
	if not html:
		return html

	wrapper = _WRAPPER_RE.match(html)
	if not wrapper:
		# Nothing to paint a ground on. Such a message inherits the viewer's own
		# theme colours and is legible as-is, so pinning dark colours here would
		# create exactly the problem this function exists to fix.
		return html

	out = html
	if "background" not in wrapper.group(1):
		style = wrapper.group(1).rstrip().rstrip(";") + "; " + CARD_STYLE
		out = f'<div style="{style}">' + out[wrapper.end() :]

	for weak, better in LOW_CONTRAST_ACCENTS.items():
		out = re.sub(re.escape(weak), better, out, flags=re.IGNORECASE)

	return _COLOURABLE_RE.sub(_colourise, out)


def _colourise(match):
	tag, attrs = match.group(1), match.group(2)
	colour = TAG_COLOURS[tag.lower()]

	existing = re.search(r'style\s*=\s*"([^"]*)"', attrs)
	if not existing:
		return f'<{tag} style="color: {colour};"{attrs}>'

	if _HAS_COLOUR_RE.search(existing.group(1)):
		return match.group(0)

	merged = existing.group(1).rstrip().rstrip(";") + f"; color: {colour};"
	return match.group(0).replace(existing.group(0), f'style="{merged}"')
