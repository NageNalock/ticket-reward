from __future__ import annotations

import unittest
from datetime import date, datetime

from src.ui.scheduler import calculate_next_run, calculate_retry_run, find_pending_retry


class SchedulerTests(unittest.TestCase):
    def test_schedules_later_today(self) -> None:
        now = datetime(2026, 8, 17, 9, 0)
        result = calculate_next_run(now, 9, 30, 600, randint=lambda _a, _b: 120)
        self.assertEqual(result, datetime(2026, 8, 17, 9, 32))

    def test_rolls_to_tomorrow_after_window(self) -> None:
        now = datetime(2026, 8, 17, 10, 0)
        result = calculate_next_run(now, 9, 30, 600, randint=lambda _a, _b: 0)
        self.assertEqual(result, datetime(2026, 8, 18, 9, 30))

    def test_zero_jitter_is_supported(self) -> None:
        now = datetime(2026, 8, 17, 8, 0)
        result = calculate_next_run(now, 9, 30, 0)
        self.assertEqual(result, datetime(2026, 8, 17, 9, 30))

    def test_retry_stays_on_same_day(self) -> None:
        now = datetime(2026, 9, 2, 10, 0)
        result = calculate_retry_run(now, date(2026, 9, 2), 30)
        self.assertEqual(result, datetime(2026, 9, 2, 10, 30))

    def test_retry_does_not_cross_midnight(self) -> None:
        now = datetime(2026, 9, 2, 23, 45)
        self.assertIsNone(calculate_retry_run(now, date(2026, 9, 2), 30))

    def test_recovers_failed_scheduled_run(self) -> None:
        records = [
            {
                "timestamp": "2026-09-02T09:45:00",
                "trigger": "scheduled",
                "status": "partial",
                "tasks_failed": ["pc_search:example"],
            }
        ]
        plan = find_pending_retry(datetime(2026, 9, 2, 10, 0), records, 3, 30)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.retry_number, 1)
        self.assertEqual(plan.run_at, datetime(2026, 9, 2, 10, 15))

    def test_overdue_retry_runs_promptly_after_app_restart(self) -> None:
        records = [
            {
                "timestamp": "2026-09-02T09:30:00",
                "trigger": "scheduled",
                "status": "failed",
                "tasks_failed": ["runtime_error:TimeoutError"],
            }
        ]
        plan = find_pending_retry(datetime(2026, 9, 2, 12, 0), records, 3, 30)
        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.run_at, datetime(2026, 9, 2, 12, 0, 1))

    def test_manual_success_cancels_pending_retry(self) -> None:
        records = [
            {
                "timestamp": "2026-09-02T09:30:00",
                "trigger": "scheduled",
                "status": "failed",
                "tasks_failed": ["daily_activities"],
            },
            {
                "timestamp": "2026-09-02T10:00:00",
                "trigger": "ui",
                "status": "success",
                "tasks_failed": [],
            },
        ]
        self.assertIsNone(find_pending_retry(datetime(2026, 9, 2, 10, 5), records, 3, 30))

    def test_stops_after_configured_retry_count(self) -> None:
        records = [
            {
                "timestamp": f"2026-09-02T{hour:02d}:00:00",
                "trigger": "scheduled",
                "status": "failed",
                "tasks_failed": ["pc_search"],
            }
            for hour in (9, 10, 11, 12)
        ]
        self.assertIsNone(find_pending_retry(datetime(2026, 9, 2, 12, 5), records, 3, 30))

    def test_login_expiry_is_not_retried(self) -> None:
        records = [
            {
                "timestamp": "2026-09-02T09:30:00",
                "trigger": "scheduled",
                "status": "failed",
                "tasks_failed": ["login_expired"],
            }
        ]
        self.assertIsNone(find_pending_retry(datetime(2026, 9, 2, 10, 0), records, 3, 30))
