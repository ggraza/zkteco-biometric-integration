"""Tests for the attendance watermark written by the biometric sync.

The bug these guard against: auto attendance used to finalise a day from the
clock alone, so an unreachable biometric server at closing time produced
"Absent, 0 hours" for employees who had worked a full day. The watermark must
only ever move on evidence of a successful fetch.
"""

import unittest
from unittest.mock import patch

import frappe
from frappe.utils import add_days, add_to_date, get_datetime, getdate

from zkteco_biometric_integration.zkteco_biometric_integration.api.test_utils import (
	cleanup_employee,
	cleanup_user,
	create_settings,
)
from zkteco_biometric_integration.zkteco_biometric_integration.services.transactions_sync_service import (
	SYNC_SAFETY_BUFFER_MINUTES,
	advance_attendance_watermark,
	process_transactions,
)

SERVICE = "zkteco_biometric_integration.zkteco_biometric_integration.services.transactions_sync_service"


class WatermarkTestCase(unittest.TestCase):
	"""Shared fixtures.

	`advance_attendance_watermark` commits, and it writes to every qualifying
	Shift Type on the site - not just the ones a test creates. So the original
	state of all shifts is snapshotted and restored, to keep tests from leaving
	real configuration behind.
	"""

	def setUp(self):
		frappe.set_user("Administrator")
		self.snapshot = frappe.get_all("Shift Type", fields=["name", "last_sync_of_checkin"])
		self.created_shifts = []

	def tearDown(self):
		for name in self.created_shifts:
			frappe.delete_doc("Shift Type", name, force=True, ignore_permissions=True)

		for row in self.snapshot:
			frappe.db.set_value(
				"Shift Type",
				row.name,
				"last_sync_of_checkin",
				row.last_sync_of_checkin,
				update_modified=False,
			)

		frappe.db.commit()

	def make_shift(
		self,
		name: str,
		auto_update_last_sync: int = 0,
		last_sync: str | None = None,
		enable_auto_attendance: int = 1,
	) -> str:
		if frappe.db.exists("Shift Type", name):
			frappe.delete_doc("Shift Type", name, force=True, ignore_permissions=True)

		shift = frappe.get_doc(
			{
				"doctype": "Shift Type",
				"__newname": name,
				"start_time": "08:00:00",
				"end_time": "16:30:00",
				"begin_check_in_before_shift_start_time": 60,
				"allow_check_out_after_shift_end_time": 60,
				"enable_auto_attendance": enable_auto_attendance,
				"auto_update_last_sync": auto_update_last_sync,
				"process_attendance_after": add_days(getdate(), -30),
				"last_sync_of_checkin": last_sync,
			}
		).insert(ignore_permissions=True)

		self.created_shifts.append(shift.name)
		return shift.name

	def get_watermark(self, shift: str):
		value = frappe.db.get_value("Shift Type", shift, "last_sync_of_checkin")
		return get_datetime(value) if value else None

	def freshen_all_shifts(self):
		"""Put every managed shift comfortably inside the stale threshold."""
		for row in frappe.get_all(
			"Shift Type", filters={"enable_auto_attendance": 1, "auto_update_last_sync": 0}
		):
			frappe.db.set_value(
				"Shift Type", row.name, "last_sync_of_checkin", get_datetime(), update_modified=False
			)


class TestAdvanceAttendanceWatermark(WatermarkTestCase):
	def test_applies_safety_buffer(self):
		"""The watermark lags the fetch, so punches still in transit are not written off."""
		shift = self.make_shift("_Test Watermark Buffer")

		advance_attendance_watermark(get_datetime("2026-08-17 19:00:00"))

		expected = add_to_date(get_datetime("2026-08-17 19:00:00"), minutes=-SYNC_SAFETY_BUFFER_MINUTES)
		self.assertEqual(self.get_watermark(shift), expected)

	def test_never_moves_backwards(self):
		"""A late or out-of-order run must not un-confirm data already confirmed."""
		shift = self.make_shift("_Test Watermark Backwards", last_sync="2026-08-17 18:00:00")

		advance_attendance_watermark(get_datetime("2026-08-17 17:00:00"))

		self.assertEqual(self.get_watermark(shift), get_datetime("2026-08-17 18:00:00"))

	def test_ignores_shifts_still_on_clock_based_updater(self):
		"""HRMS owns shifts with auto_update_last_sync ticked; we must not fight it."""
		shift = self.make_shift(
			"_Test Watermark Clock Owned", auto_update_last_sync=1, last_sync="2026-08-17 10:00:00"
		)

		advance_attendance_watermark(get_datetime("2026-08-17 19:00:00"))

		self.assertEqual(self.get_watermark(shift), get_datetime("2026-08-17 10:00:00"))

	def test_ignores_shifts_without_auto_attendance(self):
		shift = self.make_shift(
			"_Test Watermark No Auto Attendance",
			enable_auto_attendance=0,
			last_sync="2026-08-17 10:00:00",
		)

		advance_attendance_watermark(get_datetime("2026-08-17 19:00:00"))

		self.assertEqual(self.get_watermark(shift), get_datetime("2026-08-17 10:00:00"))


class TestProcessTransactionsGating(WatermarkTestCase):
	"""The watermark must move only when every enabled source reported in."""

	def setUp(self):
		super().setUp()
		with patch(
			"zkteco_biometric_integration.zkteco_biometric_integration.doctype."
			"zkteco_biometric_settings.zkteco_biometric_settings."
			"ZKTecoBiometricSettings.generate_token"
		):
			self.settings = create_settings()

	def tearDown(self):
		self.settings.delete(force=True, ignore_permissions=True, delete_permanently=True)
		cleanup_employee()
		cleanup_user()
		super().tearDown()

	@patch(f"{SERVICE}.advance_attendance_watermark")
	@patch(f"{SERVICE}.get_transactions")
	def test_successful_run_advances_watermark(self, mock_get_transactions, mock_advance):
		mock_get_transactions.return_value = iter(
			[{"emp_code": "_T-NOBODY", "punch_time": "2026-08-17 07:52:00"}]
		)

		process_transactions()

		mock_advance.assert_called_once()

	@patch(f"{SERVICE}.advance_attendance_watermark")
	@patch(f"{SERVICE}.get_transactions")
	def test_empty_response_does_not_advance_watermark(self, mock_get_transactions, mock_advance):
		"""The hosted-BioTime failure: reachable server, offline terminal, no punches."""
		mock_get_transactions.return_value = iter([])

		process_transactions()

		mock_advance.assert_not_called()

	@patch(f"{SERVICE}.advance_attendance_watermark")
	@patch(f"{SERVICE}.get_transactions")
	def test_unreachable_server_does_not_advance_watermark(self, mock_get_transactions, mock_advance):
		"""The regression itself: a failed fetch must leave attendance pending."""
		mock_get_transactions.side_effect = Exception("connection refused")

		process_transactions()

		mock_advance.assert_not_called()

	@patch(f"{SERVICE}.advance_attendance_watermark")
	@patch(f"{SERVICE}.get_transactions")
	def test_manual_single_source_sync_does_not_advance_watermark(self, mock_get_transactions, mock_advance):
		"""One device says nothing about the other locations."""
		mock_get_transactions.return_value = iter([])

		process_transactions(settings_name=self.settings.name)

		mock_advance.assert_not_called()
