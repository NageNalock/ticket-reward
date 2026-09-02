from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from src.browser.browser_manager import BrowserManager
from src.login import wait_for_login


class LoginFlowTests(unittest.TestCase):
    def test_non_bing_login_page_is_not_navigated_or_replaced(self) -> None:
        manager = BrowserManager.__new__(BrowserManager)
        manager.context = MagicMock()
        manager.context.cookies.return_value = []
        page = MagicMock()
        page.url = "https://login.live.com/oauth20_authorize.srf"

        self.assertFalse(manager.is_logged_in(page))
        manager.context.new_page.assert_not_called()
        page.goto.assert_not_called()
        page.close.assert_not_called()

    def test_wait_loop_only_checks_existing_pages(self) -> None:
        browser = MagicMock()
        page = MagicMock()
        page.is_closed.return_value = False
        browser.context.pages = [page]
        browser.has_login_cookie.return_value = False
        browser.is_logged_in.return_value = False

        with (
            patch("src.login.time.monotonic", side_effect=[0.0, 0.0, 2.0]),
            patch("src.login.time.sleep") as sleep,
        ):
            self.assertFalse(wait_for_login(browser, 1, poll_interval=0.1))

        browser.is_logged_in.assert_called_once_with(page)
        sleep.assert_called_once_with(0.1)

    def test_cookie_completes_login_without_page_changes(self) -> None:
        browser = MagicMock()
        browser.has_login_cookie.return_value = True
        with patch("src.login.time.monotonic", side_effect=[0.0, 0.0]):
            self.assertTrue(wait_for_login(browser, 1))
        browser.is_logged_in.assert_not_called()
