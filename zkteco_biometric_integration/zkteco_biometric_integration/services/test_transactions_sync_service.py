# Copyright (c) 2025, Navari Limited and contributors
# For license information, please see license.txt

import unittest
from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.utils import add_days, get_datetime, getdate

from zkteco_biometric_integration.zkteco_biometric_integration import SCHEDULED_JOB_METHOD
from zkteco_biometric_integration.zkteco_biometric_integration.api.test_utils import create_settings
from zkteco_biometric_integration.zkteco_biometric_integration.services.transactions_sync_service import (
	SYNC_SAFETY_BUFFER_MINUTES,
	advance_attendance_watermark,
	process_transactions,
)

SERVICE = "zkteco_biometric_integration.zkteco_biometric_integration.services.transactions_sync_service"

TEST_SHIFT_PREFIX = "_Test ZKTeco Shift"
TEST_HR_MANAGER_EMAIL = "_test_zkteco_hr_manager@example.com"


def create_shift(
	suffix: str,
	*,
	enable_auto_attendance: int = 1,
	auto_update_last_sync: int = 0,
	last_sync=None,
) -> str:
	"""A Shift Type whose watermark the sync service may or may not manage."""
	name = f"{TEST_SHIFT_PREFIX} {suffix}"
	if frappe.db.exists("Shift Type", name):
		frappe.delete_doc("Shift Type", name, force=True, ignore_permissions=True, delete_permanently=True)

	frappe.get_doc(
		{
			"doctype": "Shift Type",
			"__newname": name,
			"start_time": "08:00:00",
			"end_time": "17:00:00",
			"enable_auto_attendance": enable_auto_attendance,
			"auto_update_last_sync": auto_update_last_sync,
			"determine_check_in_and_check_out": "Alternating entries as IN and OUT during the same shift",
			"working_hours_calculation_based_on": "First Check-in and Last Check-out",
			"process_attendance_after": add_days(getdate(), -2),
			"last_sync_of_checkin": last_sync,
		}
	).insert(ignore_permissions=True)

	return name


def cleanup_shifts() -> None:
	for name in frappe.get_all(
		"Shift Type", filters={"name": ("like", f"{TEST_SHIFT_PREFIX}%")}, pluck="name"
	):
		frappe.delete_doc("Shift Type", name, force=True, ignore_permissions=True, delete_permanently=True)
	frappe.db.commit()  # nosemgrep


def watermark_of(shift: str):
	value = frappe.db.get_value("Shift Type", shift, "last_sync_of_checkin")
	return get_datetime(value) if value else None


class TestAttendanceWatermark(unittest.TestCase):
	"""advance_attendance_watermark tells HRMS how far auto attendance may run.

	It commits, and it writes to every qualifying Shift Type on the site - not
	only the ones a test creates - so real watermarks are snapshotted and put
	back afterwards.
	"""

	def setUp(self):
		frappe.set_user("Administrator")
		cleanup_shifts()
		self.snapshot = frappe.get_all("Shift Type", fields=["name", "last_sync_of_checkin"])

	def tearDown(self):
		cleanup_shifts()
		for row in self.snapshot:
			frappe.db.set_value(
				"Shift Type",
				row.name,
				"last_sync_of_checkin",
				row.last_sync_of_checkin,
				update_modified=False,
			)
		# restoring real watermarks the committing service overwrote
		frappe.db.commit()  # nosemgrep

	def test_watermark_lands_one_safety_buffer_behind_the_run(self):
		shift = create_shift("Managed", last_sync=get_datetime() - timedelta(days=2))
		run_started_at = get_datetime().replace(microsecond=0)

		advance_attendance_watermark(run_started_at)

		self.assertEqual(
			watermark_of(shift),
			run_started_at - timedelta(minutes=SYNC_SAFETY_BUFFER_MINUTES),
		)

	def test_never_synced_shift_gets_an_initial_watermark(self):
		shift = create_shift("NeverSynced", last_sync=None)
		run_started_at = get_datetime().replace(microsecond=0)

		advance_attendance_watermark(run_started_at)

		self.assertEqual(
			watermark_of(shift),
			run_started_at - timedelta(minutes=SYNC_SAFETY_BUFFER_MINUTES),
		)

	def test_watermark_never_moves_backwards(self):
		"""A high-water mark: an older run must not rewind attendance already processed."""
		ahead = (get_datetime() + timedelta(days=1)).replace(microsecond=0)
		shift = create_shift("Ahead", last_sync=ahead)

		advance_attendance_watermark(get_datetime())

		self.assertEqual(watermark_of(shift), ahead)

	def test_hrms_managed_shifts_are_left_alone(self):
		"""auto_update_last_sync=1 means HRMS owns the clock; we must not fight it."""
		original = (get_datetime() - timedelta(days=2)).replace(microsecond=0)
		shift = create_shift("HRMSManaged", auto_update_last_sync=1, last_sync=original)

		advance_attendance_watermark(get_datetime())

		self.assertEqual(watermark_of(shift), original)

	def test_shifts_without_auto_attendance_are_left_alone(self):
		original = (get_datetime() - timedelta(days=2)).replace(microsecond=0)
		shift = create_shift("NoAutoAttendance", enable_auto_attendance=0, last_sync=original)

		advance_attendance_watermark(get_datetime())

		self.assertEqual(watermark_of(shift), original)


class TestProcessTransactions(unittest.TestCase):
	"""process_transactions() gained a settings_name arg and watermark bookkeeping."""

	@patch(
		"zkteco_biometric_integration.zkteco_biometric_integration.doctype."
		"zkteco_biometric_settings.zkteco_biometric_settings."
		"ZKTecoBiometricSettings.generate_token"
	)
	def setUp(self, mock_generate_token):
		frappe.set_user("Administrator")
		mock_generate_token.return_value = None
		self.settings = create_settings()
		self.settings.db_set("is_fetch_enabled", 1)

	def tearDown(self):
		frappe.set_user("Administrator")
		self.settings.delete(force=True, ignore_permissions=True, delete_permanently=True)
		# fixtures are committed, so removing them must be committed as well
		frappe.db.commit()  # nosemgrep

	@patch(f"{SERVICE}.advance_attendance_watermark")
	@patch(
		f"{SERVICE}.get_transactions",
		return_value=[{"emp_code": "_T-NOBODY", "punch_time": "2026-08-17 07:52:00"}],
	)
	def test_scheduled_full_run_advances_the_watermark(self, mock_get_transactions, mock_advance):
		process_transactions()

		mock_advance.assert_called_once()
		# the watermark is stamped from when the run began, not from an arbitrary "now"
		self.assertIsNotNone(mock_advance.call_args.args[0])

	@patch(f"{SERVICE}.advance_attendance_watermark")
	@patch(f"{SERVICE}.get_transactions", return_value=[])
	def test_empty_response_does_not_advance_the_watermark(self, mock_get_transactions, mock_advance):
		"""A hosted BioTime stays reachable while its terminal is offline.

		The call then succeeds and returns nothing, which is indistinguishable from
		an hour in which nobody punched - so it is not evidence that the day's data
		has arrived.
		"""
		process_transactions()

		mock_advance.assert_not_called()

	@patch(f"{SERVICE}.advance_attendance_watermark")
	@patch(f"{SERVICE}.get_transactions", return_value=[])
	def test_targeted_run_syncs_only_that_source_and_skips_the_watermark(
		self, mock_get_transactions, mock_advance
	):
		"""The Fetch Transactions button must not vouch for sources it never polled."""
		process_transactions(self.settings.name)

		self.assertEqual(mock_get_transactions.call_count, 1)
		synced_settings = mock_get_transactions.call_args.args[0]
		self.assertEqual(synced_settings.name, self.settings.name)
		mock_advance.assert_not_called()

	@patch.object(frappe, "log_error")
	@patch(f"{SERVICE}.advance_attendance_watermark")
	@patch(f"{SERVICE}.get_transactions", side_effect=Exception("device unreachable"))
	def test_a_failed_source_blocks_the_watermark(self, mock_get_transactions, mock_advance, mock_log_error):
		"""If any device did not report, attendance must not be told the data is complete."""
		process_transactions()

		mock_advance.assert_not_called()
		mock_log_error.assert_called()
		self.assertEqual(mock_log_error.call_args.kwargs["title"], "Employee Checkin Sync Error")

	@patch(f"{SERVICE}.advance_attendance_watermark")
	def test_no_enabled_sources_does_not_advance_the_watermark(self, mock_advance):
		with patch.object(frappe, "get_all", return_value=[]):
			process_transactions()

		mock_advance.assert_not_called()


class TestFetchTransactionsAction(unittest.TestCase):
	"""The Fetch Transactions button on ZKTeco Biometric Settings."""

	@patch(
		"zkteco_biometric_integration.zkteco_biometric_integration.doctype."
		"zkteco_biometric_settings.zkteco_biometric_settings."
		"ZKTecoBiometricSettings.generate_token"
	)
	def setUp(self, mock_generate_token):
		frappe.set_user("Administrator")
		mock_generate_token.return_value = None
		self.settings = create_settings()

	def tearDown(self):
		frappe.set_user("Administrator")
		self.settings.delete(force=True, ignore_permissions=True, delete_permanently=True)
		# fixtures are committed, so removing them must be committed as well
		frappe.db.commit()  # nosemgrep

	@patch.object(frappe, "enqueue")
	def test_button_enqueues_a_run_scoped_to_this_settings_doc(self, mock_enqueue):
		self.assertTrue(self.settings.fetch_transactions())

		mock_enqueue.assert_called_once_with(
			method=SCHEDULED_JOB_METHOD,
			enqueue_after_commit=True,
			settings_name=self.settings.name,
		)

	def test_button_is_restricted_to_privileged_roles(self):
		# created before enqueue is patched: User.insert() enqueues contact creation itself
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": "_test_zkteco_plain@example.com",
				"first_name": "_Test ZKTeco Plain",
				"enabled": 1,
				"roles": [{"role": "Employee"}],
			}
		).insert(ignore_permissions=True)

		try:
			frappe.set_user(user.name)
			with patch.object(frappe, "enqueue") as mock_enqueue:
				with self.assertRaises(frappe.PermissionError):
					self.settings.fetch_transactions()
				mock_enqueue.assert_not_called()
		finally:
			frappe.set_user("Administrator")
			frappe.delete_doc("User", user.name, force=True, ignore_permissions=True)

	def test_scheduler_events_reference_real_methods(self):
		from zkteco_biometric_integration import hooks

		scheduled = [method for methods in hooks.scheduler_events.values() for method in methods]
		self.assertIn(SCHEDULED_JOB_METHOD, scheduled)

		for method in scheduled:
			self.assertTrue(callable(frappe.get_attr(method)), f"{method} is not callable")
