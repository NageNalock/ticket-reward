from __future__ import annotations

from collections import Counter
from typing import Any

from loguru import logger

from src.browser.anti_detection import random_delay
from src.rewards.panel_parser import PanelParser, TaskCard, find_task
from src.tasks.keyword_search import KeywordSearchTask
from src.tasks.puzzle import PuzzleTask
from src.tasks.quiz import QuizTask
from src.tasks.visit import VisitTask


class DailyActivitiesTask:
    VERIFY_ATTEMPTS = 4
    VERIFY_DELAY_MS = 2_000

    def __init__(self, context: Any, config: dict[str, Any]):
        self.context = context
        self.config = config

    def run(self) -> dict[str, list[str]]:
        page = self.context.new_page()
        results: dict[str, list[str]] = {
            "completed": [],
            "failed": [],
            "skipped": [],
        }
        try:
            page.goto("https://www.bing.com/", wait_until="domcontentloaded")
            random_delay(1.5, 3)
            parser = PanelParser(page)
            if not parser.open_panel():
                results["failed"].append("Rewards 面板未找到")
                parser.capture_diagnostics("rewards-panel-not-found")
                return results

            tasks = parser.parse_tasks(timeout_ms=8_000)
            if not tasks:
                if parser.daily_set_is_complete():
                    logger.info("今日每日设置已全部完成")
                    results["skipped"].append("每日设置 (已完成)")
                    return results
                results["failed"].append("未解析到奖励活动")
                parser.capture_diagnostics("rewards-activities-empty")
                return results

            daily_count = sum(task.section == "daily" for task in tasks)
            logger.info(
                f"识别到 {len(tasks)} 个奖励活动 "
                f"(每日设置 {daily_count}，额外活动 {len(tasks) - daily_count})"
            )
            title_counts = Counter(task.title for task in tasks)
            for index, original in enumerate(tasks, 1):
                label = original.title
                if title_counts[label] > 1:
                    section = "每日设置" if original.section == "daily" else "额外活动"
                    label = f"{label}（{section}，第 {index} 项）"
                if original.completed:
                    results["skipped"].append(f"{label} (已完成)")
                    continue
                task = self._rebind_task(page, parser, original)
                if task is None:
                    if parser.task_state(original) == "completed":
                        results["skipped"].append(f"{label} (已完成)")
                    else:
                        results["failed"].append(f"{label} (卡片无法唯一匹配)")
                    continue
                if task.completed:
                    results["skipped"].append(f"{label} (已完成)")
                    continue
                if not task.available:
                    results["skipped"].append(f"{label} (未解锁)")
                    continue
                if task.task_type in self.config["daily_activities"].get("skip_types", []):
                    results["skipped"].append(f"{label} (配置跳过)")
                    continue
                if task.task_type not in {"keyword_search", "puzzle", "quiz", "visit"}:
                    results["failed"].append(f"{label} (不支持 {task.task_type})")
                    continue

                try:
                    action_succeeded = self._run_task(page, task)
                    if not action_succeeded:
                        logger.debug(f"活动落地页未识别，继续复核 Rewards 状态: {task.title}")
                except Exception as exc:
                    logger.warning(f"活动动作异常，继续复核 [{task.title}]: {exc}")

                if self._wait_for_completion(page, task):
                    results["completed"].append(label)
                else:
                    results["failed"].append(f"{label} (未确认完成)")
                random_delay(1, 2)
            return results
        finally:
            page.close()

    @staticmethod
    def _rebind_task(page: Any, parser: PanelParser, original: TaskCard) -> TaskCard | None:
        candidates = parser.parse_tasks()
        if not candidates:
            if "bing.com" not in page.url.lower():
                page.goto("https://www.bing.com/", wait_until="domcontentloaded")
            parser.open_panel()
            candidates = parser.parse_tasks()
        return find_task(original, candidates)

    def _wait_for_completion(self, page: Any, task: TaskCard) -> bool:
        """Reload the flyout and trust only Bing's Offer Completed marker."""
        for attempt in range(1, self.VERIFY_ATTEMPTS + 1):
            page.wait_for_timeout(self.VERIFY_DELAY_MS)
            page.goto("https://www.bing.com/", wait_until="domcontentloaded")
            parser = PanelParser(page)
            if not parser.open_panel():
                logger.warning(f"完成校验 {attempt}/{self.VERIFY_ATTEMPTS}: Rewards 面板未找到")
                continue
            state = parser.task_state(task, timeout_ms=8_000)
            if state == "completed":
                logger.info(f"奖励活动已由 Rewards 确认完成: {task.title}")
                return True
            logger.debug(f"完成校验 {attempt}/{self.VERIFY_ATTEMPTS} 状态={state}: {task.title}")
        PanelParser(page).capture_diagnostics("activity-unconfirmed")
        return False

    def _run_task(self, page: Any, task: TaskCard) -> bool:
        logger.info(f"执行奖励活动: {task.title} ({task.task_type}, +{task.points})")
        if task.task_type == "visit":
            return VisitTask(page, task).run()
        if task.task_type == "keyword_search":
            return KeywordSearchTask(page, task).run()
        if task.task_type == "puzzle":
            return PuzzleTask(page, task, self.config).run()
        if task.task_type == "quiz":
            return QuizTask(page, task, self.config).run()
        logger.info(f"暂不支持活动类型: {task.task_type}")
        return False
