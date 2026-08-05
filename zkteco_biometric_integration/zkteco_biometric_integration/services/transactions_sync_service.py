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


def process_transactions() -> None:
	biometric_settings = frappe.get_all(
		"ZKTeco Biometric Settings", filters={"is_fetch_enabled": 1}, pluck="name"
	)

	for setting in biometric_settings:
		settings_doc: ZKTecoBiometricSettings = frappe.get_doc("ZKTeco Biometric Settings", setting)

		settings, params, end_time = build_transaction_data(settings_doc)
		try:
			for txn in get_transactions(settings, params, end_time):
				if emp_checkin := create_employee_checkin(txn):
					(manage_user(emp_checkin) if settings_doc.enable_mandatory_checkin else None)

			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				title="Employee Checkin Sync Error",
				message=frappe.get_traceback(),
				reference_doctype="ZKTeco Biometric Settings",
				reference_name=settings_doc.name,
			)


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
