from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from time import monotonic
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright

from src.rewards.panel_parser import PanelParser, find_task
from src.tasks.daily_activities import DailyActivitiesTask

FIXTURE = Path(__file__).parent / "fixtures/rewards_panel.html"


@unittest.skipUnless(os.environ.get("REWARDS_BROWSER_TESTS") == "1", "Opt-in Chromium regression")
class ActivityBrowserTests(unittest.TestCase):
    """Run real DOM/navigation against isolated, fully routed pages without a login."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except Exception:
            cls.playwright.stop()
            raise

    @classmethod
    def tearDownClass(cls) -> None:
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self) -> None:
        self.context = self.browser.new_context()
        self.context.route("**/*", self.route)
        self.page = self.context.new_page()
        self.completed: set[str] = set()
        self.visited: list[str] = []
        self.invites = 0
        self.daily_complete = False
        self.credit_visits = True

    def tearDown(self) -> None:
        self.context.close()

    def panel_html(self) -> str:
        return (
            FIXTURE.read_text()
            .replace("__COMPLETED_OFFERS__", json.dumps(sorted(self.completed)))
            .replace("__DAILY_COMPLETE__", json.dumps(self.daily_complete))
        )

    def route(self, route: object) -> None:
        url = urlsplit(route.request.url)
        offer = parse_qs(url.query).get("offer", [""])[0]
        if url.path == "/":
            html = """<button id="id_rh" onclick="document.querySelector('iframe').style.display='block'">
                Microsoft Rewards</button>
                <iframe src="https://rewards.bing.com/test-panel"
                    style="display:none;width:720px;height:800px"></iframe>"""
        elif url.path == "/test-panel":
            html = self.panel_html()
        elif url.path == "/test-visit":
            self.visited.append(offer)
            if self.credit_visits:
                self.completed.add(offer)
            html = (
                """<h1>推荐活动介绍</h1><button onclick="fetch('/test-invite')">发送邀请</button>"""
            )
        elif url.path == "/test-quiz":
            self.visited.append(offer)
            html = f"""<h1>测试问题</h1><button role="option" onclick="
                fetch('/test-credit?offer={offer}'); document.body.textContent='Quiz complete';">
                测试答案</button>"""
        elif url.path == "/test-credit":
            self.completed.add(offer)
            html = "ok"
        elif url.path == "/test-invite":
            self.invites += 1
            html = "unexpected invitation"
        else:
            route.abort()
            return
        route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)

    def test_hidden_preloaded_flyout_is_opened_before_scanning_all_seven_cards(self) -> None:
        self.page.goto("https://www.bing.com/")
        parser = PanelParser(self.page)
        self.assertTrue(parser.open_panel())
        cards = parser.parse_tasks()
        self.assertEqual(len(cards), 7)
        self.assertEqual(sum(card.section == "daily" for card in cards), 3)
        questions = [card for card in cards if card.title == "你是否知道答案？"]
        self.assertEqual(len(questions), 2)
        self.assertNotEqual(questions[0].task_id, questions[1].task_id)
        self.assertEqual([card.completed for card in questions], [True, False])
        self.assertTrue(all(card.task_type == "quiz" and card.points == 5 for card in questions))
        referrals = [card for card in cards if card.title == "将推荐转化为奖励"]
        self.assertEqual(
            [(card.task_type, card.points) for card in referrals], [("visit", 10), ("referral", 0)]
        )
        self.assertFalse(cards[-1].available)

    def test_parent_links_identify_same_title_cards_after_reordering(self) -> None:
        self.page.set_content("""
          <a href="https://www.bing.com/search?q=quiz&amp;offerid=one&amp;cvid=old">
            <div class="promo_cont" aria-label="Offer not Completed">
              <span class="promo-title">你是否知道答案？</span><span class="point_cont">+5</span>
            </div></a>
          <a href="https://www.bing.com/search?q=quiz&amp;offerid=two">
            <div class="promo_cont" aria-label="Offer Completed">
              <span class="promo-title">你是否知道答案？</span><span class="point_cont">✓5</span>
            </div></a>""")
        parser = PanelParser(self.page)
        original = parser.parse_tasks()[0]
        self.page.evaluate("""() => {
            const first = document.querySelector('a');
            first.href = first.href.replace('cvid=old', 'cvid=new');
            document.body.append(first);
        }""")
        candidates = parser.parse_tasks()
        self.assertEqual(len(candidates), 2)
        self.assertEqual(parser.task_state(original), "pending")
        self.assertEqual(find_task(original, candidates).task_id, original.task_id)

    def test_nested_link_metadata_does_not_duplicate_or_strip_card(self) -> None:
        self.page.set_content("""<div class="promo_cont" aria-label="Offer not Completed">
          <a data-offer-id="nested" href="https://www.bing.com/search?q=example">
            <span class="promo-title">今日探索</span></a>
          <span class="point_cont">+10</span></div>""")
        cards = PanelParser(self.page).parse_tasks()
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].points, 10)
        self.assertTrue(cards[0].task_id)

    def test_late_extra_cards_are_included_before_inventory_is_accepted(self) -> None:
        self.page.set_content("""<div id="daily_set_card">
          <a class="promo_cont" data-offer-id="first" href="/search?q=first">
            <span class="promo-title">每日活动</span><span class="point_cont">+10</span>
          </a></div><script>setTimeout(() => {
            document.body.insertAdjacentHTML('beforeend', `
              <a class="promo_cont" data-offer-id="late" href="/test-quiz">
                <span class="promo-title">你是否知道答案？</span>
                <span class="point_cont">+5</span></a>`);
          }, 600);</script>""")
        cards = PanelParser(self.page).parse_tasks(timeout_ms=4_000)
        self.assertEqual([card.section for card in cards], ["daily", "extra"])

    def test_slow_balance_can_use_its_full_wait_after_flyout_opens(self) -> None:
        self.context.route(
            "**/test-late-rewards",
            lambda route: route.fulfill(
                content_type="text/html; charset=utf-8",
                body="""<p>加载中</p><script>
              setTimeout(() => { document.body.innerHTML =
                '<section>1,234<span>我的积分</span></section>'; }, 7000);
            </script>""",
            ),
        )
        self.page.set_content('<iframe src="https://rewards.bing.com/test-late-rewards"></iframe>')
        parser = PanelParser(self.page)
        start = monotonic()
        self.assertTrue(parser.open_panel())
        self.assertLess(monotonic() - start, 3)
        self.assertEqual(parser.get_current_points(timeout_ms=8_000), 1234)

    def test_description_reward_claim_is_not_a_fixed_offer_chip(self) -> None:
        self.page.set_content("""<a class="promo_cont" href="/test-invite">
          <span class="promo-title">将推荐转化为奖励</span>
          <span class="promo-desc">邀请朋友可获得 +7500 积分</span></a>""")
        card = PanelParser(self.page).parse_tasks()[0]
        self.assertEqual(card.points, 0)
        self.assertEqual(card.task_type, "referral")

    def test_explicit_pending_state_wins_over_stale_checkmark(self) -> None:
        self.page.set_content("""<div class="promo_cont completed" aria-label="Offer not Completed">
          <span class="promo-title">你是否知道答案？</span>
          <span class="point_cont"><span class="checkmark">✓5</span></span></div>""")
        self.assertFalse(PanelParser(self.page).parse_tasks()[0].completed)

    @patch("src.tasks.daily_activities.random_delay")
    @patch("src.tasks.visit.random_delay")
    def test_runner_completes_daily_visit_and_extra_quiz_without_sending_invites(
        self, *_: object
    ) -> None:
        runner = self.runner()
        result = runner.run()
        self.assertEqual(self.visited, ["daily-referral", "quiz-new"])
        self.assertEqual(self.completed, {"daily-referral", "quiz-new"})
        self.assertEqual(len(result["completed"]), 2)
        self.assertEqual(len(result["skipped"]), 5)
        self.assertEqual(result["failed"], [])
        self.assertEqual(self.invites, 0)
        self.assertEqual(self.context.pages, [self.page])

    @patch("src.tasks.daily_activities.random_delay")
    def test_completed_daily_set_still_runs_extra_quiz(self, *_: object) -> None:
        self.daily_complete = True
        result = self.runner().run()
        self.assertEqual(self.visited, ["quiz-new"])
        self.assertEqual(len(result["completed"]), 1)
        self.assertEqual(result["failed"], [])
        self.assertEqual(self.invites, 0)

    @patch("src.rewards.panel_parser.PanelParser.capture_diagnostics")
    @patch("src.tasks.daily_activities.random_delay")
    @patch("src.tasks.visit.random_delay")
    def test_visited_offer_without_credit_remains_failed(self, *_: object) -> None:
        self.credit_visits = False
        self.completed.add("quiz-new")
        result = self.runner().run()
        self.assertEqual(self.visited, ["daily-referral"])
        self.assertEqual(result["completed"], [])
        self.assertEqual(len(result["failed"]), 1)
        self.assertIn("未确认完成", result["failed"][0])
        self.assertEqual(self.invites, 0)

    def runner(self) -> DailyActivitiesTask:
        runner = DailyActivitiesTask(
            self.context,
            {
                "daily_activities": {
                    "skip_types": ["referral"],
                    "quiz_timeout_sec": 5,
                    "puzzle_timeout_sec": 5,
                }
            },
        )
        runner.VERIFY_DELAY_MS = 0
        runner.VERIFY_ATTEMPTS = 2
        return runner
