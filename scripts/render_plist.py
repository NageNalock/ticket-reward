#!/usr/bin/env python3
from __future__ import annotations

import plistlib
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.config_loader import load_config  # noqa: E402


def main() -> int:
    config = load_config()
    schedule = config["schedule"]
    output = PROJECT_ROOT / "scripts/com.user.bingrewards.generated.plist"
    venv_bin = PROJECT_ROOT / ".venv/bin"
    payload = {
        "Label": "com.user.bingrewards",
        "ProgramArguments": ["/bin/bash", str(PROJECT_ROOT / "scripts/run.sh")],
        "StartCalendarInterval": {
            "Hour": int(schedule["run_hour"]),
            "Minute": int(schedule["run_minute"]),
        },
        "RunAtLoad": False,
        "StandardOutPath": str(PROJECT_ROOT / "data/logs/launchd.out.log"),
        "StandardErrorPath": str(PROJECT_ROOT / "data/logs/launchd.err.log"),
        "EnvironmentVariables": {
            "PATH": f"{venv_bin}:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"
        },
        "WorkingDirectory": str(PROJECT_ROOT),
    }
    with output.open("wb") as handle:
        plistlib.dump(payload, handle, sort_keys=False)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
