from __future__ import annotations

from typing import Any

from loguru import logger

from src.browser.anti_detection import random_delay
from src.rewards.panel_parser import PanelParser, TaskCard
from src.tasks.keyword_search import KeywordSearchTask
from src.tasks.puzzle import PuzzleTask
from src.tasks.quiz import QuizTask


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

            tasks = parser.parse_daily_tasks()
            if not tasks:
                if parser.daily_set_is_complete():
                    logger.info("今日每日设置已全部完成")
                    results["skipped"].append("每日设置 (已完成)")
                    return results
                results["failed"].append("未解析到每日设置")
                parser.capture_diagnostics("rewards-daily-set-empty")
                return results

            logger.info(f"识别到 {len(tasks)} 个每日设置任务")
            for original in tasks:
                task = self._rebind_task(page, parser, original)
                if task is None:
                    results["failed"].append(f"{original.title} (卡片已失效)")
                    continue
                if task.completed:
                    results["skipped"].append(f"{task.title} (已完成)")
                    continue
                if not task.available:
                    results["skipped"].append(f"{task.title} (未解锁)")
                    continue
                if task.task_type in self.config["daily_activities"]["skip_types"]:
                    results["failed"].append(f"{task.title} (配置跳过，未完成)")
                    continue
                if task.task_type not in {"keyword_search", "puzzle", "quiz"}:
                    results["failed"].append(f"{task.title} (不支持 {task.task_type})")
                    continue

                try:
                    action_succeeded = self._run_task(page, task)
                    if not action_succeeded:
                        logger.debug(f"活动落地页未识别，继续复核 Rewards 状态: {task.title}")
                except Exception as exc:
                    logger.warning(f"活动动作异常，继续复核 [{task.title}]: {exc}")

                if self._wait_for_completion(page, task.title):
                    results["completed"].append(task.title)
                else:
                    results["failed"].append(f"{task.title} (未确认完成)")
                random_delay(1, 2)
            return results
        finally:
            page.close()

    @staticmethod
    def _rebind_task(page: Any, parser: PanelParser, original: TaskCard) -> TaskCard | None:
        candidates = parser.parse_daily_tasks()
        if not candidates:
            if "bing.com" not in page.url.lower():
                page.goto("https://www.bing.com/", wait_until="domcontentloaded")
            parser.open_panel()
            candidates = parser.parse_daily_tasks()
        for candidate in candidates:
            if candidate.title == original.title:
                return candidate
        return None

    def _wait_for_completion(self, page: Any, title: str) -> bool:
        """Reload the flyout and trust only Bing's Offer Completed marker."""
        for attempt in range(1, self.VERIFY_ATTEMPTS + 1):
            page.wait_for_timeout(self.VERIFY_DELAY_MS)
            page.goto("https://www.bing.com/", wait_until="domcontentloaded")
            parser = PanelParser(page)
            if not parser.open_panel():
                logger.warning(f"完成校验 {attempt}/{self.VERIFY_ATTEMPTS}: Rewards 面板未找到")
                continue
            state = parser.daily_task_state(title)
            if state == "completed":
                logger.info(f"每日任务已由 Rewards 确认完成: {title}")
                return True
            logger.debug(f"完成校验 {attempt}/{self.VERIFY_ATTEMPTS} 状态={state}: {title}")
        PanelParser(page).capture_diagnostics("daily-task-unconfirmed")
        return False

    def _run_task(self, page: Any, task: TaskCard) -> bool:
        logger.info(f"执行每日活动: {task.title} ({task.task_type}, +{task.points})")
        if task.task_type == "keyword_search":
            return KeywordSearchTask(page, task).run()
        if task.task_type == "puzzle":
            return PuzzleTask(page, task, self.config).run()
        if task.task_type == "quiz":
            return QuizTask(page, task, self.config).run()
        logger.info(f"暂不支持活动类型: {task.task_type}")
        return False
