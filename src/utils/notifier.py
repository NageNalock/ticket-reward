from __future__ import annotations

import platform
import subprocess

from loguru import logger

_APPLESCRIPT = """
on run argv
  display notification (item 2 of argv) with title (item 1 of argv)
end run
""".strip()


def send_notification(title: str, message: str) -> bool:
    """Send a best-effort macOS notification without invoking a shell."""
    if platform.system() != "Darwin":
        logger.debug("桌面通知仅在 macOS 上启用")
        return False
    try:
        subprocess.run(
            ["osascript", "-e", _APPLESCRIPT, title, message],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return True
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(f"发送桌面通知失败: {exc}")
        return False
