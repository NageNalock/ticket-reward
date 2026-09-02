from __future__ import annotations

import json
import os
import tempfile
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from src.utils.storage import project_path


class PointsTracker:
    """Append-only run history with migration from the original daily format."""

    VERSION = 2

    def __init__(self, history_path: str | Path = "data/points_history.json"):
        self.history_path = project_path(history_path)
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self._runs = self._load()

    def _load(self) -> list[dict[str, Any]]:
        if not self.history_path.exists():
            return []
        try:
            with self.history_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)

            if isinstance(data, dict) and isinstance(data.get("runs"), list):
                return [record for record in data["runs"] if isinstance(record, dict)]
            if isinstance(data, list):
                return [record for record in data if isinstance(record, dict)]
            if isinstance(data, dict):
                return self._migrate_daily_history(data)
            raise ValueError("历史文件根节点格式不受支持")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            logger.warning(f"积分历史无法读取，将保留原文件并从空记录继续: {exc}")
            return []

    @staticmethod
    def _migrate_daily_history(data: dict[str, Any]) -> list[dict[str, Any]]:
        migrated: list[dict[str, Any]] = []
        for day, raw_record in sorted(data.items()):
            if not isinstance(raw_record, dict):
                continue
            record = dict(raw_record)
            record.setdefault("date", day)
            record.setdefault("timestamp", f"{day}T00:00:00")
            failed = record.get("tasks_failed") or []
            completed = record.get("tasks_completed") or []
            record.setdefault("status", PointsTracker._status(completed, failed))
            record.setdefault("trigger", "legacy")
            migrated.append(record)
        return migrated

    @staticmethod
    def _status(completed_tasks: list[str], failed_tasks: list[str]) -> str:
        if failed_tasks and completed_tasks:
            return "partial"
        if failed_tasks:
            return "failed"
        return "success"

    def record_run(
        self,
        start_points: int | None,
        end_points: int | None,
        completed_tasks: list[str],
        failed_tasks: list[str],
        duration_sec: float,
        trigger: str = "manual",
    ) -> dict[str, Any]:
        now = datetime.now()
        earned = (
            end_points - start_points
            if start_points is not None and end_points is not None
            else None
        )
        record: dict[str, Any] = {
            "date": now.strftime("%Y-%m-%d"),
            "timestamp": now.isoformat(timespec="seconds"),
            "status": self._status(completed_tasks, failed_tasks),
            "trigger": trigger,
            "start_points": start_points,
            "end_points": end_points,
            "earned": earned,
            "tasks_completed": list(completed_tasks),
            "tasks_failed": list(failed_tasks),
            "duration_sec": round(duration_sec, 1),
        }
        self._runs.append(record)
        self._save()
        return record

    def _save(self) -> None:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.history_path.parent,
            prefix=f".{self.history_path.name}.",
            suffix=".tmp",
        )
        payload = {"version": self.VERSION, "runs": self._runs}
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.history_path)
        finally:
            with suppress(OSError):
                Path(temporary_name).unlink(missing_ok=True)

    def get_history(self, runs: int = 20) -> list[dict[str, Any]]:
        ordered = sorted(
            self._runs,
            key=lambda record: str(record.get("timestamp", "")),
            reverse=True,
        )
        return [dict(record) for record in ordered[: max(0, runs)]]

    def get_summary(self) -> dict[str, int]:
        return {
            "total": len(self._runs),
            "success": sum(record.get("status") == "success" for record in self._runs),
            "partial": sum(record.get("status") == "partial" for record in self._runs),
            "failed": sum(record.get("status") == "failed" for record in self._runs),
            "earned": sum(
                int(record["earned"])
                for record in self._runs
                if isinstance(record.get("earned"), int)
            ),
        }
