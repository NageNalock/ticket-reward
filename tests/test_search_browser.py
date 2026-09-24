from __future__ import annotations

import os
import unittest
from contextlib import ExitStack
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from playwright.sync_api import sync_playwright

from src.tasks.pc_search import PcSearchTask


@unittest.skipUnless(os.environ.get("REWARDS_BROWSER_TESTS") == "1", "Opt-in Chromium regression")
class SearchBrowserTests(unittest.TestCase):
    """Exercise recovery with isolated pages; every request is intercepted locally."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(channel="chromium", headless=True)
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
        self.home_visits = 0
        self.home_errors = 0
        self.abort_home_once = False
        self.searches: list[str] = []
        self.external_searches: list[str] = []
        self.config = {
            "search": {
                "pc_count": 2, "click_result_probability": 0,
                "min_interval_sec": 0, "max_interval_sec": 0,
            },
            "anti_detection": {
                "typing_min_sec": 0, "typing_max_sec": 0,
                "scroll_times_min": 0, "scroll_times_max": 0,
            },
        }
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        for target in ("random_delay", "time.sleep", "random.shuffle"):
            self.patches.enter_context(patch(f"src.tasks.base_task.{target}"))
        self.scroll = self.patches.enter_context(patch("src.tasks.base_task.smooth_scroll"))
        self.capture = self.patches.enter_context(
            patch("src.tasks.base_task.BaseSearchTask._capture_failure")
        )

    def tearDown(self) -> None:
        self.context.close()

    def route(self, route: object) -> None:
        url = urlsplit(route.request.url)
        query = parse_qs(url.query).get("q", [""])[0]
        form = '<form action="/search"><input id="sb_form_q" name="q"></form>'
        if url.hostname == "example.test":
            if query:
                self.external_searches.append(query)
            html = form
        elif url.hostname == "www.bing.com" and url.path == "/":
            self.home_visits += 1
            if self.abort_home_once:
                self.abort_home_once = False
                route.abort("timedout")
                return
            html = "<h1>Connection timed out</h1>" if self.home_visits <= self.home_errors else form
        elif url.hostname == "www.bing.com" and url.path == "/search":
            self.searches.append(query)
            html = form + '<div id="b_results">Search results</div>'
        else:
            route.abort()
            return
        route.fulfill(status=200, content_type="text/html; charset=utf-8", body=html)

    def runner(self) -> PcSearchTask:
        return PcSearchTask(self.context, self.config, ["first", "second"])

    def assert_completed(self, result: dict) -> None:
        self.assertEqual(result, {"completed": 2, "total": 2, "failed_terms": []})
        self.assertEqual(self.searches, ["first", "second"])
        self.assertEqual(self.context.pages, [])
        self.capture.assert_not_called()

    def test_initial_homepage_timeout_is_retried_before_searching(self) -> None:
        self.abort_home_once = True
        self.assert_completed(self.runner().run())
        self.assertEqual(self.home_visits, 2)

    def test_error_page_between_searches_recovers_without_losing_next_term(self) -> None:
        def error_after_first_search(page: object, *_: object) -> None:
            if len(self.searches) == 1:
                page.set_content("<h1>ERR_CONNECTION_TIMED_OUT</h1>")

        self.scroll.side_effect = error_after_first_search
        self.assert_completed(self.runner().run())
        self.assertEqual(self.home_visits, 2)

    def test_failed_term_is_recorded_once_and_next_term_gets_a_fresh_page(self) -> None:
        self.home_errors = 2
        result = self.runner().run()
        self.assertEqual(result, {"completed": 1, "total": 2, "failed_terms": ["first"]})
        self.assertEqual(self.searches, ["second"])
        self.assertEqual(self.home_visits, 3)
        self.capture.assert_called_once()

    def test_persistent_failure_has_bounded_retries_and_no_false_success(self) -> None:
        self.home_errors = 100
        result = self.runner().run()
        self.assertEqual(result, {"completed": 0, "total": 2, "failed_terms": ["first", "second"]})
        self.assertEqual(self.home_visits, 4)
        self.assertEqual(self.searches, [])
        self.assertEqual(self.capture.call_count, 2)
        self.assertEqual(self.context.pages, [])

    def test_failed_optional_visit_preserves_search_and_never_types_on_external_page(self) -> None:
        self.config["search"]["click_result_probability"] = 1
        runner = self.runner()

        def visit(page: object) -> None:
            page.goto("https://example.test/")
            raise RuntimeError("Unable to return from result page")

        with patch.object(runner, "_visit_random_result", side_effect=visit):
            self.assert_completed(runner.run())
        self.assertEqual(self.home_visits, 2)
        self.assertEqual(self.external_searches, [])


if __name__ == "__main__":
    unittest.main()
