from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

from src.utils.storage import ensure_runtime_dirs, project_path


class ConfigError(ValueError):
    """Raised when the YAML configuration is missing or invalid."""


SCHEDULE_DEFAULTS = {
    "retry_count": 3,
    "retry_interval_min": 30,
}


REQUIRED_PATHS = (
    ("browser", "headless"),
    ("browser", "user_data_dir"),
    ("browser", "viewport", "width"),
    ("browser", "viewport", "height"),
    ("user_agents", "pc"),
    ("user_agents", "mobile"),
    ("search", "pc_count"),
    ("search", "mobile_count"),
    ("search", "min_interval_sec"),
    ("search", "max_interval_sec"),
    ("daily_activities", "enabled"),
)


def _get_nested(config: dict[str, Any], keys: tuple[str, ...]) -> Any:
    current: Any = config
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            raise ConfigError(f"缺少配置项: {'.'.join(keys)}")
        current = current[key]
    return current


def validate_config(config: dict[str, Any]) -> None:
    for keys in REQUIRED_PATHS:
        _get_nested(config, keys)

    for key in ("pc_count", "mobile_count"):
        value = config["search"][key]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ConfigError(f"search.{key} 必须是非负整数")

    minimum = config["search"]["min_interval_sec"]
    maximum = config["search"]["max_interval_sec"]
    if minimum < 0 or maximum < minimum:
        raise ConfigError("搜索间隔必须满足 0 <= min_interval_sec <= max_interval_sec")

    probability = config["search"].get("click_result_probability", 0)
    if not 0 <= probability <= 1:
        raise ConfigError("search.click_result_probability 必须在 0 到 1 之间")

    viewport = config["browser"]["viewport"]
    if viewport["width"] <= 0 or viewport["height"] <= 0:
        raise ConfigError("browser.viewport 宽高必须大于 0")

    schedule = config.get("schedule", {})
    hour = schedule.get("run_hour")
    minute = schedule.get("run_minute")
    jitter = schedule.get("jitter_sec")
    retry_count = schedule.get("retry_count", SCHEDULE_DEFAULTS["retry_count"])
    retry_interval = schedule.get(
        "retry_interval_min", SCHEDULE_DEFAULTS["retry_interval_min"]
    )
    if not isinstance(hour, int) or isinstance(hour, bool) or not 0 <= hour <= 23:
        raise ConfigError("schedule.run_hour 必须是 0 到 23 的整数")
    if not isinstance(minute, int) or isinstance(minute, bool) or not 0 <= minute <= 59:
        raise ConfigError("schedule.run_minute 必须是 0 到 59 的整数")
    if not isinstance(jitter, int) or isinstance(jitter, bool) or jitter < 0:
        raise ConfigError("schedule.jitter_sec 必须是非负整数")
    if (
        not isinstance(retry_count, int)
        or isinstance(retry_count, bool)
        or retry_count < 0
    ):
        raise ConfigError("schedule.retry_count 必须是非负整数")
    if (
        not isinstance(retry_interval, int)
        or isinstance(retry_interval, bool)
        or retry_interval <= 0
    ):
        raise ConfigError("schedule.retry_interval_min 必须是正整数")


def load_config(path: str | Path = "config/config.yaml") -> dict[str, Any]:
    ensure_runtime_dirs()
    config_path = project_path(path)
    if not config_path.is_file():
        raise ConfigError(f"配置文件不存在: {config_path}")

    try:
        with config_path.open("r", encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ConfigError(f"配置文件格式错误: {exc}") from exc

    if not isinstance(loaded, dict):
        raise ConfigError("配置文件根节点必须是对象")

    config = deepcopy(loaded)
    schedule = config.get("schedule")
    if isinstance(schedule, dict):
        for key, value in SCHEDULE_DEFAULTS.items():
            schedule.setdefault(key, value)
    validate_config(config)
    return config
