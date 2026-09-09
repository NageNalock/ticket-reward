from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import MagicMock, patch

from src.rewards.panel_parser import TaskCard
from src.tasks.daily_activities import DailyActivitiesTask


class DailyActivitiesTests(unittest.TestCase):
    @patch("src.tasks.daily_activities.random_delay")
    @patch("src.tasks.daily_activities.PanelParser")
    def test_completed_daily_set_is_not_reported_as_failure(
        self, parser_class: MagicMock, _delay: MagicMock
    ) -> None:
        context = MagicMock()
        page = context.new_page.return_value
        parser = parser_class.return_value
        parser.open_panel.return_value = True
        parser.parse_tasks.return_value = []
        parser.daily_set_is_complete.return_value = True

        result = DailyActivitiesTask(context, {"daily_activities": {}}).run()

        self.assertEqual(result["completed"], [])
        self.assertEqual(result["failed"], [])
        self.assertEqual(result["skipped"], ["每日设置 (已完成)"])
        page.close.assert_called_once()

    @patch("src.tasks.daily_activities.PanelParser")
    def test_server_confirmed_state_finishes_verification(self, parser_class: MagicMock) -> None:
        page = MagicMock()
        parser = parser_class.return_value
        parser.open_panel.return_value = True
        parser.task_state.return_value = "completed"
        runner = DailyActivitiesTask(MagicMock(), {"daily_activities": {}})
        runner.VERIFY_ATTEMPTS = 1
        runner.VERIFY_DELAY_MS = 0
        card = TaskCard("每日任务", "", 10, "visit", MagicMock(), task_id="daily-1")

        self.assertTrue(runner._wait_for_completion(page, card))
        parser.task_state.assert_called_once_with(card, timeout_ms=8_000)

    @patch("src.tasks.daily_activities.random_delay")
    @patch("src.tasks.daily_activities.PanelParser")
    def test_locked_and_unsupported_video_cards_are_skipped_before_rebinding(
        self, parser_class: MagicMock, _delay: MagicMock
    ) -> None:
        parser = parser_class.return_value
        parser.open_panel.return_value = True
        parser.parse_tasks.return_value = [
            TaskCard("锁定活动", "", 10, "visit", MagicMock(), available=False),
            TaskCard("锁定视频", "", 10, "video", MagicMock(), available=False),
            TaskCard("观看视频", "", 10, "video", MagicMock()),
        ]
        runner = DailyActivitiesTask(MagicMock(), {"daily_activities": {}})
        runner._rebind_task = MagicMock(return_value=None)
        runner._run_task = MagicMock()
        runner._wait_for_completion = MagicMock()

        result = runner.run()

        self.assertEqual(result["skipped"], [
            "锁定活动 (未解锁)", "锁定视频 (未解锁)", "观看视频 (暂不支持视频任务)",
        ])
        self.assertEqual(result["completed"], [])
        self.assertEqual(result["failed"], [])
        runner._rebind_task.assert_not_called()
        runner._run_task.assert_not_called()
        runner._wait_for_completion.assert_not_called()

    @patch("src.tasks.daily_activities.random_delay")
    @patch("src.tasks.daily_activities.PanelParser")
    def test_card_that_becomes_locked_after_scanning_is_also_skipped(
        self, parser_class: MagicMock, _delay: MagicMock
    ) -> None:
        original = TaskCard("今日探索", "", 10, "visit", MagicMock(), task_id="one")
        parser = parser_class.return_value
        parser.open_panel.return_value = True
        parser.parse_tasks.side_effect = [[original], [replace(original, available=False)]]
        runner = DailyActivitiesTask(MagicMock(), {"daily_activities": {}})
        runner._run_task = MagicMock()
        runner._wait_for_completion = MagicMock()

        result = runner.run()

        self.assertEqual(result["skipped"], ["今日探索 (未解锁)"])
        self.assertEqual(result["completed"], [])
        self.assertEqual(result["failed"], [])
        runner._run_task.assert_not_called()
        runner._wait_for_completion.assert_not_called()

    @patch("src.tasks.daily_activities.random_delay")
    @patch("src.tasks.daily_activities.PanelParser")
    def test_daily_card_skipped_by_config_is_neither_completed_nor_failed(
        self, parser_class: MagicMock, _delay: MagicMock
    ) -> None:
        context = MagicMock()
        card = TaskCard(
            title="推荐活动",
            description="",
            points=0,
            task_type="referral",
            element=MagicMock(),
        )
        parser = parser_class.return_value
        parser.open_panel.return_value = True
        parser.parse_tasks.return_value = [card]

        runner = DailyActivitiesTask(
            context,
            {"daily_activities": {"skip_types": ["referral"]}},
        )
        runner._run_task = MagicMock()
        runner._wait_for_completion = MagicMock()
        result = runner.run()

        self.assertEqual(result["completed"], [])
        self.assertEqual(result["skipped"], ["推荐活动 (配置跳过)"])
        self.assertEqual(result["failed"], [])
        runner._run_task.assert_not_called()
        runner._wait_for_completion.assert_not_called()

    @patch("src.tasks.daily_activities.random_delay")
    @patch("src.tasks.daily_activities.PanelParser")
    def test_rewards_confirmation_wins_when_landing_page_is_unrecognized(
        self, parser_class: MagicMock, _delay: MagicMock
    ) -> None:
        context = MagicMock()
        card = TaskCard(
            title="特殊落地页活动",
            description="",
            points=10,
            task_type="keyword_search",
            element=MagicMock(),
        )
        parser = parser_class.return_value
        parser.open_panel.return_value = True
        parser.parse_tasks.return_value = [card]
        runner = DailyActivitiesTask(
            context,
            {"daily_activities": {"skip_types": []}},
        )
        runner._run_task = MagicMock(return_value=False)
        runner._wait_for_completion = MagicMock(return_value=True)

        result = runner.run()

        self.assertEqual(result["completed"], ["特殊落地页活动"])
        self.assertEqual(result["failed"], [])
        runner._wait_for_completion.assert_called_once_with(context.new_page.return_value, card)

    @patch("src.tasks.daily_activities.random_delay")
    @patch("src.tasks.daily_activities.PanelParser")
    def test_extra_question_runs_after_daily_set_completed_without_merging_same_titles(
        self, parser_class: MagicMock, _delay: MagicMock
    ) -> None:
        pending = TaskCard("你是否知道答案？", "挑战自己", 5, "quiz", MagicMock(), task_id="one")
        completed = replace(pending, task_id="two", completed=True)
        parser = parser_class.return_value
        parser.open_panel.return_value = True
        parser.daily_set_is_complete.return_value = True
        parser.parse_tasks.return_value = [completed, pending]
        runner = DailyActivitiesTask(
            MagicMock(), {"daily_activities": {"skip_types": ["referral"]}}
        )
        runner._run_task = MagicMock(return_value=True)
        runner._wait_for_completion = MagicMock(return_value=True)

        result = runner.run()

        runner._run_task.assert_called_once_with(runner.context.new_page.return_value, pending)
        self.assertEqual(len(result["completed"]), 1)
        self.assertEqual(len(result["skipped"]), 1)
        self.assertNotEqual(result["completed"][0], result["skipped"][0].split(" (")[0])
        self.assertEqual(result["failed"], [])

    @patch("src.tasks.daily_activities.random_delay")
    @patch("src.tasks.daily_activities.PanelParser")
    def test_visit_is_not_blocked_by_referral_skip_but_opening_alone_is_not_success(
        self, parser_class: MagicMock, _delay: MagicMock
    ) -> None:
        card = TaskCard("将推荐转化为奖励", "", 10, "visit", MagicMock(), task_id="fixed-reward")
        parser = parser_class.return_value
        parser.open_panel.return_value = True
        parser.parse_tasks.return_value = [card]
        runner = DailyActivitiesTask(
            MagicMock(), {"daily_activities": {"skip_types": ["referral"]}}
        )
        runner._run_task = MagicMock(return_value=True)
        runner._wait_for_completion = MagicMock(return_value=False)

        result = runner.run()

        runner._run_task.assert_called_once()
        self.assertEqual(result["completed"], [])
        self.assertEqual(result["skipped"], [])
        self.assertEqual(result["failed"], ["将推荐转化为奖励 (未确认完成)"])

    @patch("src.tasks.daily_activities.PanelParser")
    def test_pending_and_unknown_states_are_not_counted_as_completion(
        self, parser_class: MagicMock
    ) -> None:
        parser = parser_class.return_value
        parser.open_panel.return_value = True
        parser.task_state.side_effect = ["pending", "unknown"]
        runner = DailyActivitiesTask(MagicMock(), {"daily_activities": {}})
        runner.VERIFY_ATTEMPTS = 2
        card = TaskCard("未到账活动", "", 5, "visit", MagicMock())
        self.assertFalse(runner._wait_for_completion(MagicMock(), card))
        self.assertEqual(parser.task_state.call_count, 2)

    def test_rebinding_does_not_choose_another_offer_after_dom_reorders(self) -> None:
        pending = TaskCard("同名活动", "", 5, "quiz", MagicMock(), task_id="one")
        other = replace(pending, task_id="two", completed=True)
        parser = MagicMock()
        parser.parse_tasks.return_value = [other, pending]
        self.assertIs(DailyActivitiesTask._rebind_task(MagicMock(), parser, pending), pending)


if __name__ == "__main__":
    unittest.main()
