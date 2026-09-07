from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.utils.login_status import (
    CHROMIUM_EPOCH_OFFSET,
    LoginState,
    read_login_state,
    saved_login_state,
)


class LoginStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.profile = Path(self.temp.name) / "profile"
        self.flag = Path(self.temp.name) / "login_expired.flag"
        self.now = 1_700_000_000

    def database(self, *, network: bool = False) -> Path:
        path = self.profile / ("Default/Network/Cookies" if network else "Default/Cookies")
        path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, "
                "encrypted_value BLOB, expires_utc INTEGER)"
            )
        return path

    def cookie(
        self,
        database: Path,
        *,
        expires_in: int | None = 3600,
        domain: str = ".bing.com",
        name: str = "_U",
        value: str = "",
        encrypted: bytes = b"synthetic-test-session",
    ) -> None:
        expires = (
            0
            if expires_in is None
            else int((self.now + expires_in + CHROMIUM_EPOCH_OFFSET) * 1_000_000)
        )
        with sqlite3.connect(database) as connection:
            connection.execute(
                "INSERT INTO cookies VALUES (?, ?, ?, ?, ?)",
                (domain, name, value, encrypted, expires),
            )

    def state(self) -> LoginState:
        return read_login_state(self.profile, self.flag, now=self.now)

    def test_first_launch_without_profile_is_logged_out_and_creates_nothing(self) -> None:
        self.assertEqual(self.state(), LoginState.LOGGED_OUT)
        self.assertFalse(self.profile.exists())

    def test_restores_saved_login_on_every_read_without_modifying_database(self) -> None:
        database = self.database()
        self.cookie(database)
        before = (hashlib.sha256(database.read_bytes()).digest(), database.stat().st_mtime_ns)
        self.assertEqual(self.state(), LoginState.LOGGED_IN)
        self.assertEqual(self.state(), LoginState.LOGGED_IN)
        self.assertEqual(
            before, (hashlib.sha256(database.read_bytes()).digest(), database.stat().st_mtime_ns)
        )

    def test_expired_cookie_requires_login(self) -> None:
        self.cookie(self.database(), expires_in=-1)
        self.assertEqual(self.state(), LoginState.EXPIRED)

    def test_server_expiry_flag_overrides_locally_valid_session(self) -> None:
        self.cookie(self.database())
        self.flag.touch()
        self.assertEqual(self.state(), LoginState.EXPIRED)
        self.flag.unlink()
        self.assertEqual(self.state(), LoginState.LOGGED_IN)

    def test_session_cookie_without_expiry_is_recognized(self) -> None:
        self.cookie(self.database(), expires_in=None)
        self.assertEqual(self.state(), LoginState.LOGGED_IN)

    def test_other_domains_names_and_empty_values_are_not_login(self) -> None:
        database = self.database()
        self.cookie(database, domain="bing.com.example.invalid")
        self.cookie(database, domain="notbing.com")
        self.cookie(database, name="other_cookie")
        self.cookie(database, encrypted=b"")
        self.assertEqual(self.state(), LoginState.LOGGED_OUT)

    def test_current_network_layout_takes_precedence_over_legacy_database(self) -> None:
        self.cookie(self.database())
        self.database(network=True)
        self.assertEqual(self.state(), LoginState.LOGGED_OUT)

    def test_accepts_unencrypted_session_and_bing_subdomain(self) -> None:
        self.cookie(
            self.database(network=True), domain="www.bing.com", value="synthetic", encrypted=b""
        )
        self.assertEqual(self.state(), LoginState.LOGGED_IN)

    def test_missing_table_is_unavailable_instead_of_logged_out(self) -> None:
        database = self.database()
        with sqlite3.connect(database) as connection:
            connection.execute("DROP TABLE cookies")
        self.assertEqual(self.state(), LoginState.UNAVAILABLE)

    def test_locked_database_is_unavailable_instead_of_logged_out(self) -> None:
        database = self.database()
        self.cookie(database)
        with sqlite3.connect(database) as connection:
            connection.execute("BEGIN EXCLUSIVE")
            self.assertEqual(self.state(), LoginState.UNAVAILABLE)
        self.assertEqual(self.state(), LoginState.LOGGED_IN)

    def test_refresh_observes_login_and_logout_in_the_same_profile(self) -> None:
        database = self.database()
        self.assertEqual(self.state(), LoginState.LOGGED_OUT)
        self.cookie(database)
        self.assertEqual(self.state(), LoginState.LOGGED_IN)
        with sqlite3.connect(database) as connection:
            connection.execute("DELETE FROM cookies")
        self.assertEqual(self.state(), LoginState.LOGGED_OUT)

    def test_invalid_config_does_not_report_a_logout(self) -> None:
        with patch("src.utils.login_status.load_config", side_effect=ValueError("invalid config")):
            self.assertEqual(saved_login_state(), LoginState.UNAVAILABLE)
