from __future__ import annotations

import unittest

from src.ui.history_view import (
    extract_live_progress,
    format_history_row,
    format_running_row,
    format_summary,
)


class HistoryViewTests(unittest.TestCase):
    def test_formats_run_for_native_table(self) -> None:
        row = format_history_row(
            {
                "timestamp": "2026-08-17T23:30:12",
                "status": "partial",
                "trigger": "ui",
                "earned": 15,
                "tasks_completed": ["pc_search(5/5)"],
                "tasks_skipped": ["推荐活动 (配置跳过)"],
                "tasks_failed": ["daily"],
                "duration_sec": 61.2,
            }
        )
        self.assertEqual(row["time"], "2026-08-17 23:30")
        self.assertEqual(row["status"], "部分失败")
        self.assertEqual(row["earned"], "+15")
        self.assertEqual(row["duration"], "61s")
        self.assertEqual(row["skipped"], "推荐活动 (配置跳过)")

    def test_old_history_without_skipped_tasks_still_displays(self) -> None:
        row = format_history_row({"tasks_failed": ["points_unverified"]})
        self.assertEqual(row["skipped"], "—")
        self.assertEqual(row["failed"], "积分未核实")

    def test_formats_summary_cards(self) -> None:
        cards = format_summary({"total": 4, "success": 2, "partial": 1, "failed": 1, "earned": 30})
        self.assertEqual(cards[0], "运行次数\n4")
        self.assertEqual(cards[-1], "累计积分\n+30")

    def test_extracts_latest_worker_progress(self) -> None:
        log = """
2026-08-20 12:00:00 | INFO | PC 搜索 30/30 完成: 测试
2026-08-20 12:01:00 | INFO | MOBILE 搜索 7/20 完成: 测试
"""
        self.assertEqual(extract_live_progress(log), "移动搜索 7/20")
        self.assertEqual(
            extract_live_progress("执行每日活动: 今日卡片 (keyword_search, +10)"),
            "每日任务：今日卡片",
        )
        self.assertEqual(extract_live_progress("unrelated debug output"), "")

    def test_reports_balance_readiness_progress(self) -> None:
        self.assertEqual(
            extract_live_progress("任务前积分读取 2/3：等待 Rewards 加载"),
            "任务前积分：正在读取 2/3",
        )
        self.assertEqual(
            extract_live_progress("任务后积分读取 1/3：等待 Rewards 加载"),
            "任务后积分：正在读取 1/3",
        )

    def test_formats_running_row(self) -> None:
        row = format_running_row(
            "2026-08-20T12:02:20",
            "ui",
            "PC 搜索 12/30",
            245,
        )
        self.assertEqual(row["status"], "运行中")
        self.assertEqual(row["trigger"], "菜单栏")
        self.assertEqual(row["completed"], "PC 搜索 12/30")
        self.assertEqual(row["duration"], "245s")
