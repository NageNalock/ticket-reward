from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Callable


@dataclass(frozen=True)
class RetryPlan:
    run_at: datetime
    retry_number: int


def calculate_next_run(
    now: datetime,
    hour: int,
    minute: int,
    jitter_sec: int,
    randint: Callable[[int, int], int] = random.randint,
) -> datetime:
    """Return the next future local run time, including a bounded jitter."""
    jitter_sec = max(0, int(jitter_sec))
    for day_offset in (0, 1):
        day = now.date() + timedelta(days=day_offset)
        base = datetime.combine(day, time(hour=hour, minute=minute))
        candidate = base + timedelta(seconds=randint(0, jitter_sec))
        if candidate > now:
            return candidate
    raise RuntimeError("无法计算下一次运行时间")


def calculate_retry_run(
    now: datetime,
    cycle_date: date,
    retry_interval_min: int,
) -> datetime | None:
    """Return a future retry time without crossing the scheduled run's day."""
    candidate = now + timedelta(minutes=max(1, int(retry_interval_min)))
    return candidate if candidate.date() == cycle_date else None


def find_pending_retry(
    now: datetime,
    records: list[dict[str, object]],
    retry_count: int,
    retry_interval_min: int,
) -> RetryPlan | None:
    """Recover an unfinished scheduled cycle from today's persisted history."""
    retry_count = max(0, int(retry_count))
    if retry_count == 0:
        return None

    dated_records: list[tuple[datetime, dict[str, object]]] = []
    for record in records:
        raw_timestamp = record.get("timestamp")
        if not isinstance(raw_timestamp, str):
            continue
        try:
            timestamp = datetime.fromisoformat(raw_timestamp)
        except ValueError:
            continue
        if timestamp.date() == now.date():
            dated_records.append((timestamp, record))

    scheduled = [
        item for item in dated_records if item[1].get("trigger") == "scheduled"
    ]
    if not scheduled:
        return None

    scheduled.sort(key=lambda item: item[0])
    latest_timestamp, latest_record = scheduled[-1]
    if latest_record.get("status") == "success":
        return None

    failed_tasks = latest_record.get("tasks_failed")
    if isinstance(failed_tasks, list) and "login_expired" in failed_tasks:
        return None

    if any(
        timestamp > latest_timestamp and record.get("status") == "success"
        for timestamp, record in dated_records
    ):
        return None

    # One scheduled record is the initial attempt, so it is followed by retry #1.
    retry_number = len(scheduled)
    if retry_number > retry_count:
        return None

    candidate = latest_timestamp + timedelta(minutes=max(1, int(retry_interval_min)))
    if candidate <= now:
        candidate = now + timedelta(seconds=1)
    if candidate.date() != now.date():
        return None
    return RetryPlan(candidate, retry_number)
