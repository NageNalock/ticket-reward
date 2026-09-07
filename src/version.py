"""Application version and identity embedded in each release bundle."""
from __future__ import annotations

import json
from dataclasses import dataclass

from src.utils.storage import bundled_path

APP_VERSION = "0.5.4"
REPOSITORY = "NageNalock/ticket-reward"


@dataclass(frozen=True)
class InstalledVersion:
    version: str = APP_VERSION
    build_number: int = 0
    release_tag: str = ""

    @property
    def label(self) -> str:
        suffix = f" · build {self.build_number}" if self.build_number else " · 本地开发版"
        return f"v{self.version}{suffix}"


def installed_version() -> InstalledVersion:
    try:
        info = json.loads(bundled_path("build-info.json").read_text(encoding="utf-8"))
        return InstalledVersion(
            version=str(info["version"]),
            build_number=max(0, int(info["build_number"])),
            release_tag=str(info["release_tag"]),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return InstalledVersion()
