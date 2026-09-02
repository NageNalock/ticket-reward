#!/usr/bin/env python3
from __future__ import annotations

import multiprocessing
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def main() -> int:
    multiprocessing.freeze_support()
    args = sys.argv[1:]
    if args and args[0] == "--worker":
        from src.main import main as rewards_main

        worker_args = args[1:] or ["--mode", "headless", "--trigger", "ui"]
        return rewards_main(worker_args)
    if args and args[0] == "--login-worker":
        from src.login import main as login_main

        return login_main(["--auto", *args[1:]])

    from src.ui.app import run_menu_bar_app

    return run_menu_bar_app(smoke_test="--ui-smoke-test" in args)


if __name__ == "__main__":
    raise SystemExit(main())
