"""Copy only the browser revisions selected by the installed Playwright CLI."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def bundle_browsers(cache: Path, destination: Path) -> None:
    cache = cache.resolve()
    result = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "--dry-run", "--no-shell", "chromium"],
        env={**os.environ, "PLAYWRIGHT_BROWSERS_PATH": str(cache)},
        check=True, capture_output=True, text=True,
    )
    paths = [Path(match).resolve() for match in re.findall(
        r"^\s*Install location:\s*(.+?)\s*$", result.stdout, re.MULTILINE,
    )]
    if (
        sum(path.name.startswith("chromium-") for path in paths) != 1
        or len(paths) != len(set(paths))
        or any(path.parent != cache or not path.is_dir()
               or not re.fullmatch(r"(?:chromium|ffmpeg)-\d+", path.name) for path in paths)
    ):
        raise RuntimeError("Cannot identify the installed Chromium runtime; refusing to bundle the cache")
    # A fresh destination prevents old revisions and .links from leaking into a release.
    destination.mkdir(parents=True, exist_ok=False)
    for path in paths:
        shutil.copytree(path, destination / path.name, symlinks=True, copy_function=shutil.copy)
    print("Bundled browser runtimes: " + ", ".join(path.name for path in paths))


if __name__ == "__main__":
    bundle_browsers(Path(sys.argv[1]), Path(sys.argv[2]))
