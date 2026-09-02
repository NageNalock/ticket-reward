from __future__ import annotations

import random
import re
import time
from typing import Any

from src.tasks.task_navigation import close_task, open_task


class PuzzleTask:
    SUCCESS_TEXT = re.compile(
        r"^\s*(?:已完成|拼图(?:已)?完成|成功|恭喜(?:[，,!！ ].{0,50})?|"
        r"completed|congratulations(?:[,! ].{0,50})?|you did it)\s*[.!！。]?\s*$",
        re.IGNORECASE,
    )

    def __init__(self, page: Any, task_card: Any, config: dict[str, Any]):
        self.page = page
        self.task = task_card
        self.timeout = int(config["daily_activities"]["puzzle_timeout_sec"])

    def run(self) -> bool:
        target = open_task(self.page, self.task)
        deadline = time.monotonic() + self.timeout
        try:
            start = target.page.locator(
                "button:has-text('开始'), button:has-text('Start'), [class*='start']"
            ).first
            if start.count() and start.is_visible():
                start.click()
                target.page.wait_for_timeout(1_000)

            while time.monotonic() < deadline:
                if self._is_complete(target.page):
                    return True
                tiles = self._visible_tiles(target.page)
                if not tiles:
                    target.page.wait_for_timeout(1_000)
                    continue
                random.choice(tiles).click(timeout=3_000)
                target.page.wait_for_timeout(random.randint(250, 700))
            return self._is_complete(target.page)
        finally:
            close_task(target, self.page)

    def _visible_tiles(self, page: Any) -> list[Any]:
        selectors = (
            "[class*='puzzle'] [class*='tile']",
            "[class*='puzzle'] [class*='piece']",
            "[role='grid'] button",
            "[class*='slide'] button",
        )
        for selector in selectors:
            locator = page.locator(selector)
            visible = [
                locator.nth(index)
                for index in range(min(locator.count(), 25))
                if locator.nth(index).is_visible()
            ]
            if visible:
                return visible
        return []

    def _is_complete(self, page: Any) -> bool:
        return bool(page.get_by_text(self.SUCCESS_TEXT).count())
