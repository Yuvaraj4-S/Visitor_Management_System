"""Drop per-service status columns that were removed from Hospitality Request
and Visitor Pass. The main `status` field now drives all flow decisions.
"""
import frappe


HOSPITALITY_COLS = [
	"cab_status",
	"hotel_status",
	"tour_status",
	"buggy_status",
	"greeting_status",
	"greeting_vendor",
	"greeting_photo",
	"greeting_occasion",
	"flight_train_no",
]
VP_COLS = [
	"cab_status_display",
	"hotel_status_display",
	"tour_status_display",
	"buggy_status_display",
	"greeting_status_display",
]


def _drop_columns(table, columns):
	# `table`/`col` are not user input: they come only from the hardcoded
	# constant lists and fixed "tab<DocType>" names in this module. SQL
	# identifiers cannot be bound as parameters, so f-strings are required.
	existing = [c.get("Field") or c.get("name") for c in frappe.db.sql(f"SHOW COLUMNS FROM `{table}`", as_dict=True)]  # noqa: S608  # nosemgrep: frappe-sql-format-injection
	for col in columns:
		if col not in existing:
			continue
		try:
			frappe.db.sql_ddl(f"ALTER TABLE `{table}` DROP COLUMN `{col}`")  # noqa: S608  # nosemgrep: frappe-sql-format-injection
			print(f"Dropped {table}.{col}")
		except Exception:
			frappe.log_error(
				title=f"Drop column failed: {table}.{col}",
				message=frappe.get_traceback(),
			)


def execute():
	_drop_columns("tabHospitality Request", HOSPITALITY_COLS)
	_drop_columns("tabVisitor Pass", VP_COLS)
