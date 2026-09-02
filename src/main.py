from __future__ import annotations

import argparse
import fcntl
import json
import sys
import time
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any, Iterator

from loguru import logger

from src.browser.browser_manager import BrowserManager
from src.rewards.panel_parser import PanelParser
from src.rewards.points_tracker import PointsTracker
from src.tasks.daily_activities import DailyActivitiesTask
from src.tasks.mobile_search import MobileSearchTask
from src.tasks.pc_search import PcSearchTask
from src.utils.config_loader import ConfigError, load_config
from src.utils.logger import setup_logger
from src.utils.notifier import send_notification
from src.utils.storage import ensure_runtime_dirs, project_path


@contextmanager
def single_instance(lock_path: Path) -> Iterator[None]:
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("已有一个 Bing Rewards 任务正在运行") from exc
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bing Rewards 自动任务")
    parser.add_argument("--mode", choices=("headed", "headless"), default=None)
    parser.add_argument("--pc-count", type=int, default=None)
    parser.add_argument("--mobile-count", type=int, default=None)
    parser.add_argument("--skip-daily", action="store_true")
    parser.add_argument("--skip-pc", action="store_true")
    parser.add_argument("--skip-mobile", action="store_true")
    parser.add_argument("--check-login", action="store_true")
    parser.add_argument("--trigger", choices=("manual", "ui", "scheduled"), default="manual")
    return parser


def _load_search_terms() -> list[str]:
    path = project_path("config/search_terms.json")
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    terms = data.get("hot", []) + data.get("general", [])
    if not terms:
        raise ValueError(f"搜索词库为空: {path}")
    return list(dict.fromkeys(str(term).strip() for term in terms if str(term).strip()))


def _read_points(context: Any) -> int | None:
    page = context.new_page()
    try:
        page.goto("https://www.bing.com/", wait_until="domcontentloaded")
        page.wait_for_timeout(1_000)
        parser = PanelParser(page)
        if parser.open_panel():
            return parser.get_current_points()
        return None
    finally:
        page.close()


def _apply_cli_overrides(config: dict[str, Any], args: argparse.Namespace) -> None:
    if args.mode is not None:
        config["browser"]["headless"] = args.mode == "headless"
    if args.pc_count is not None:
        if args.pc_count < 0:
            raise ConfigError("--pc-count 不能为负数")
        config["search"]["pc_count"] = args.pc_count
    if args.mobile_count is not None:
        if args.mobile_count < 0:
            raise ConfigError("--mobile-count 不能为负数")
        config["search"]["mobile_count"] = args.mobile_count


def run(args: argparse.Namespace) -> int:
    ensure_runtime_dirs()
    config = load_config()
    _apply_cli_overrides(config, args)
    terms = _load_search_terms()
    login_flag = project_path("data/login_expired.flag")
    tracker = PointsTracker()
    browser = BrowserManager(config)
    started_at = time.monotonic()
    completed_tasks: list[str] = []
    failed_tasks: list[str] = []

    logger.info("=== Bing Rewards 自动任务开始 ===")
    try:
        browser.start()
        if not browser.is_logged_in():
            login_flag.touch()
            logger.error("未检测到登录态，请先运行 .venv/bin/python scripts/login.py")
            tracker.record_run(
                None,
                None,
                [],
                ["login_expired"],
                time.monotonic() - started_at,
                trigger=args.trigger,
            )
            send_notification("Bing Rewards", "登录态已过期，请重新登录")
            return 2
        login_flag.unlink(missing_ok=True)
        if args.check_login:
            logger.info("Microsoft 账号登录态有效")
            return 0

        start_points = _read_points(browser.context)
        logger.info(f"任务前积分: {start_points if start_points is not None else '未读取到'}")

        daily_enabled = config["daily_activities"]["enabled"] and not args.skip_daily
        if daily_enabled:
            try:
                daily = DailyActivitiesTask(browser.context, config).run()
                completed_tasks.extend(daily["completed"])
                failed_tasks.extend(daily["failed"])
                if daily["skipped"]:
                    logger.info(f"已跳过活动: {daily['skipped']}")
            except Exception as exc:
                logger.exception(f"每日活动阶段失败: {exc}")
                failed_tasks.append("daily_activities")

        if not args.skip_pc and config["search"]["pc_count"] > 0:
            try:
                result = PcSearchTask(browser.context, config, terms).run()
                completed_tasks.append(f"pc_search({result['completed']}/{result['total']})")
                failed_tasks.extend(f"pc_search:{term}" for term in result["failed_terms"])
            except Exception as exc:
                logger.exception(f"PC 搜索阶段失败: {exc}")
                failed_tasks.append("pc_search")

        if not args.skip_mobile and config["search"]["mobile_count"] > 0:
            try:
                browser.switch_to_mobile()
                result = MobileSearchTask(browser.context, config, terms).run()
                completed_tasks.append(f"mobile_search({result['completed']}/{result['total']})")
                failed_tasks.extend(f"mobile_search:{term}" for term in result["failed_terms"])
            except Exception as exc:
                logger.exception(f"移动搜索阶段失败: {exc}")
                failed_tasks.append("mobile_search")

        if browser.mode != "pc":
            browser.switch_to_pc()
        end_points = _read_points(browser.context)
        if start_points is None or end_points is None:
            failed_tasks.append("points_unverified")
        duration = time.monotonic() - started_at
        record = tracker.record_run(
            start_points,
            end_points,
            completed_tasks,
            failed_tasks,
            duration,
            trigger=args.trigger,
        )
        earned = record["earned"]
        earned_text = str(earned) if earned is not None else "未知"
        logger.info(
            "=== 任务完成 ===\n"
            f"积分: {start_points} → {end_points} (获得 {earned_text} 分)\n"
            f"完成: {completed_tasks}\n"
            f"失败: {failed_tasks}\n"
            f"耗时: {duration:.0f} 秒"
        )
        send_notification("Bing Rewards 任务完成", f"本次获得 {earned_text} 积分")
        return 0 if not failed_tasks else 1
    finally:
        browser.stop()


def main(argv: list[str] | None = None) -> int:
    setup_logger()
    args = build_parser().parse_args(argv)
    started_at = time.monotonic()
    try:
        with single_instance(project_path("data/run.lock")):
            return run(args)
    except (ConfigError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
        logger.error(str(exc))
        return 2
    except KeyboardInterrupt:
        logger.warning("用户中止任务")
        return 130
    except Exception as exc:
        logger.exception(f"未处理的运行错误: {exc}")
        with suppress(Exception):
            PointsTracker().record_run(
                None,
                None,
                [],
                [f"runtime_error:{type(exc).__name__}"],
                time.monotonic() - started_at,
                trigger=args.trigger,
            )
        return 1


if __name__ == "__main__":
    sys.exit(main())
