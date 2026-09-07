from __future__ import annotations

import unittest
from concurrent.futures import Future
from unittest.mock import MagicMock, patch

from src.ui.app import MenuBarController
from src.utils.login_status import LoginState


class LoginUiTests(unittest.TestCase):
    """Exercise the native controller with offscreen stand-ins; never launch a worker."""

    def setUp(self) -> None:
        self.controller = MenuBarController.alloc().initWithSmokeTest_(True)
        for name in (
            "login_status_label",
            "login_button",
            "login_menu_item",
            "empty_detail",
            "runtime_status",
            "status_item",
            "run_button",
            "run_menu_item",
            "window",
        ):
            setattr(self.controller, name, MagicMock())

    def test_signed_in_state_hides_login_button_and_disables_login_menu(self) -> None:
        self.controller._set_login_state(LoginState.LOGGED_IN)
        self.controller.login_status_label.setStringValue_.assert_called_with("已登录")
        self.controller.login_button.setHidden_.assert_called_with(True)
        self.controller.login_menu_item.setTitle_.assert_called_with("已登录")
        self.controller.login_menu_item.setAction_.assert_called_with(None)
        self.assertIn("账号已登录", self.controller._empty_login_hint())

    def test_runtime_messages_do_not_overwrite_login_state(self) -> None:
        self.controller._set_login_state(LoginState.LOGGED_IN)
        self.controller.login_status_label.reset_mock()
        self.controller._set_runtime_status("最近一次：成功")
        self.controller._set_runtime_status("任务运行中…")
        self.controller.login_status_label.setStringValue_.assert_not_called()
        self.assertEqual(self.controller.login_state, LoginState.LOGGED_IN)

    def test_signed_out_and_expired_states_offer_the_correct_login_action(self) -> None:
        for state, title in ((LoginState.LOGGED_OUT, "登录账号"), (LoginState.EXPIRED, "重新登录")):
            self.controller._set_login_state(state)
            self.controller.login_button.setHidden_.assert_called_with(False)
            self.controller.login_button.setEnabled_.assert_called_with(True)
            self.controller.login_button.setTitle_.assert_called_with(title)
            self.controller.login_button.setAction_.assert_called_with("loginNow:")

    def test_checking_state_does_not_flash_a_login_button(self) -> None:
        self.controller._set_login_state(LoginState.CHECKING)
        self.controller.login_button.setHidden_.assert_called_with(True)

    def test_unavailable_state_offers_a_recheck_instead_of_relogin(self) -> None:
        self.controller._set_login_state(LoginState.UNAVAILABLE)
        self.controller.login_button.setTitle_.assert_called_with("重新检测")
        self.controller.login_button.setAction_.assert_called_with("refreshLoginStatus:")

    def test_background_result_restores_login_and_coalesces_overlapping_checks(self) -> None:
        controller = self.controller
        controller.smoke_test = False
        controller.login_executor = MagicMock()
        result = Future()
        controller.login_executor.submit.return_value = result
        controller._refresh_login_status(force=True)
        controller._refresh_login_status(force=True)
        controller.login_executor.submit.assert_called_once()
        result.set_result(LoginState.LOGGED_IN)
        controller._refresh_login_status()
        self.assertEqual(controller.login_state, LoginState.LOGGED_IN)
        controller.login_button.setHidden_.assert_called_with(True)
        controller._refresh_login_status()
        controller.login_executor.submit.assert_called_once()

    def test_check_failure_is_unknown_and_can_be_retried(self) -> None:
        controller = self.controller
        controller.smoke_test = False
        controller.login_executor = MagicMock()
        controller.login_check = Future()
        controller.login_check.set_exception(OSError("unavailable"))
        controller._refresh_login_status()
        self.assertEqual(controller.login_state, LoginState.UNAVAILABLE)
        controller.refreshLoginStatus_(None)
        self.assertEqual(controller.login_state, LoginState.CHECKING)
        controller.login_executor.submit.assert_called_once()

    def test_reopening_dashboard_requests_fresh_status_without_resetting_known_login(self) -> None:
        self.controller.login_state = LoginState.LOGGED_IN
        with (
            patch.object(MenuBarController, "_refresh_login_status") as refresh,
            patch("src.ui.app.NSApp"),
        ):
            self.controller.showDashboard_(None)
        refresh.assert_called_once_with(force=True)
        self.assertEqual(self.controller.login_state, LoginState.LOGGED_IN)

    def test_login_in_progress_does_not_accept_an_old_background_result(self) -> None:
        controller = self.controller
        controller.smoke_test = False
        controller.process = MagicMock()
        controller.process_kind = "login"
        controller.login_state = LoginState.LOGGING_IN
        controller.login_check = Future()
        controller.login_check.set_result(LoginState.LOGGED_OUT)
        controller._refresh_login_status(force=True)
        self.assertEqual(controller.login_state, LoginState.LOGGING_IN)

    def test_already_logged_in_does_not_start_another_login_worker(self) -> None:
        self.controller.login_state = LoginState.LOGGED_IN
        with patch.object(MenuBarController, "_start_child") as start:
            self.controller.loginNow_(None)
        start.assert_not_called()

    def test_successful_login_updates_button_before_next_background_check(self) -> None:
        controller = self.controller
        controller.process = MagicMock()
        controller.process.poll.return_value = 0
        controller.process_kind = "login"
        controller.login_state = LoginState.LOGGING_IN
        with patch.object(MenuBarController, "refreshHistory_"):
            controller.pollProcess_(None)
        self.assertEqual(controller.login_state, LoginState.LOGGED_IN)
        controller.login_button.setHidden_.assert_called_with(True)

    def test_task_running_prevents_a_second_login_action(self) -> None:
        self.controller.process = MagicMock()
        self.controller._set_login_state(LoginState.LOGGED_OUT)
        self.controller.login_button.setEnabled_.assert_called_with(False)
        self.controller.login_menu_item.setAction_.assert_called_with(None)
