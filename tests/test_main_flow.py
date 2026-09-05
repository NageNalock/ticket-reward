from __future__ import annotations

import argparse
import unittest
from contextlib import nullcontext
from unittest.mock import MagicMock, patch

from src.main import main, run


class MainFlowTests(unittest.TestCase):
    def test_daily_set_runs_before_searches(self) -> None:
        args = argparse.Namespace(
            mode=None,
            pc_count=None,
            mobile_count=None,
            skip_daily=False,
            skip_pc=False,
            skip_mobile=False,
            check_login=False,
            trigger="manual",
        )
        config = {
            "search": {"pc_count": 1, "mobile_count": 1},
            "daily_activities": {"enabled": True},
        }
        events: list[str] = []

        def read_points(_context, *, phase="任务前") -> int:
            events.append(phase)
            return 100

        with (
            patch("src.main.ensure_runtime_dirs"),
            patch("src.main.load_config", return_value=config),
            patch("src.main._load_search_terms", return_value=["测试"]),
            patch("src.main.project_path", return_value=MagicMock()),
            patch("src.main._read_points", side_effect=read_points),
            patch("src.main.send_notification"),
            patch("src.main.PointsTracker") as tracker_class,
            patch("src.main.BrowserManager") as browser_class,
            patch("src.main.DailyActivitiesTask") as daily_class,
            patch("src.main.PcSearchTask") as pc_class,
            patch("src.main.MobileSearchTask") as mobile_class,
        ):
            browser = browser_class.return_value
            browser.is_logged_in.return_value = True
            browser.mode = "pc"
            tracker_class.return_value.record_run.return_value = {"earned": 0}

            def run_daily() -> dict[str, list[str]]:
                events.append("daily")
                return {
                    "completed": ["daily"], "failed": [],
                    "skipped": ["推荐活动 (配置跳过)"],
                }

            def run_pc() -> dict[str, object]:
                events.append("pc")
                return {"completed": 1, "total": 1, "failed_terms": []}

            def run_mobile() -> dict[str, object]:
                events.append("mobile")
                return {"completed": 1, "total": 1, "failed_terms": []}

            daily_class.return_value.run.side_effect = run_daily
            pc_class.return_value.run.side_effect = run_pc
            mobile_class.return_value.run.side_effect = run_mobile

            self.assertEqual(run(args), 0)

        self.assertEqual(events, ["任务前", "daily", "pc", "mobile", "任务后"])
        record = tracker_class.return_value.record_run.call_args
        self.assertEqual(record.args[3], [])
        self.assertEqual(record.kwargs["skipped_tasks"], ["推荐活动 (配置跳过)"])

    def test_unhandled_worker_error_is_written_to_history(self) -> None:
        with (
            patch("src.main.setup_logger"),
            patch("src.main.single_instance", return_value=nullcontext()),
            patch("src.main.run", side_effect=Exception("network down")),
            patch("src.main.PointsTracker") as tracker_class,
        ):
            self.assertEqual(main(["--trigger", "ui"]), 1)

        call = tracker_class.return_value.record_run.call_args
        self.assertIsNotNone(call)
        self.assertEqual(call.args[3], ["runtime_error:Exception"])
        self.assertEqual(call.kwargs["trigger"], "ui")


if __name__ == "__main__":
    unittest.main()
