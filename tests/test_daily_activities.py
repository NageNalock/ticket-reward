from __future__ import annotations

import unittest
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
        parser.parse_daily_tasks.return_value = []
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
        parser.daily_task_state.return_value = "completed"
        task = DailyActivitiesTask(MagicMock(), {"daily_activities": {}})
        task.VERIFY_ATTEMPTS = 1
        task.VERIFY_DELAY_MS = 0

        self.assertTrue(task._wait_for_completion(page, "每日任务"))
        parser.daily_task_state.assert_called_once_with("每日任务")

    @patch("src.tasks.daily_activities.random_delay")
    @patch("src.tasks.daily_activities.PanelParser")
    def test_required_daily_card_skipped_by_config_is_a_failure(
        self, parser_class: MagicMock, _delay: MagicMock
    ) -> None:
        context = MagicMock()
        card = TaskCard(
            title="推荐活动",
            description="",
            points=10,
            task_type="referral",
            element=MagicMock(),
        )
        parser = parser_class.return_value
        parser.open_panel.return_value = True
        parser.parse_daily_tasks.return_value = [card]

        result = DailyActivitiesTask(
            context,
            {"daily_activities": {"skip_types": ["referral"]}},
        ).run()

        self.assertEqual(result["completed"], [])
        self.assertEqual(result["skipped"], [])
        self.assertEqual(result["failed"], ["推荐活动 (配置跳过，未完成)"])

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
        parser.parse_daily_tasks.return_value = [card]
        runner = DailyActivitiesTask(
            context,
            {"daily_activities": {"skip_types": []}},
        )
        runner._run_task = MagicMock(return_value=False)
        runner._wait_for_completion = MagicMock(return_value=True)

        result = runner.run()

        self.assertEqual(result["completed"], ["特殊落地页活动"])
        self.assertEqual(result["failed"], [])
        runner._wait_for_completion.assert_called_once_with(
            context.new_page.return_value, card.title
        )


if __name__ == "__main__":
    unittest.main()
