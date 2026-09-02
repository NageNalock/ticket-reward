from __future__ import annotations

import random
import time
from typing import Any

from src.tasks.task_navigation import close_task, open_task


class QuizTask:
    def __init__(self, page: Any, task_card: Any, config: dict[str, Any]):
        self.page = page
        self.task = task_card
        self.timeout = int(config["daily_activities"]["quiz_timeout_sec"])

    def run(self) -> bool:
        target = open_task(self.page, self.task)
        deadline = time.monotonic() + self.timeout
        answered = 0
        try:
            start = target.page.locator(
                "button:has-text('开始'), button:has-text('Start'), button:has-text('Take the quiz')"
            ).first
            if start.count() and start.is_visible():
                start.click()
                target.page.wait_for_timeout(1_000)

            while time.monotonic() < deadline:
                if self._is_complete(target.page):
                    return True
                options = self._visible_options(target.page)
                if not options:
                    return answered > 0
                random.choice(options).click(timeout=5_000)
                answered += 1
                target.page.wait_for_timeout(1_500)

                next_button = target.page.locator(
                    "button:has-text('下一题'), button:has-text('Next'), [class*='next']"
                ).first
                if next_button.count() and next_button.is_visible():
                    next_button.click()
                    target.page.wait_for_timeout(1_000)
            return self._is_complete(target.page)
        finally:
            close_task(target, self.page)

    @staticmethod
    def _visible_options(page: Any) -> list[Any]:
        selectors = (
            "[role='option']",
            "[class*='answer'] button",
            "[class*='option']",
            "button[data-option]",
        )
        for selector in selectors:
            locator = page.locator(selector)
            visible = [
                locator.nth(index)
                for index in range(min(locator.count(), 12))
                if locator.nth(index).is_visible()
            ]
            if visible:
                return visible
        return []

    @staticmethod
    def _is_complete(page: Any) -> bool:
        for text in ("测验完成", "已完成", "Quiz complete", "Congratulations"):
            if page.get_by_text(text, exact=False).count():
                return True
        return False
