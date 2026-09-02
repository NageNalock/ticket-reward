from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from typing import Any


@dataclass
class TaskTarget:
    page: Any
    opened_new_page: bool
    origin_url: str


def open_task(origin_page: Any, task: Any, settle_ms: int = 2_000) -> TaskTarget:
    context = origin_page.context
    previous_pages = set(context.pages)
    origin_url = origin_page.url
    task.element.click(timeout=10_000)
    origin_page.wait_for_timeout(settle_ms)
    new_pages = [candidate for candidate in context.pages if candidate not in previous_pages]
    target_page = new_pages[-1] if new_pages else origin_page
    with suppress(Exception):
        target_page.wait_for_load_state("domcontentloaded", timeout=15_000)
    return TaskTarget(target_page, target_page is not origin_page, origin_url)


def close_task(target: TaskTarget, origin_page: Any) -> None:
    if target.opened_new_page:
        if not target.page.is_closed():
            target.page.close()
        return

    if origin_page.url != target.origin_url:
        try:
            origin_page.go_back(wait_until="domcontentloaded", timeout=12_000)
        except Exception:
            origin_page.goto("https://www.bing.com/", wait_until="domcontentloaded")
