from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import MagicMock

from src.rewards.panel_parser import (
    PanelParser,
    TaskCard,
    classify_task,
    extract_current_points,
    extract_task_points,
    find_task,
    offer_is_completed,
    task_identity,
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

    def test_recognizes_question_cards_without_the_word_quiz(self) -> None:
        self.assertEqual(classify_task("你是否知道答案？", "通过这些小问题挑战自己"), "quiz")
        self.assertEqual(classify_task("Trivia time"), "quiz")
        self.assertEqual(classify_task("今日挑战", href="/search?q=quiz&quizId=example"), "quiz")

    def test_fixed_reward_referral_is_a_visit_not_an_invitation(self) -> None:
        self.assertEqual(classify_task("将推荐转化为奖励", points=10), "visit")
        self.assertEqual(classify_task("将推荐转化为奖励", "赚取 7,500 积分"), "referral")
        self.assertEqual(classify_task("探索新活动", points=5), "visit")

    def test_rejects_parent_containers_with_many_reward_chips(self) -> None:
        text = "任务一 +15\n任务二 +15\n任务三 +5"
        self.assertFalse(PanelParser._looks_like_task(text))

    def test_rejects_status_messages_as_tasks(self) -> None:
        self.assertFalse(PanelParser._looks_like_task("正在进行中!\n1000"))
        self.assertFalse(PanelParser._looks_like_task("你已获得 15 积分！"))
        self.assertFalse(PanelParser._looks_like_task("You're on track for Star bonus points\n3"))

    @staticmethod
    def card(task_id: str = "pending", **kwargs: object) -> TaskCard:
        return TaskCard(
            title="你是否知道答案？",
            description="挑战自己",
            points=5,
            task_type="quiz",
            element=MagicMock(),
            task_id=task_id,
            **kwargs,
        )

    def test_same_title_completed_card_cannot_complete_pending_offer(self) -> None:
        parser = PanelParser.__new__(PanelParser)
        pending = self.card()
        completed = self.card("completed", completed=True)
        parser.parse_tasks = MagicMock(return_value=[completed, pending])
        parser.daily_set_is_complete = MagicMock(return_value=False)

        self.assertIs(find_task(pending, [completed, pending]), pending)
        self.assertEqual(parser.task_state(pending), "pending")
        self.assertEqual(parser.task_state(completed), "completed")

    def test_missing_offer_is_unknown_even_when_other_offers_remain(self) -> None:
        parser = PanelParser.__new__(PanelParser)
        parser.parse_tasks = MagicMock(return_value=[self.card("different", completed=True)])
        parser.daily_set_is_complete = MagicMock(return_value=False)
        self.assertEqual(parser.task_state(self.card(section="daily")), "unknown")

    def test_daily_set_completion_does_not_complete_extra_offer(self) -> None:
        parser = PanelParser.__new__(PanelParser)
        parser.parse_tasks = MagicMock(return_value=[])
        parser.daily_set_is_complete = MagicMock(return_value=True)
        self.assertEqual(parser.task_state(self.card(section="daily")), "completed")
        self.assertEqual(parser.task_state(self.card(section="extra")), "unknown")

    def test_pending_daily_card_wins_over_aggregate_marker(self) -> None:
        parser = PanelParser.__new__(PanelParser)
        pending = self.card(section="daily")
        parser.parse_tasks = MagicMock(return_value=[pending])
        parser.daily_set_is_complete = MagicMock(return_value=True)
        self.assertEqual(parser.task_state(pending), "pending")

    def test_fallback_requires_unique_content_and_same_section(self) -> None:
        original = self.card("")
        other_section = replace(original, section="daily", completed=True)
        other_description = replace(original, description="另一个问题", completed=True)
        self.assertIs(find_task(original, [other_section, other_description, original]), original)
        self.assertIsNone(find_task(original, [original, replace(original, completed=True)]))

    def test_identifier_match_is_also_required_to_be_unambiguous(self) -> None:
        original = self.card()
        self.assertIsNone(find_task(original, [original, replace(original, completed=True)]))

    def test_ambiguous_daily_cards_do_not_fall_back_to_aggregate_completion(self) -> None:
        parser = PanelParser.__new__(PanelParser)
        original = self.card(section="daily")
        parser.parse_tasks = MagicMock(return_value=[original, replace(original, completed=True)])
        parser.daily_set_is_complete = MagicMock(return_value=True)
        self.assertEqual(parser.task_state(original), "unknown")

    def test_link_identity_preserves_offer_but_ignores_tracking_and_parameter_order(self) -> None:
        first = task_identity("", "", "https://www.bing.com/search?q=quiz&offerid=one&cvid=old")
        second = task_identity("", "", "https://www.bing.com/search?cvid=new&offerid=one&q=quiz")
        different = task_identity("", "", "https://www.bing.com/search?q=quiz&offerid=two")
        self.assertEqual(first, second)
        self.assertNotEqual(first, different)
        self.assertNotIn("offerid", first)
        self.assertEqual(task_identity("", "", "javascript:void(0)"), "")

    def test_recycled_dom_id_does_not_match_a_different_offer_link(self) -> None:
        self.assertNotEqual(
            task_identity("", "card-1", "https://www.bing.com/search?offerid=one"),
            task_identity("", "card-1", "https://www.bing.com/search?offerid=two"),
        )
        self.assertEqual(
            task_identity("stable-offer", "card-1", "/search?cvid=old"),
            task_identity("stable-offer", "card-2", "/search?cvid=new"),
        )


if __name__ == "__main__":
    unittest.main()
