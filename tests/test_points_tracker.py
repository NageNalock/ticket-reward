from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.rewards.points_tracker import PointsTracker


class PointsTrackerTests(unittest.TestCase):
    def test_records_earned_points_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            tracker = PointsTracker(path)
            record = tracker.record_run(100, 135, ["pc_search"], [], 12.34)
            persisted = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(record["earned"], 35)
        self.assertEqual(record["status"], "success")
        self.assertEqual(persisted["runs"][0]["duration_sec"], 12.3)

    def test_missing_point_values_do_not_create_fake_earnings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            tracker = PointsTracker(Path(directory) / "history.json")
            record = tracker.record_run(None, None, [], ["points"], 1)
        self.assertIsNone(record["earned"])
        self.assertEqual(record["status"], "failed")

    def test_skipped_tasks_are_persisted_separately_from_successes_and_failures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            tracker = PointsTracker(path)
            tracker.record_run(
                100, 120, ["每日任务"], [], 2,
                skipped_tasks=["推荐活动 (配置跳过)"],
            )
            record = PointsTracker(path).get_history(1)[0]

        self.assertEqual(record["status"], "success")
        self.assertEqual(record["tasks_completed"], ["每日任务"])
        self.assertEqual(record["tasks_failed"], [])
        self.assertEqual(record["tasks_skipped"], ["推荐活动 (配置跳过)"])

    def test_history_is_returned_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            path.write_text(
                json.dumps(
                    {
                        "2026-01-01": {"earned": 1},
                        "2026-01-03": {"earned": 3},
                        "2026-01-02": {"earned": 2},
                    }
                ),
                encoding="utf-8",
            )
            history = PointsTracker(path).get_history(2)
        self.assertEqual([item["date"] for item in history], ["2026-01-03", "2026-01-02"])

    def test_multiple_runs_on_one_day_are_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "history.json"
            tracker = PointsTracker(path)
            tracker.record_run(1, 2, ["first"], [], 1)
            tracker.record_run(2, 3, ["second"], ["warning"], 2)
            reloaded = PointsTracker(path)

        history = reloaded.get_history(10)
        self.assertEqual(len(history), 2)
        self.assertEqual(reloaded.get_summary()["total"], 2)
        self.assertEqual(reloaded.get_summary()["partial"], 1)


if __name__ == "__main__":
    unittest.main()
