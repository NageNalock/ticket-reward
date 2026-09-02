from __future__ import annotations

import shutil
import sys
from pathlib import Path

IS_FROZEN = bool(getattr(sys, "frozen", False))
RESOURCE_ROOT = Path(sys._MEIPASS) if IS_FROZEN else Path(__file__).resolve().parents[2]
APP_SUPPORT_ROOT = Path.home() / "Library/Application Support/Bing Rewards"
PROJECT_ROOT = RESOURCE_ROOT


def bundled_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else RESOURCE_ROOT / path


def project_path(value: str | Path) -> Path:
    """Resolve writable paths to Application Support in a frozen macOS app."""
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    if IS_FROZEN and path.parts and path.parts[0] in {"config", "data"}:
        return APP_SUPPORT_ROOT / path
    return PROJECT_ROOT / path


def ensure_runtime_dirs() -> None:
    for relative in (
        "data/browser_profile",
        "data/logs",
        "data/screenshots",
    ):
        project_path(relative).mkdir(parents=True, exist_ok=True)

    if IS_FROZEN:
        config_dir = project_path("config")
        config_dir.mkdir(parents=True, exist_ok=True)
        for filename in ("config.yaml", "search_terms.json"):
            destination = config_dir / filename
            source = bundled_path(f"config/{filename}")
            if not destination.exists() and source.exists():
                shutil.copy2(source, destination)
