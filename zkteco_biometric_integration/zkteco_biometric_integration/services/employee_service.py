import frappe
from frappe import _

from zkteco_biometric_integration.zkteco_biometric_integration.doctype.zkteco_biometric_settings.zkteco_biometric_settings import (
	ZKTecoBiometricSettings,
)

from ..api.zkteco_api import create_employee
from ..utils import does_employee_exist, log_throw_error

EMPLOYEE_FILTER_FIELDS = ("company", "department", "designation", "branch", "status")
DEFAULT_STATUS = "Active"
SEARCH_PAGE_LENGTH = 50


def post_employee(settings: "ZKTecoBiometricSettings", employee_id: str) -> dict:
	employee_payload = build_request_payload(employee_id)
	return create_employee(settings, employee_payload)


def build_request_payload(employee_id: str) -> dict:
	if not does_employee_exist(employee_id):
		log_throw_error(f"Employee with ID/Name {employee_id} does not exist")

	emp = frappe.db.get_value(
		"Employee",
		employee_id,
		["name", "first_name", "last_name", "company", "department", "branch"],
		as_dict=True,
	)

	if emp.name != employee_id:
		frappe.throw(_("Mismatching Employee IDs"))

	return {
		"emp_code": emp.name,
		"first_name": emp.first_name,
		"last_name": emp.last_name or emp.first_name,
		"area": [1],
		"department": 1,
	}


def build_employee_filters(filters: dict | str | None = None) -> dict:
	"""Translate the dialog's filter values into Employee query filters."""
	filters = frappe.parse_json(filters) or {}

	query_filters = {field: filters[field] for field in EMPLOYEE_FILTER_FIELDS if filters.get(field)}
	query_filters.setdefault("status", DEFAULT_STATUS)

	if role := filters.get("role"):
		users = frappe.get_all(
			"Has Role",
			filters={"role": role, "parenttype": "User"},
			pluck="parent",
		)
		query_filters["user_id"] = ["in", users or [""]]

	return query_filters


def resolve_employees(employees: list | str | None = None, filters: dict | str | None = None) -> list[str]:
	"""An explicit selection wins; otherwise everyone matching the filters."""
	employees = frappe.parse_json(employees) if isinstance(employees, str) else employees

	if employees:
		return employees if isinstance(employees, list) else [employees]

	return frappe.get_all("Employee", filters=build_employee_filters(filters), pluck="name")


@frappe.whitelist()
def search_employees(filters: dict | str | None = None, txt: str = "") -> list[dict]:
	frappe.only_for(["System Manager", "HR Manager"])

	or_filters = [["name", "like", f"%{txt}%"], ["employee_name", "like", f"%{txt}%"]] if txt else None

	rows = frappe.get_all(
		"Employee",
		filters=build_employee_filters(filters),
		or_filters=or_filters,
		fields=["name", "employee_name", "designation"],
		limit_page_length=SEARCH_PAGE_LENGTH,
		order_by="name",
	)

	return [
		{
			"value": row.name,
			"description": " / ".join(part for part in (row.employee_name, row.designation) if part),
		}
		for row in rows
	]


def sync_employees(settings_name: str, employees: list[str]) -> dict:
	settings: ZKTecoBiometricSettings = frappe.get_doc("ZKTeco Biometric Settings", settings_name)

	total = len(employees)
	synced: list[str] = []
	failed: list[dict] = []

	for index, employee_id in enumerate(employees, start=1):
		try:
			post_employee(settings, employee_id)
			synced.append(employee_id)

		except Exception as e:
			failed.append({"employee": employee_id, "error": str(e)})
			frappe.log_error(
				title=f"ZKTeco Employee Sync Failed: {employee_id}",
				message=frappe.get_traceback(),
			)

		frappe.publish_progress(
			index * 100 / total,
			title=_("Syncing Employees"),
			description=employee_id,
		)

	return {"total": total, "synced": synced, "failed": failed}
