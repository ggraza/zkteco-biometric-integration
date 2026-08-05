from datetime import date, datetime, time

import frappe
from frappe import _


def map_checkin(punch_state: str) -> str:
	punch_state_map = {
		"Check In": "IN",
		"Check Out": "OUT",
	}

	checkin_state = punch_state_map.get(punch_state)
	if not checkin_state:
		frappe.log_error(f"Unknown punch state: {punch_state}")
		return
	return checkin_state


def get_employees() -> list[dict]:
	return frappe.get_all(
		"Employee",
		filters={"status": "Active"},
	)


def does_checkin_exist(transaction: dict) -> bool:
	return frappe.db.exists(
		"Employee Checkin",
		{
			"employee": transaction.get("emp_code"),
			"time": transaction.get("punch_time"),
			"log_type": map_checkin(transaction.get("punch_state_display")),
		},
	)


def does_employee_exist(emp_code: str) -> bool:
	return frappe.db.exists(
		"Employee",
		{
			"name": emp_code,
			"status": "Active",
		},
	)


def log_throw_error(title: str) -> None:
	frappe.log_error(title=title, message=frappe.get_traceback())
	frappe.throw(_(title))
