# For license information, please see license.txt

from frappe.model.document import Document

# The member's full ID number and the field a new one is typed into. Mirrors
# PRIVATE_ID_FIELDS in visitor_pass.py, which owns the rules for both.
PRIVATE_ID_FIELDS = ("id_proof_number", "id_proof_number_entry")


class VisitorGroupMember(Document):
	"""One accompanying visitor on a group pass.

	Deliberately thin. A delegation of twelve arriving for one meeting is one
	visit with one host, one purpose and one approval — not twelve passes each
	needing their own photo, ID scan and approval lane. The lead visitor stays
	on the Visitor Pass itself; everyone with them is a row here.

	Screening still applies to every row: `VisitorPass._validate_group_members`
	runs the blacklist against each member, so a group cannot be used to walk a
	barred person past the check the lead visitor goes through.

	The ID number follows the pass's own rule: stored in full in
	`id_proof_number` (permlevel 1), shown as `id_proof_number_masked`, typed
	into `id_proof_number_entry`. `VisitorPass.validate` maintains all three.
	"""

	def as_dict(self, *args, **kwargs):
		# A serialised row never carries the full number — see VisitorPass.as_dict.
		# This also keeps it out of the "row added / removed" entries Frappe writes
		# to the pass's change history (Version stores `row.as_dict()`).
		data = super().as_dict(*args, **kwargs)
		for fieldname in PRIVATE_ID_FIELDS:
			data.pop(fieldname, None)
		return data
