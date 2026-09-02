from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any, Literal

from loguru import logger
from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

from src.utils.storage import project_path

DeviceMode = Literal["pc", "mobile"]


class BrowserManager:
    """Own a Playwright process and one persistent Chromium context."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.playwright: Playwright | None = None
        self.context: BrowserContext | None = None
        self.mode: DeviceMode | None = None
        self._headless_override: bool | None = None
        self.user_data_dir = project_path(config["browser"]["user_data_dir"])
        self.user_data_dir.mkdir(parents=True, exist_ok=True)

    def start(self, headless: bool | None = None) -> BrowserContext:
        if getattr(sys, "frozen", False):
            contents = Path(sys.executable).resolve().parents[1]
            bundled_browsers = contents / "Resources/ms-playwright"
            os.environ.setdefault(
                "PLAYWRIGHT_BROWSERS_PATH",
                str(bundled_browsers),
            )
        if self.playwright is None:
            self.playwright = sync_playwright().start()
        self._headless_override = headless
        return self._launch_context("pc")

    def _launch_context(self, mode: DeviceMode) -> BrowserContext:
        if self.playwright is None:
            raise RuntimeError("BrowserManager.start() 尚未调用")
        if self.context is not None:
            self.context.close()
            self.context = None

        browser_config = self.config["browser"]
        headless = (
            self._headless_override
            if self._headless_override is not None
            else bool(browser_config["headless"])
        )
        is_mobile = mode == "mobile"
        viewport = {"width": 390, "height": 844} if is_mobile else dict(browser_config["viewport"])

        logger.debug(f"启动 {mode} 浏览器上下文 (headless={headless})")
        self.context = self.playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.user_data_dir),
            headless=headless,
            viewport=viewport,
            user_agent=self.config["user_agents"][mode],
            is_mobile=is_mobile,
            has_touch=is_mobile,
            device_scale_factor=3 if is_mobile else 1,
            locale=browser_config.get("locale", "zh-CN"),
            timezone_id=browser_config.get("timezone_id", "Asia/Shanghai"),
            args=["--disable-blink-features=AutomationControlled"],
        )
        timeout = int(browser_config.get("navigation_timeout_ms", 30_000))
        self.context.set_default_timeout(timeout)
        self.context.set_default_navigation_timeout(timeout)
        self._install_stealth(self.context)
        self.mode = mode
        return self.context

    @staticmethod
    def _install_stealth(context: BrowserContext) -> None:
        """Install playwright-stealth v2 with a small built-in fallback."""
        try:
            from playwright_stealth import Stealth

            stealth = Stealth()
            payload = stealth.script_payload
            context.add_init_script(script=payload)
            logger.debug("playwright-stealth 已启用")
        except Exception as exc:  # package/API differences should not stop login
            logger.warning(f"playwright-stealth 初始化失败，使用基础脚本: {exc}")
            context.add_init_script(
                script="Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )

    def switch_to_mobile(self) -> BrowserContext:
        return self._launch_context("mobile")

    def switch_to_pc(self) -> BrowserContext:
        return self._launch_context("pc")

    def new_page(self) -> Page:
        if self.context is None:
            raise RuntimeError("浏览器尚未启动")
        return self.context.new_page()

    def has_login_cookie(self) -> bool:
        """Check Bing's authenticated session cookie without touching any page."""
        if self.context is None:
            raise RuntimeError("浏览器尚未启动")
        try:
            return any(
                cookie.get("name") == "_U"
                and bool(cookie.get("value"))
                and "bing.com" in str(cookie.get("domain", ""))
                for cookie in self.context.cookies()
            )
        except Exception as exc:
            logger.debug(f"读取 Bing 登录 Cookie 失败: {exc}")
            return False

    def is_logged_in(self, page: Page | None = None) -> bool:
        """Check login state, reusing page when supplied and never navigating it."""
        if self.context is None:
            raise RuntimeError("浏览器尚未启动")
        if self.has_login_cookie():
            return True

        owns_page = page is None
        target = page if page is not None else self.context.new_page()
        try:
            if owns_page:
                target.goto("https://www.bing.com/", wait_until="domcontentloaded")
                target.wait_for_timeout(1_500)
            elif "bing.com" not in target.url.lower():
                return False

            sign_in = target.locator(
                "#id_l, a:has-text('Sign in'), a:has-text('登录'), a:has-text('登入')"
            ).first
            if sign_in.count() and sign_in.is_visible():
                return False

            account_selectors = (
                "#id_n",
                "#mectrl_headerPicture",
                "[aria-label*='Account manager' i]",
            )
            for selector in account_selectors:
                locator = target.locator(selector).first
                if (
                    locator.count()
                    and locator.is_visible()
                    and (selector != "#id_n" or locator.inner_text().strip())
                ):
                    return True

            rewards = target.locator(
                "#id_rh, [aria-label*='Microsoft Rewards' i], [title*='Microsoft Rewards' i]"
            ).first
            if rewards.count():
                text = " ".join(
                    filter(
                        None,
                        [
                            rewards.inner_text(timeout=2_000),
                            rewards.get_attribute("aria-label") or "",
                            rewards.get_attribute("title") or "",
                        ],
                    )
                )
                if re.search(r"\b\d[\d,\.]*\b", text):
                    return True

            logger.warning("页面已打开，但没有找到可确认登录态的账号或积分元素")
            return False
        except Exception as exc:
            logger.warning(f"登录态检测失败: {exc}")
            return False
        finally:
            if owns_page:
                target.close()

    def close(self) -> None:
        if self.context is not None:
            self.context.close()
            self.context = None
            self.mode = None

    def stop(self) -> None:
        self.close()
        if self.playwright is not None:
            self.playwright.stop()
            self.playwright = None

    def __enter__(self) -> "BrowserManager":
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()
