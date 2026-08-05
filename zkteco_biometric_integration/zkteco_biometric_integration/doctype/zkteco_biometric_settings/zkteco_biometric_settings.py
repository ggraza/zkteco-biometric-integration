# Copyright (c) 2025, Navari Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_datetime

SYNC_INLINE_LIMIT = 20
PER_EMPLOYEE_TIMEOUT_SECONDS = 30
SYNC_EMPLOYEES_METHOD = (
	"zkteco_biometric_integration.zkteco_biometric_integration.services.employee_service.sync_employees"
)


class ZKTecoBiometricSettings(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		enable_mandatory_checkin: DF.Check
		expiry: DF.Datetime | None
		is_fetch_enabled: DF.Check
		issued_at: DF.Datetime | None
		last_fetched_time: DF.Datetime | None
		password: DF.Password
		token: DF.Text | None
		url: DF.Data
		username: DF.Data
	# end: auto-generated types

	def after_insert(self):
		self.db_set("last_fetched_time", get_datetime())

	def validate(self):
		self.url = self.url.strip("/")

	@frappe.whitelist()
	def generate_token(self) -> str:
		from ...api.zkteco_api import get_token

		return get_token(self) if self.is_token_expired else self.token

	@property
	def is_token_expired(self) -> bool:
		if not self.token or not self.expiry:
			return True

		return get_datetime() >= get_datetime(self.expiry)

	@frappe.whitelist()
	def sync_employees(self, employees: str | list | None = None, filters: str | dict | None = None) -> dict:
		frappe.only_for(["System Manager", "HR Manager"])

		from ...services.employee_service import resolve_employees
		from ...services.employee_service import sync_employees as run_sync

		employee_ids = resolve_employees(employees, filters)

		if not employee_ids:
			frappe.throw(_("No employees matched the given selection"))

		frappe.enqueue(
			SYNC_EMPLOYEES_METHOD,
			queue="long",
			enqueue_after_commit=True,
			timeout=len(employee_ids) * PER_EMPLOYEE_TIMEOUT_SECONDS,
			settings_name=self.name,
			employees=employee_ids,
		)

		return {"queued": True, "total": len(employee_ids)}
