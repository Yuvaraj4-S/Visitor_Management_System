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
	existing = [c.get("Field") or c.get("name") for c in frappe.db.sql(f"SHOW COLUMNS FROM `{table}`", as_dict=True)]
	for col in columns:
		if col not in existing:
			continue
		try:
			frappe.db.sql_ddl(f"ALTER TABLE `{table}` DROP COLUMN `{col}`")
			print(f"Dropped {table}.{col}")
		except Exception:
			frappe.log_error(
				title=f"Drop column failed: {table}.{col}",
				message=frappe.get_traceback(),
			)


def execute():
	_drop_columns("tabHospitality Request", HOSPITALITY_COLS)
	_drop_columns("tabVisitor Pass", VP_COLS)
	frappe.db.commit()
