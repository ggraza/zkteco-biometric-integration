from datetime import datetime, timedelta

import frappe
from frappe.model.document import Document
from frappe.utils import get_datetime

from zkteco_biometric_integration.zkteco_biometric_integration.doctype.zkteco_biometric_settings.zkteco_biometric_settings import (
	ZKTecoBiometricSettings,
)
from zkteco_biometric_integration.zkteco_biometric_integration.utils import (
	does_checkin_exist,
	does_employee_exist,
	map_checkin,
)

from ..api.zkteco_api import get_transactions

TXNs_PAGE_SIZE = 30

SYNC_SAFETY_BUFFER_MINUTES = 60


def process_transactions(settings_name: str | None = None) -> None:
	is_full_run = settings_name is None

	biometric_settings = (
		[settings_name]
		if settings_name
		else frappe.get_all("ZKTeco Biometric Settings", filters={"is_fetch_enabled": 1}, pluck="name")
	)

	run_started_at = get_datetime()
	all_sources_succeeded = bool(biometric_settings)
	all_sources_reported = bool(biometric_settings)

	for setting in biometric_settings:
		settings_doc: ZKTecoBiometricSettings = frappe.get_doc("ZKTeco Biometric Settings", setting)
		received = 0

		settings, params, end_time = build_transaction_data(settings_doc)
		try:
			for txn in get_transactions(settings, params, end_time):
				received += 1
				if emp_checkin := create_employee_checkin(txn):
					(manage_user(emp_checkin) if settings_doc.enable_mandatory_checkin else None)

			frappe.db.commit()
		except Exception:
			all_sources_succeeded = False
			frappe.db.rollback()
			frappe.log_error(
				title="Employee Checkin Sync Error",
				message=frappe.get_traceback(),
				reference_doctype="ZKTeco Biometric Settings",
				reference_name=settings_doc.name,
			)

		if not received:
			all_sources_reported = False

	if is_full_run and all_sources_succeeded and all_sources_reported:
		advance_attendance_watermark(run_started_at)


def advance_attendance_watermark(fetched_upto: datetime) -> None:
	"""Tell HRMS how far it may safely process auto attendance.
	Only shifts that have opted out of `auto_update_last_sync` are managed here,
	so this never fights HRMS' own clock-based updater.
	"""
	watermark = fetched_upto - timedelta(minutes=SYNC_SAFETY_BUFFER_MINUTES)

	shifts = frappe.get_all(
		"Shift Type",
		filters={"enable_auto_attendance": 1, "auto_update_last_sync": 0},
		fields=["name", "last_sync_of_checkin"],
	)

	for shift in shifts:
		if shift.last_sync_of_checkin and get_datetime(shift.last_sync_of_checkin) >= watermark:
			continue

		frappe.db.set_value("Shift Type", shift.name, "last_sync_of_checkin", watermark)

	frappe.db.commit()


def build_transaction_data(settings: "ZKTecoBiometricSettings") -> tuple:
	start_time = settings.last_fetched_time if settings.last_fetched_time else get_datetime()
	end_time = get_datetime()
	params = {
		"start_time": (start_time.strftime("%Y-%m-%d %H:%M:%S")),
		"end_time": (end_time.strftime("%Y-%m-%d %H:%M:%S")),
		"page_size": TXNs_PAGE_SIZE,
	}

	return (
		settings,
		params,
		end_time,
	)


def create_employee_checkin(transaction: dict) -> Document | None:
	validation_rules = [
		lambda: does_checkin_exist(transaction),
		lambda: not does_employee_exist(transaction.get("emp_code")),
	]

	if any(rule() for rule in validation_rules):
		return

	current_user = frappe.session.user

	try:
		frappe.set_user("ZKTeco Biometric")

		log_type = map_checkin(transaction.get("punch_state_display"))

		if not log_type:
			return None

		employee_checkin = frappe.get_doc(
			{
				"doctype": "Employee Checkin",
				"employee": transaction.get("emp_code"),
				"time": transaction.get("punch_time"),
				"log_type": log_type,
			}
		)
		employee_checkin.insert(ignore_permissions=True)

		return employee_checkin

	except Exception:
		frappe.log_error(title="Employee Checkin Creation Error", message=frappe.get_traceback())
	finally:
		frappe.set_user(current_user)  # nosemgrep


def activate_user(user_id: str, log_type: str) -> None:
	try:
		if "System Manager" in frappe.get_roles(user_id):
			return

		should_enable = log_type == "IN"

		frappe.db.set_value("User", user_id, "enabled", int(should_enable), update_modified=False)

	except Exception:
		frappe.log_error(message=frappe.get_traceback(), title="User Activation Error")


def manage_user(employee_checkin: Document):
	if frappe.db.exists("Employee", employee_checkin.employee):
		employee = frappe.get_doc("Employee", employee_checkin.employee)

		if employee.user_id:
			activate_user(employee.user_id, employee_checkin.log_type)
