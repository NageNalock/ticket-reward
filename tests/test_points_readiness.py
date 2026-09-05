from __future__ import annotations

import io
import unittest
from unittest.mock import MagicMock, patch

from loguru import logger
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from src.main import POINTS_READ_ATTEMPTS, POINTS_WAIT_MS, _read_points
from src.rewards.panel_parser import PanelParser


class PointsReadinessTests(unittest.TestCase):
    def test_waits_for_delayed_balance_instead_of_returning_unknown(self) -> None:
        parser = PanelParser(MagicMock())
        parser._get_current_points_once = MagicMock(side_effect=[None, None, 1234])

        with patch("src.rewards.panel_parser.monotonic", side_effect=[0, 0, 0.5]):
            self.assertEqual(parser.get_current_points(timeout_ms=2_000), 1234)

        self.assertEqual(parser.page.wait_for_timeout.call_count, 2)

    def test_balance_wait_has_a_deadline(self) -> None:
        parser = PanelParser(MagicMock())
        parser._get_current_points_once = MagicMock(return_value=None)

        with patch("src.rewards.panel_parser.monotonic", side_effect=[0, 0, 0.5, 1]):
            self.assertIsNone(parser.get_current_points(timeout_ms=1_000))

        self.assertEqual(parser._get_current_points_once.call_count, 3)
        self.assertEqual(parser.page.wait_for_timeout.call_count, 2)

    def test_detached_frame_is_retried(self) -> None:
        parser = PanelParser(MagicMock())
        parser._get_current_points_once = MagicMock(
            side_effect=[PlaywrightError("Frame was detached"), 1234]
        )

        with patch("src.rewards.panel_parser.monotonic", side_effect=[0, 0]):
            self.assertEqual(parser.get_current_points(timeout_ms=1_000), 1234)

    def test_zero_is_a_valid_balance_without_extra_waiting(self) -> None:
        parser = PanelParser(MagicMock())
        parser._get_current_points_once = MagicMock(return_value=0)

        self.assertEqual(parser.get_current_points(timeout_ms=1_000), 0)
        parser.page.wait_for_timeout.assert_not_called()

    @patch("src.main.PanelParser")
    def test_reopens_page_when_first_authenticated_visit_has_no_balance(
        self, parser_class: MagicMock
    ) -> None:
        context = MagicMock()
        first, second = MagicMock(), MagicMock()
        context.new_page.side_effect = [first, second]
        parser = parser_class.return_value
        parser.open_panel.return_value = True
        parser.get_current_points.side_effect = [None, 0]

        self.assertEqual(_read_points(context), 0)
        self.assertEqual(context.new_page.call_count, 2)
        first.close.assert_called_once()
        second.close.assert_called_once()
        parser.get_current_points.assert_called_with(timeout_ms=POINTS_WAIT_MS)

    @patch("src.main.PanelParser")
    def test_navigation_error_is_retried_without_logging_private_error_context(
        self, parser_class: MagicMock
    ) -> None:
        context = MagicMock()
        first, second = MagicMock(), MagicMock()
        context.new_page.side_effect = [first, second]
        first.goto.side_effect = PlaywrightTimeoutError("PRIVATE_CONTEXT")
        parser = parser_class.return_value
        parser.open_panel.return_value = True
        parser.get_current_points.return_value = 1234
        output = io.StringIO()
        sink = logger.add(output, format="{message}")
        try:
            self.assertEqual(_read_points(context, phase="任务后"), 1234)
        finally:
            logger.remove(sink)

        self.assertNotIn("PRIVATE_CONTEXT", output.getvalue())
        self.assertIn("任务后积分读取 1/3：加载 Bing 首页失败 (TimeoutError)", output.getvalue())
        first.close.assert_called_once()
        second.close.assert_called_once()

    @patch("src.main.PanelParser")
    def test_panel_not_ready_is_retried_before_reading_balance(
        self, parser_class: MagicMock
    ) -> None:
        context = MagicMock()
        parser = parser_class.return_value
        parser.open_panel.side_effect = [False, True]
        parser.get_current_points.return_value = 1234

        self.assertEqual(_read_points(context), 1234)
        self.assertEqual(context.new_page.call_count, 2)
        parser.get_current_points.assert_called_once()

    @patch("src.main.PanelParser")
    def test_exhausted_retries_return_unknown_and_close_every_page(
        self, parser_class: MagicMock
    ) -> None:
        context = MagicMock()
        pages = [MagicMock() for _ in range(POINTS_READ_ATTEMPTS)]
        context.new_page.side_effect = pages
        parser = parser_class.return_value
        parser.open_panel.return_value = True
        parser.get_current_points.return_value = None

        self.assertIsNone(_read_points(context))
        self.assertEqual(context.new_page.call_count, POINTS_READ_ATTEMPTS)
        for page in pages:
            page.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
