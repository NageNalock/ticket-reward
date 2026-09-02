from __future__ import annotations

import argparse
import sys
import time
from typing import Any

from src.browser.browser_manager import BrowserManager
from src.utils.config_loader import load_config
from src.utils.logger import setup_logger
from src.utils.storage import ensure_runtime_dirs, project_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="登录 Microsoft/Bing 账号")
    parser.add_argument("--auto", action="store_true", help="自动等待登录成功，不读取终端输入")
    parser.add_argument("--timeout", type=int, default=600, help="自动等待超时秒数")
    return parser


def main(argv: list[str] | None = None) -> int:
    setup_logger()
    ensure_runtime_dirs()
    args = build_parser().parse_args(argv)
    config = load_config()
    config["browser"]["headless"] = False
    browser = BrowserManager(config)
    browser.start(headless=False)
    page = browser.context.pages[0] if browser.context.pages else browser.new_page()
    try:
        page.goto("https://www.bing.com/", wait_until="domcontentloaded")
        print("请在打开的浏览器中登录 Microsoft 账号。")
        if args.auto:
            print("登录成功后窗口会自动关闭。")
            if wait_for_login(browser, max(10, args.timeout)):
                return _mark_success()
            print("✗ 等待登录超时，请重试。")
            return 1

        print("确认 Bing 顶栏已显示头像或积分后，回到终端按回车。")
        input()
        if browser.is_logged_in(page):
            return _mark_success()
        print("✗ 未检测到登录态，请确认登录完成后重试。")
        return 1
    finally:
        page.close()
        browser.stop()


def _mark_success() -> int:
    project_path("data/login_expired.flag").unlink(missing_ok=True)
    print("✓ 登录成功，登录态已保存。")
    return 0


def wait_for_login(browser: BrowserManager, timeout_sec: int, poll_interval: float = 2.0) -> bool:
    """Poll existing pages and cookies without opening or navigating a tab."""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if browser.has_login_cookie():
            return True
        pages: list[Any] = list(browser.context.pages) if browser.context is not None else []
        for candidate in pages:
            if not candidate.is_closed() and browser.is_logged_in(candidate):
                return True
        time.sleep(poll_interval)
    return False


if __name__ == "__main__":
    sys.exit(main())
