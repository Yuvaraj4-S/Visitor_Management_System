# For license information, please see license.txt

from frappe.model.document import Document


class SecurityLogMember(Document):
	"""One accompanying visitor on a group pass, as the gate saw them on one log.

	A group pass used to move through the gate as one person: the lead visitor's
	log checked the whole group in and out, and nobody could tell the evacuation
	marshal whether the twelve names on the pass were all inside. Each row here
	says whether that member came through on this log (`present`); the Security
	Log writes the result onto the pass's own Visitor Group Member row.
	"""

	pass
