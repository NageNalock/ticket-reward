"""Read saved Bing session metadata without opening a browser or exposing cookies."""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from enum import Enum
from pathlib import Path

from src.utils.config_loader import load_config
from src.utils.storage import project_path

CHROMIUM_EPOCH_OFFSET = 11_644_473_600


class LoginState(str, Enum):
    CHECKING = "checking"
    LOGGED_IN = "logged_in"
    LOGGED_OUT = "logged_out"
    EXPIRED = "expired"
    UNAVAILABLE = "unavailable"
    LOGGING_IN = "logging_in"


def read_login_state(profile: Path, expired_flag: Path, *, now: float | None = None) -> LoginState:
    """Use the same _U session as BrowserManager, including its local expiry."""
    try:
        if expired_flag.exists():
            return LoginState.EXPIRED
        candidates = (profile / "Default/Network/Cookies", profile / "Default/Cookies")
        database = next((path for path in candidates if path.is_file()), None)
        if database is None:
            return LoginState.LOGGED_OUT
        timestamp = time.time() if now is None else now
        chromium_now = int((timestamp + CHROMIUM_EPOCH_OFFSET) * 1_000_000)
        with closing(
            sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.1)
        ) as connection:
            # Only a boolean leaves SQLite. Cookie values and account details are
            # neither selected, decrypted, copied nor written to logs/cache files.
            result = connection.execute(
                "SELECT MAX(CASE WHEN expires_utc = 0 OR expires_utc > ? THEN 1 ELSE 0 END) "
                "FROM cookies WHERE name = '_U' "
                "AND (host_key = 'bing.com' OR host_key LIKE '%.bing.com') "
                "AND (length(value) > 0 OR length(encrypted_value) > 0)",
                (chromium_now,),
            ).fetchone()[0]
        if result is None:
            return LoginState.LOGGED_OUT
        return LoginState.LOGGED_IN if result else LoginState.EXPIRED
    except (OSError, sqlite3.Error, ValueError):
        # A busy/corrupt profile is unknown, rather than evidence of a logout.
        return LoginState.UNAVAILABLE


def saved_login_state() -> LoginState:
    try:
        config = load_config()
        return read_login_state(
            project_path(config["browser"]["user_data_dir"]),
            project_path("data/login_expired.flag"),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return LoginState.UNAVAILABLE
