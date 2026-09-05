from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from src.updater import (
    API_URL,
    DownloadCancelled,
    UpdateError,
    download_release,
    fetch_latest_release,
    is_newer_release,
    parse_checksum,
    parse_release,
)
from src.version import REPOSITORY, InstalledVersion

ARCHIVE = b"a test release archive"
HASH = hashlib.sha256(ARCHIVE).hexdigest()
ASSET_NAME = "Bing-Rewards-macOS-arm64.zip"
TAG = "build-10-abcdef0"


def payload() -> dict:
    return {
        "tag_name": TAG,
        "name": "Bing Rewards build 10",
        "body": "Release notes",
        "html_url": f"https://github.com/{REPOSITORY}/releases/tag/{TAG}",
        "draft": False,
        "prerelease": False,
        "published_at": "2026-09-05T00:00:00Z",
        "assets": [{
            "name": ASSET_NAME,
            "browser_download_url": f"https://github.com/{REPOSITORY}/releases/download/{TAG}/{ASSET_NAME}",
            "state": "uploaded", "size": len(ARCHIVE), "digest": f"sha256:{HASH}",
        }],
    }


class ReleaseTests(unittest.TestCase):
    def test_compares_numeric_builds_and_never_downgrades(self) -> None:
        self.assertTrue(is_newer_release(TAG, InstalledVersion(build_number=9)))
        self.assertFalse(is_newer_release(TAG, InstalledVersion(build_number=10)))
        self.assertFalse(is_newer_release(TAG, InstalledVersion(build_number=11)))
        self.assertFalse(is_newer_release(TAG, InstalledVersion(release_tag=TAG)))
        self.assertIsNone(is_newer_release(TAG, InstalledVersion()))

    def test_compares_semantic_versions_numerically(self) -> None:
        self.assertTrue(is_newer_release("v0.10.0", InstalledVersion("0.9.9")))
        self.assertFalse(is_newer_release("v0.5.0", InstalledVersion("0.5.0")))
        self.assertFalse(is_newer_release("0.4.1", InstalledVersion("0.5.0")))
        self.assertIsNone(is_newer_release("nightly", InstalledVersion()))
        self.assertIsNone(is_newer_release("v1.0.0-beta.1", InstalledVersion()))

    def test_selects_matching_architecture_only(self) -> None:
        release = parse_release(payload(), "arm64")
        self.assertEqual(release.asset.name, ASSET_NAME)
        self.assertEqual(release.asset.sha256, HASH)
        self.assertIsNone(parse_release(payload(), "x86_64").asset)
        self.assertIsNone(parse_release(payload(), "unknown").asset)

    def test_selects_universal_fallback(self) -> None:
        data = payload()
        asset = data["assets"][0]
        asset["name"] = asset["name"].replace("arm64", "universal2")
        asset["browser_download_url"] = asset["browser_download_url"].replace("arm64", "universal2")
        self.assertIn("universal2", parse_release(data, "x86_64").asset.name)

    def test_rejects_unpublished_and_malformed_releases(self) -> None:
        for key in ("draft", "prerelease"):
            with self.subTest(key=key), self.assertRaises(UpdateError):
                parse_release({**payload(), key: True}, "arm64")
        for invalid in (None, [], {}, {**payload(), "assets": None}):
            with self.subTest(invalid=invalid), self.assertRaises(UpdateError):
                parse_release(invalid, "arm64")

    def test_rejects_untrusted_asset_urls(self) -> None:
        for url in (
            "http://github.com/example.zip", "https://evil.test/example.zip",
            f"https://github.com/other/repo/releases/download/{TAG}/{ASSET_NAME}",
            f"https://github.com/{REPOSITORY}/releases/download/other/{ASSET_NAME}",
        ):
            data = payload()
            data["assets"][0]["browser_download_url"] = url
            with self.subTest(url=url), self.assertRaises(UpdateError):
                parse_release(data, "arm64")

    def test_checks_asset_size_and_upload_state(self) -> None:
        for size in (0, -1, True, "100", 3 * 1024**3):
            data = payload()
            data["assets"][0]["size"] = size
            with self.subTest(size=size), self.assertRaises(UpdateError):
                parse_release(data, "arm64")
        data = payload()
        data["assets"][0]["state"] = "starter"
        self.assertIsNone(parse_release(data, "arm64").asset)

    def test_fetches_public_github_endpoint(self) -> None:
        with patch("src.updater.urlopen", return_value=io.BytesIO(json.dumps(payload()).encode())) as request:
            self.assertEqual(fetch_latest_release("arm64").tag, TAG)
        args, kwargs = request.call_args
        self.assertEqual(args[0].full_url, API_URL)
        self.assertNotIn("Authorization", args[0].headers)
        self.assertGreater(kwargs["timeout"], 0)

    def test_handles_empty_repository_and_network_failures(self) -> None:
        with patch("src.updater.urlopen", side_effect=HTTPError(API_URL, 404, "", {}, None)):
            self.assertIsNone(fetch_latest_release())
        for error in (HTTPError(API_URL, 429, "", {}, None), URLError("offline"), TimeoutError()):
            with self.subTest(error=error), patch("src.updater.urlopen", side_effect=error), self.assertRaises(UpdateError):
                fetch_latest_release()

    def test_rejects_malformed_and_oversized_responses(self) -> None:
        for body in (b"not JSON", b"[1,2]", b"x" * (2 * 1024**2 + 1)):
            with patch("src.updater.urlopen", return_value=io.BytesIO(body)), self.assertRaises(UpdateError):
                fetch_latest_release()


class DownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.destination = Path(self.temp.name)
        self.release = parse_release(payload(), "arm64")

    def test_atomically_saves_verified_archive_and_reports_progress(self) -> None:
        progress = []
        with patch("src.updater.urlopen", return_value=io.BytesIO(ARCHIVE)):
            path = download_release(self.release, self.destination, lambda *item: progress.append(item))
        self.assertEqual(path.read_bytes(), ARCHIVE)
        self.assertEqual(path.name, ASSET_NAME)
        self.assertEqual(progress[-1], (len(ARCHIVE), len(ARCHIVE)))
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_removes_failed_or_incomplete_downloads(self) -> None:
        for body in (ARCHIVE[:-1], ARCHIVE + b"extra", b"x" * len(ARCHIVE)):
            with self.subTest(body=body), patch("src.updater.urlopen", return_value=io.BytesIO(body)), self.assertRaises(UpdateError):
                download_release(self.release, self.destination)
            self.assertEqual(list(self.destination.iterdir()), [])

    def test_missing_checksum_never_downloads(self) -> None:
        data = payload()
        data["assets"][0].pop("digest")
        with patch("src.updater.urlopen") as request, self.assertRaises(UpdateError):
            download_release(parse_release(data, "arm64"), self.destination)
        request.assert_not_called()

    def test_reads_legacy_checksum_manifest(self) -> None:
        data = payload()
        data["assets"][0].pop("digest")
        data["assets"].append({
            "name": "SHA256SUMS.txt", "state": "uploaded",
            "browser_download_url": f"https://github.com/{REPOSITORY}/releases/download/{TAG}/SHA256SUMS.txt",
        })
        with patch("src.updater.urlopen", side_effect=[
            io.BytesIO(f"{HASH}  dist/{ASSET_NAME}\n".encode()), io.BytesIO(ARCHIVE),
        ]):
            path = download_release(parse_release(data, "arm64"), self.destination)
        self.assertEqual(path.read_bytes(), ARCHIVE)

    def test_checksum_manifest_matches_exact_filename(self) -> None:
        for prefix in ("", "*", "dist/", "./"):
            self.assertEqual(parse_checksum(f"{HASH}  {prefix}{ASSET_NAME}", ASSET_NAME), HASH)
        with self.assertRaises(UpdateError):
            parse_checksum(f"{HASH}  other/{ASSET_NAME}", ASSET_NAME)
        with self.assertRaises(UpdateError):
            parse_checksum(f"{HASH}  {ASSET_NAME}\n{'a' * 64}  {ASSET_NAME}", ASSET_NAME)

    def test_cancellation_before_and_during_download(self) -> None:
        cancel = Event()
        cancel.set()
        with patch("src.updater.urlopen") as request, self.assertRaises(DownloadCancelled):
            download_release(self.release, self.destination, cancel=cancel)
        request.assert_not_called()
        cancel.clear()
        with patch("src.updater.urlopen", return_value=io.BytesIO(ARCHIVE)), self.assertRaises(DownloadCancelled):
            download_release(self.release, self.destination, lambda *_: cancel.set(), cancel)
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_stream_network_failure_cleans_partial_file(self) -> None:
        class BrokenResponse(io.BytesIO):
            def read(self, _size=-1):
                raise TimeoutError()

        with patch("src.updater.urlopen", return_value=BrokenResponse()), self.assertRaises(UpdateError):
            download_release(self.release, self.destination)
        self.assertEqual(list(self.destination.iterdir()), [])
