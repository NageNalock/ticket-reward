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
    if args == ["--check-update"]:
        from src.updater import UpdateError, fetch_latest_release, is_newer_release
        from src.version import installed_version

        current = installed_version()
        try:
            release = fetch_latest_release()
        except UpdateError as exc:
            print(str(exc), file=sys.stderr)
            return 1
        print(f"Current: {current.label}")
        print(f"Latest: {release.tag if release else 'No release'}")
        if release is not None:
            print(f"Newer: {is_newer_release(release.tag, current)}")
            print(f"Asset: {release.asset.name if release.asset else 'No compatible asset'}")
        return 0
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
