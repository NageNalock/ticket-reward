from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from src.rewards.panel_parser import (
    PanelParser,
    TaskCard,
    classify_task,
    extract_current_points,
    extract_task_points,
    offer_is_completed,
)


class PanelParserTests(unittest.TestCase):
    def test_extracts_localized_balance(self) -> None:
        self.assertEqual(extract_current_points("7,658\n我的积分"), 7658)
        self.assertEqual(extract_current_points("Points balance 12,345"), 12345)

    def test_reward_chip_is_not_a_balance(self) -> None:
        self.assertIsNone(extract_current_points("+15 points"))
        self.assertIsNone(extract_current_points("任务奖励 7,500 积分"))

    def test_extracts_points_only_from_scoped_offer_chip(self) -> None:
        self.assertEqual(extract_task_points("任务 +15"), 15)
        self.assertEqual(extract_task_points("任务\n10", "10 积分"), 10)
        self.assertEqual(extract_task_points("可赚取 7,500 积分"), 0)

    def test_reads_authoritative_offer_completion_marker(self) -> None:
        self.assertFalse(offer_is_completed("今日活动 - Offer not Completed"))
        self.assertTrue(offer_is_completed("今日活动 - Offer Completed"))
        self.assertTrue(offer_is_completed("今日活动 - Offer is Completed"))
        self.assertTrue(offer_is_completed("今日活动 - 已完成"))

    def test_classifies_known_task_types(self) -> None:
        self.assertEqual(classify_task("完成此拼图", "排列图块"), "puzzle")
        self.assertEqual(classify_task("每日测验"), "quiz")
        self.assertEqual(classify_task("邀请朋友获得奖励"), "referral")
        self.assertEqual(
            classify_task("南非风光", href="https://www.bing.com/search?q=桌山"),
            "keyword_search",
        )
        self.assertEqual(classify_task("正在进行中!"), "unknown")

    def test_rejects_parent_containers_with_many_reward_chips(self) -> None:
        text = "任务一 +15\n任务二 +15\n任务三 +5"
        self.assertFalse(PanelParser._looks_like_task(text))

    def test_rejects_status_messages_as_tasks(self) -> None:
        self.assertFalse(PanelParser._looks_like_task("正在进行中!\n1000"))
        self.assertFalse(PanelParser._looks_like_task("你已获得 15 积分！"))
        self.assertFalse(PanelParser._looks_like_task("You're on track for Star bonus points\n3"))

    def test_removed_daily_card_is_treated_as_server_completed(self) -> None:
        parser = PanelParser.__new__(PanelParser)
        remaining = TaskCard(
            title="仍待完成",
            description="",
            points=10,
            task_type="keyword_search",
            element=MagicMock(),
        )
        parser.parse_daily_tasks = MagicMock(return_value=[remaining])
        parser.daily_set_is_complete = MagicMock(return_value=False)

        self.assertEqual(parser.daily_task_state("已移除的任务"), "completed")
        self.assertEqual(parser.daily_task_state("仍待完成"), "pending")

    def test_empty_daily_set_requires_completion_marker(self) -> None:
        parser = PanelParser.__new__(PanelParser)
        parser.parse_daily_tasks = MagicMock(return_value=[])
        parser.daily_set_is_complete = MagicMock(side_effect=[True, False])

        self.assertEqual(parser.daily_task_state("最后一项"), "completed")
        self.assertEqual(parser.daily_task_state("无法确认"), "unknown")


if __name__ == "__main__":
    unittest.main()
