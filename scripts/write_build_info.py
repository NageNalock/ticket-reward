#!/usr/bin/env python3
"""Embed the exact release tag so consecutive CI builds can be compared."""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.version import APP_VERSION  # noqa: E402


def main() -> None:
    build_number = int(os.environ.get("GITHUB_RUN_NUMBER", "0"))
    sha = os.environ.get("GITHUB_SHA", "")
    if build_number and not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("CI builds require a valid GITHUB_SHA")
    tag = f"build-{build_number}-{sha[:7]}" if build_number else ""
    payload = {"version": APP_VERSION, "build_number": build_number, "release_tag": tag}
    path = ROOT / "build/build-info.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"Build identity: {APP_VERSION} ({tag or 'local'})")


if __name__ == "__main__":
    main()
