# Copyright (c) 2026, Navari Limited and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

from zkteco_biometric_integration.zkteco_biometric_integration import (
	SCHEDULED_JOB_METHOD,
)


class ZKTecoGlobal(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		cron_expression: DF.Data | None
		fetch_frequency: DF.Literal[
			"",
			"All",
			"Hourly",
			"Hourly Long",
			"Hourly Maintenance",
			"Daily",
			"Daily Long",
			"Daily Maintenance",
			"Weekly",
			"Weekly Long",
			"Monthly",
			"Monthly Long",
			"Yearly",
			"Cron",
		]
	# end: auto-generated types

	def validate(self):
		self.validate_cron_expression()
		self.update_schedule_job()

	@property
	def is_cron(self) -> bool:
		return self.fetch_frequency == "Cron"

	def validate_cron_expression(self) -> None:
		if self.is_cron and not self.cron_expression:
			frappe.throw(_("Cron Expression is required when Fetch Frequency is set to Cron"))

	def update_schedule_job(self) -> None:
		if not self.fetch_frequency or (self.is_cron and not self.cron_expression):
			return

		job_name = frappe.db.exists("Scheduled Job Type", {"method": SCHEDULED_JOB_METHOD})
		if not job_name:
			return

		frequency = "Cron" if self.is_cron else self.fetch_frequency
		cron_format = self.cron_expression if self.is_cron else ""

		job = frappe.get_doc("Scheduled Job Type", job_name)
		if job.frequency == frequency and (job.cron_format or "") == cron_format:
			return

		job.db_set(
			{"frequency": frequency, "cron_format": cron_format},
			update_modified=False,
		)


def update_scheduled_job() -> None:
	frappe.get_cached_doc("ZKTeco Global").update_schedule_job()
