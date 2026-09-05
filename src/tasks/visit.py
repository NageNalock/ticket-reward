from __future__ import annotations

from typing import Any

from src.browser.anti_detection import random_delay
from src.tasks.task_navigation import close_task, open_task


class VisitTask:
    """Open a fixed-reward offer; the caller separately verifies Rewards credit."""

    def __init__(self, page: Any, task_card: Any):
        self.page = page
        self.task = task_card

    def run(self) -> bool:
        target = open_task(self.page, self.task)
        try:
            # Do not interact with referral forms, invite buttons or other CTAs.
            random_delay(3, 6)
            return not target.page.is_closed()
        finally:
            close_task(target, self.page)
