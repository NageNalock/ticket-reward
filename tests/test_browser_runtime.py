from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import Mock, patch

from scripts.bundle_browsers import bundle_browsers
from src.browser.browser_manager import BrowserManager


def config() -> dict:
    return {
        "browser": {"user_data_dir": "data/test-profile", "headless": True,
                    "viewport": {"width": 1280, "height": 800}},
        "user_agents": {"pc": "RuntimeTest/Desktop", "mobile": "RuntimeTest/Mobile"},
    }


class BrowserBundleTests(unittest.TestCase):
    def test_bundles_current_revisions_without_old_browsers_or_local_registry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache"
            destination = Path(directory) / "bundled"
            for name in ("chromium-1234", "ffmpeg-1011", "chromium-1000",
                         "ffmpeg-1000", "chromium_headless_shell-1234", ".links"):
                (cache / name).mkdir(parents=True)
                (cache / name / "file").write_text("test runtime")
            executable = cache / "chromium-1234/file"
            executable.chmod(0o755)
            (executable.parent / "link").symlink_to("file")
            output = "\n".join(f"  Install location: {cache / name}\n"
                               for name in ("chromium-1234", "ffmpeg-1011"))
            with patch("scripts.bundle_browsers.subprocess.run", return_value=CompletedProcess(
                [], 0, stdout=output,
            )):
                bundle_browsers(cache, destination)
            self.assertEqual({item.name for item in destination.iterdir()},
                             {"chromium-1234", "ffmpeg-1011"})
            self.assertTrue(os.access(destination / "chromium-1234/file", os.X_OK))
            self.assertEqual((destination / "chromium-1234/link").readlink(), Path("file"))

    def test_refuses_unrecognized_install_plan_without_copying_entire_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("chromium-1234", "chromium_headless_shell-1234"):
                (root / name).mkdir()
            for output in ("unexpected CLI output", f"Install location: {root / 'missing'}",
                           f"Install location: {root / 'chromium_headless_shell-1234'}"):
                with self.subTest(output=output), patch(
                    "scripts.bundle_browsers.subprocess.run",
                    return_value=CompletedProcess([], 0, stdout=output),
                ), self.assertRaises(RuntimeError):
                    bundle_browsers(root, root / "bundled")
                self.assertFalse((root / "bundled").exists())

    def test_visible_login_and_background_modes_use_the_same_browser_channel(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch(
            "src.browser.browser_manager.project_path", return_value=Path(directory),
        ), patch.object(BrowserManager, "_install_stealth"):
            for headless in (False, True):
                manager = BrowserManager(config())
                manager.playwright = Mock()
                manager._headless_override = headless
                manager._launch_context("pc")
                manager.switch_to_mobile()
                manager.switch_to_pc()
                launch = manager.playwright.chromium.launch_persistent_context
                self.assertEqual(launch.call_count, 3)
                for call in launch.call_args_list:
                    self.assertEqual(call.kwargs["channel"], "chromium")
                    self.assertEqual(call.kwargs["headless"], headless)
                self.assertTrue(launch.call_args_list[1].kwargs["is_mobile"])
                manager.stop()


@unittest.skipUnless(os.environ.get("REWARDS_BROWSER_TESTS") == "1", "Opt-in Chromium regression")
class BrowserRuntimeTests(unittest.TestCase):
    def test_new_headless_preserves_session_across_device_switch_and_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory, patch(
            "src.browser.browser_manager.project_path", return_value=Path(directory),
        ):
            manager = BrowserManager(config())
            try:
                context = manager.start(headless=True)
                context.route("**/*", lambda route: route.fulfill(body="<h1>Offline fixture</h1>"))
                page = context.new_page()
                page.goto("https://runtime.test/")
                page.evaluate("localStorage.setItem('session', 'test-session')")
                context.add_cookies([{"name": "test-session", "value": "retained",
                                      "url": "https://runtime.test/", "expires": 4102444800}])
                for launch in (manager.switch_to_mobile, manager.switch_to_pc,
                               lambda: (manager.stop(), manager.start(headless=True))[1]):
                    context = launch()
                    context.route("**/*", lambda route: route.fulfill(body="<h1>Offline fixture</h1>"))
                    page = context.new_page()
                    page.goto("https://runtime.test/")
                    self.assertEqual(page.evaluate("localStorage.getItem('session')"), "test-session")
                    self.assertTrue(any(cookie["name"] == "test-session" and cookie["value"] == "retained"
                                        for cookie in context.cookies()))
            finally:
                manager.stop()
