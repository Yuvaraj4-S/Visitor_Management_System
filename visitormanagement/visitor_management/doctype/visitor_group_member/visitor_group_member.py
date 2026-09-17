# For license information, please see license.txt

from frappe.model.document import Document


class VisitorGroupMember(Document):
	"""One accompanying visitor on a group pass.

	Deliberately thin. A delegation of twelve arriving for one meeting is one
	visit with one host, one purpose and one approval — not twelve passes each
	needing their own photo, ID scan and approval lane. The lead visitor stays
	on the Visitor Pass itself; everyone with them is a row here.

	Screening still applies to every row: `VisitorPass._validate_group_members`
	runs the blacklist against each member, so a group cannot be used to walk a
	barred person past the check the lead visitor goes through.
	"""

	pass
