from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from src.tasks.base_task import select_search_terms
from src.tasks.pc_search import PcSearchTask


class SearchTermTests(unittest.TestCase):
    def test_returns_requested_count(self) -> None:
        with patch(
            "src.tasks.base_task.random.shuffle", side_effect=lambda values: values.reverse()
        ):
            selected = select_search_terms(["a", "b"], 5)
        self.assertEqual(len(selected), 5)
        self.assertTrue(set(selected).issubset({"a", "b"}))

    def test_deduplicates_and_ignores_blanks(self) -> None:
        selected = select_search_terms(["a", " ", "a", "b"], 2)
        self.assertEqual(set(selected), {"a", "b"})

    def test_empty_terms_fail_for_positive_count(self) -> None:
        with self.assertRaises(ValueError):
            select_search_terms([], 1)


class SearchVisitTests(unittest.TestCase):
    def test_failed_result_visit_closes_its_popup_without_closing_existing_pages(self) -> None:
        context, page, existing, popup = MagicMock(), MagicMock(), MagicMock(), MagicMock()
        context.pages = [page, existing]
        page.locator.return_value.count.return_value = 1
        result = page.locator.return_value.nth.return_value
        result.click.side_effect = lambda **_: setattr(context, "pages", [page, existing, popup])
        page.wait_for_timeout.side_effect = RuntimeError("Result navigation failed")
        page.url = "https://www.bing.com/search?q=example"
        page.is_closed.return_value = False
        runner = PcSearchTask(context, {}, [])

        with self.assertRaisesRegex(RuntimeError, "Result navigation failed"):
            runner._visit_random_result(page)

        popup.close.assert_called_once()
        existing.close.assert_not_called()
        page.close.assert_not_called()


if __name__ == "__main__":
    unittest.main()
