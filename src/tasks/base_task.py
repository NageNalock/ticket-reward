from __future__ import annotations

import random
import time
from abc import ABC, abstractmethod
from contextlib import suppress
from datetime import datetime
from typing import Any

from loguru import logger

from src.browser.anti_detection import human_type, random_delay, smooth_scroll
from src.utils.storage import project_path


def select_search_terms(terms: list[str], count: int) -> list[str]:
    """Return exactly count terms, reshuffling before a list is reused."""
    cleaned = list(dict.fromkeys(term.strip() for term in terms if term.strip()))
    if count <= 0:
        return []
    if not cleaned:
        raise ValueError("搜索词库为空")

    selected: list[str] = []
    while len(selected) < count:
        batch = cleaned.copy()
        random.shuffle(batch)
        selected.extend(batch[: count - len(selected)])
    return selected


class BaseTask(ABC):
    @abstractmethod
    def run(self) -> Any:
        raise NotImplementedError


class BaseSearchTask(BaseTask):
    device_name = "pc"
    count_key = "pc_count"

    def __init__(self, context: Any, config: dict[str, Any], search_terms: list[str]):
        self.context = context
        self.config = config
        self.search_terms = search_terms

    def run(self) -> dict[str, Any]:
        requested = int(self.config["search"][self.count_key])
        terms = select_search_terms(self.search_terms, requested)
        result: dict[str, Any] = {
            "completed": 0,
            "total": len(terms),
            "failed_terms": [],
        }
        if not terms:
            return result

        page = self.context.new_page()
        try:
            page.goto("https://www.bing.com/", wait_until="domcontentloaded")
            random_delay(1.5, 3.5)
            for index, term in enumerate(terms, start=1):
                try:
                    self._do_search(page, term)
                    result["completed"] += 1
                    logger.info(
                        f"{self.device_name.upper()} 搜索 {index}/{len(terms)} 完成: {term}"
                    )
                except Exception as exc:
                    result["failed_terms"].append(term)
                    logger.warning(f"{self.device_name.upper()} 搜索失败 [{term}]: {exc}")
                    self._capture_failure(page, index)

                if index < len(terms):
                    interval = random.uniform(
                        self.config["search"]["min_interval_sec"],
                        self.config["search"]["max_interval_sec"],
                    )
                    time.sleep(interval)
            return result
        finally:
            page.close()

    def _find_search_box(self, page: Any) -> Any:
        for selector in ("#sb_form_q", "input[name='q']", "textarea[name='q']"):
            locator = page.locator(selector).first
            if locator.count():
                locator.wait_for(state="visible", timeout=10_000)
                return locator
        raise RuntimeError("未找到 Bing 搜索框")

    def _do_search(self, page: Any, term: str) -> None:
        box = self._find_search_box(page)
        anti = self.config["anti_detection"]
        human_type(
            box,
            term,
            float(anti["typing_min_sec"]),
            float(anti["typing_max_sec"]),
        )
        box.press("Enter")
        page.locator("#b_results").wait_for(state="attached", timeout=15_000)
        random_delay(0.8, 2.2)
        smooth_scroll(
            page,
            int(anti["scroll_times_min"]),
            int(anti["scroll_times_max"]),
        )

        if random.random() < float(self.config["search"]["click_result_probability"]):
            self._visit_random_result(page)

    def _visit_random_result(self, page: Any) -> None:
        results = page.locator("#b_results .b_algo h2 a")
        count = min(results.count(), 8)
        if count <= 0:
            return

        result = results.nth(random.randrange(count))
        previous_pages = set(self.context.pages)
        previous_url = page.url
        result.click(timeout=8_000)
        page.wait_for_timeout(1_000)
        new_pages = [
            candidate for candidate in self.context.pages if candidate not in previous_pages
        ]
        target = new_pages[-1] if new_pages else page
        with suppress(Exception):
            target.wait_for_load_state("domcontentloaded", timeout=12_000)
        time.sleep(
            random.uniform(
                self.config["search"]["result_stay_min_sec"],
                self.config["search"]["result_stay_max_sec"],
            )
        )
        if target is not page:
            target.close()
        elif page.url != previous_url:
            page.go_back(wait_until="domcontentloaded", timeout=12_000)
            page.locator("#b_results").wait_for(state="attached", timeout=10_000)

    def _capture_failure(self, page: Any, index: int) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output = project_path(f"data/screenshots/{self.device_name}-search-{stamp}-{index}.png")
        output.parent.mkdir(parents=True, exist_ok=True)
        try:
            page.screenshot(path=str(output), full_page=False)
        except Exception as exc:
            logger.debug(f"搜索失败截图未保存: {exc}")
