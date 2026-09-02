from __future__ import annotations

from typing import Any

from src.browser.anti_detection import random_delay, smooth_scroll
from src.tasks.task_navigation import close_task, open_task


class KeywordSearchTask:
    def __init__(self, page: Any, task_card: Any):
        self.page = page
        self.task = task_card

    def run(self) -> bool:
        target = open_task(self.page, self.task)
        try:
            result_count = target.page.locator("#b_results").count()
            is_search = "bing.com/search" in target.page.url.lower() or result_count > 0
            if result_count:
                smooth_scroll(target.page, 1, 2)
            random_delay(3, 6)
            return is_search
        finally:
            close_task(target, self.page)
