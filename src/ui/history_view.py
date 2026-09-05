from __future__ import annotations

import re
from typing import Any

STATUS_LABELS = {
    "success": "成功",
    "partial": "部分失败",
    "failed": "失败",
}
TRIGGER_LABELS = {
    "manual": "命令行",
    "ui": "菜单栏",
    "scheduled": "定时",
    "legacy": "旧记录",
}


def format_history_row(record: dict[str, Any]) -> dict[str, str]:
    timestamp = str(record.get("timestamp") or "—").replace("T", " ")
    if len(timestamp) >= 16:
        timestamp = timestamp[:16]
    completed = record.get("tasks_completed") or []
    failed = record.get("tasks_failed") or []
    earned = record.get("earned")
    duration = record.get("duration_sec")
    return {
        "time": timestamp,
        "status": STATUS_LABELS.get(str(record.get("status")), "未知"),
        "trigger": TRIGGER_LABELS.get(
            str(record.get("trigger")), str(record.get("trigger") or "—")
        ),
        "earned": f"{earned:+d}" if isinstance(earned, int) else "—",
        "completed": _task_summary(completed),
        "skipped": _task_summary(record.get("tasks_skipped") or []),
        "failed": _task_summary(failed),
        "duration": f"{float(duration):.0f}s" if isinstance(duration, (int, float)) else "—",
    }


def format_summary(summary: dict[str, int]) -> list[str]:
    return [
        f"运行次数\n{summary.get('total', 0)}",
        f"成功\n{summary.get('success', 0)}",
        f"部分失败\n{summary.get('partial', 0)}",
        f"失败\n{summary.get('failed', 0)}",
        f"累计积分\n{summary.get('earned', 0):+d}",
    ]


def extract_live_progress(log_text: str) -> str:
    """Return the newest user-facing phase from the current worker log chunk."""
    progress = ""
    for raw_line in log_text.splitlines():
        line = raw_line.rsplit("|", 1)[-1].strip()
        match = re.search(r"(任务前|任务后)积分读取 (\d+)/(\d+)", line)
        if match:
            progress = f"{match.group(1)}积分：正在读取 {match.group(2)}/{match.group(3)}"
            continue
        match = re.search(r"PC 搜索\s+(\d+)/(\d+)\s+完成", line, re.IGNORECASE)
        if match:
            progress = f"PC 搜索 {match.group(1)}/{match.group(2)}"
            continue
        match = re.search(r"MOBILE 搜索\s+(\d+)/(\d+)\s+完成", line, re.IGNORECASE)
        if match:
            progress = f"移动搜索 {match.group(1)}/{match.group(2)}"
            continue
        match = re.search(r"识别到\s*(\d+)\s*个每日设置任务", line)
        if match:
            progress = f"每日设置：发现 {match.group(1)} 项"
            continue
        match = re.search(r"执行每日活动:\s*(.+?)\s*\(", line)
        if match:
            progress = f"每日任务：{match.group(1)}"
            continue
        match = re.search(r"每日任务已由 Rewards 确认完成:\s*(.+)$", line)
        if match:
            progress = f"每日任务：{match.group(1)} 已完成"
            continue
        if "今日每日设置已全部完成" in line:
            progress = "每日设置：今日已完成"
        elif "任务前积分:" in line:
            progress = "准备执行任务"
        elif "=== 任务完成 ===" in line:
            progress = "正在保存结果"
    return progress


def format_running_row(
    started_at: str,
    trigger: str,
    progress: str,
    duration_sec: int,
    *,
    login: bool = False,
) -> dict[str, str]:
    return {
        "_running": "1",
        "time": started_at.replace("T", " ")[:16],
        "status": "登录中" if login else "运行中",
        "trigger": TRIGGER_LABELS.get(trigger, trigger),
        "earned": "—",
        "completed": progress,
        "skipped": "—",
        "failed": "—",
        "duration": f"{max(0, duration_sec)}s",
    }


def _task_summary(tasks: list[Any]) -> str:
    if not tasks:
        return "—"
    text = ", ".join("积分未核实" if task == "points_unverified" else str(task) for task in tasks)
    return text if len(text) <= 42 else f"{text[:39]}…"
