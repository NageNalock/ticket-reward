from __future__ import annotations

import unittest
from unittest.mock import patch

from src.tasks.base_task import select_search_terms


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


if __name__ == "__main__":
    unittest.main()
