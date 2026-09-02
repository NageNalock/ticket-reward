from __future__ import annotations

import math
import random
import time
from typing import Any


def random_delay(min_sec: float = 2.0, max_sec: float = 6.0) -> None:
    """Sleep for a bounded random interval."""
    if min_sec < 0 or max_sec < min_sec:
        raise ValueError("延迟范围无效")
    time.sleep(random.uniform(min_sec, max_sec))


def human_type(
    locator: Any,
    text: str,
    min_sec: float = 0.05,
    max_sec: float = 0.2,
) -> None:
    """Clear a Playwright locator and type text with per-character jitter."""
    locator.click()
    locator.fill("")
    for char in text:
        locator.press_sequentially(
            char,
            delay=random.uniform(min_sec * 1000, max_sec * 1000),
        )


def smooth_scroll(page: Any, times_min: int = 1, times_max: int = 3) -> None:
    """Scroll in several small steps instead of jumping to the bottom."""
    if times_min < 0 or times_max < times_min:
        raise ValueError("滚动次数范围无效")
    for _ in range(random.randint(times_min, times_max)):
        distance = random.randint(200, 600)
        page.mouse.wheel(0, distance)
        time.sleep(random.uniform(0.4, 1.2))


def move_mouse_to(page: Any, locator: Any) -> None:
    """Move the mouse to the center of a locator along a curved path."""
    box = locator.bounding_box()
    if not box:
        return

    target_x = box["x"] + box["width"] / 2
    target_y = box["y"] + box["height"] / 2
    start_x = random.uniform(10, max(11, target_x / 2))
    start_y = random.uniform(10, max(11, target_y / 2))
    control_x = (start_x + target_x) / 2 + random.uniform(-80, 80)
    control_y = (start_y + target_y) / 2 + random.uniform(-60, 60)

    steps = random.randint(8, 16)
    for index in range(1, steps + 1):
        t = index / steps
        inverse = 1 - t
        x = inverse * inverse * start_x + 2 * inverse * t * control_x + t * t * target_x
        y = inverse * inverse * start_y + 2 * inverse * t * control_y + t * t * target_y
        page.mouse.move(math.floor(x), math.floor(y))
        time.sleep(random.uniform(0.01, 0.04))
