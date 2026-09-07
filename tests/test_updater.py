from __future__ import annotations

import hashlib
import io
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from src.updater import (
    LATEST_URL,
    RELEASES_URL,
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
ASSET_NAME = "Bing-Rewards-macOS-arm64.dmg"
TAG = "build-10-abcdef0"
PAGE_URL = f"{RELEASES_URL}/tag/{TAG}"
ASSETS_URL = f"{RELEASES_URL}/expanded_assets/{TAG}"
DOWNLOAD_URL = f"{RELEASES_URL}/download/{TAG}/{ASSET_NAME}"


class WebResponse(io.BytesIO):
    def __init__(self, body: bytes, url: str, headers: dict | None = None):
        super().__init__(body)
        self.url = url
        self.headers = headers or {}
        self.read_count = 0

    def geturl(self) -> str:
        return self.url

    def read(self, size: int = -1) -> bytes:
        self.read_count += 1
        return super().read(size)


def web_fixture(name: str) -> bytes:
    text = (Path(__file__).parent / "fixtures" / name).read_text()
    return text.replace("__TAG__", TAG).replace("__HASH__", HASH).encode()


def payload(extension: str = "dmg") -> dict:
    asset_name = f"Bing-Rewards-macOS-arm64.{extension}"
    return {
        "tag_name": TAG,
        "name": "Bing Rewards build 10",
        "body": "Release notes",
        "html_url": f"https://github.com/{REPOSITORY}/releases/tag/{TAG}",
        "draft": False,
        "prerelease": False,
        "published_at": "2026-09-05T00:00:00Z",
        "assets": [{
            "name": asset_name,
            "browser_download_url": f"https://github.com/{REPOSITORY}/releases/download/{TAG}/{asset_name}",
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

    def test_prefers_dmg_when_legacy_zip_is_also_present(self) -> None:
        data = payload("zip")
        data["assets"].extend(payload()["assets"])
        self.assertEqual(parse_release(data, "arm64").asset.name, ASSET_NAME)

    def test_prefers_compatible_universal_dmg_over_native_zip(self) -> None:
        data = payload("zip")
        dmg = payload()["assets"][0]
        dmg["name"] = dmg["name"].replace("arm64", "universal2")
        dmg["browser_download_url"] = dmg["browser_download_url"].replace("arm64", "universal2")
        data["assets"].append(dmg)
        self.assertEqual(parse_release(data, "arm64").asset.name, "Bing-Rewards-macOS-universal2.dmg")

    def test_keeps_legacy_zip_support(self) -> None:
        release = parse_release(payload("zip"), "arm64")
        self.assertEqual(release.asset.name, "Bing-Rewards-macOS-arm64.zip")

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


class PublicPageTests(unittest.TestCase):
    def test_head_stays_head_through_actual_cdn_style_redirects(self) -> None:
        from src.updater import _open

        requests = []
        redirects = (301, 302, 303, 307, 308)

        class Handler(BaseHTTPRequestHandler):
            def do_HEAD(self):
                requests.append((self.command, self.path))
                index = int(self.path.strip("/"))
                if index < len(redirects):
                    self.send_response(redirects[index])
                    self.send_header("Location", f"/{index + 1}")
                    self.send_header("Content-Length", "0")
                else:
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(ARCHIVE)))
                self.end_headers()

            def do_GET(self):
                requests.append((self.command, self.path))
                self.send_error(405)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = Thread(target=lambda: server.serve_forever(poll_interval=0.01), daemon=True)
        thread.start()
        try:
            with _open(f"http://127.0.0.1:{server.server_port}/0", method="HEAD") as response:
                self.assertEqual(response.headers["Content-Length"], str(len(ARCHIVE)))
            self.assertEqual(requests, [("HEAD", f"/{index}") for index in range(6)])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def responses(self) -> list[WebResponse]:
        return [
            WebResponse(web_fixture("github_release.html"), PAGE_URL),
            WebResponse(web_fixture("github_release_assets.html"), ASSETS_URL),
            WebResponse(b"must never read archive bytes", DOWNLOAD_URL,
                        {"Content-Length": str(len(ARCHIVE))}),
        ]

    def test_check_only_reads_public_pages_and_head_metadata(self) -> None:
        responses = self.responses()
        with patch("src.updater._urlopen", side_effect=responses) as request:
            release = fetch_latest_release("arm64")
        self.assertEqual(release.tag, TAG)
        self.assertEqual(release.title, "Bing Rewards <update>")
        self.assertEqual(release.notes, "修复更新 & 优化体积\n• 保留确认下载\n说明")
        self.assertEqual(release.published_at, "2026-09-05")
        self.assertEqual(release.asset.size, len(ARCHIVE))
        self.assertEqual(release.asset.sha256, HASH)
        self.assertEqual(release.checksum_url, f"{RELEASES_URL}/download/{TAG}/SHA256SUMS.txt")
        self.assertEqual(responses[-1].read_count, 0)
        self.assertEqual([(call.args[0].get_method(), call.args[0].full_url)
                          for call in request.call_args_list], [
            ("GET", LATEST_URL), ("GET", ASSETS_URL), ("HEAD", DOWNLOAD_URL),
        ])
        for call in request.call_args_list:
            self.assertNotIn("Authorization", call.args[0].headers)
            self.assertNotIn("X-github-api-version", call.args[0].headers)
            self.assertNotIn("api.github.com", call.args[0].full_url)
            self.assertGreater(call.kwargs["timeout"], 0)
        self.assertEqual(request.call_args_list[0].args[0].headers["Accept"], "text/html")

    def test_missing_digest_uses_manifest_without_fetching_it_during_check(self) -> None:
        responses = self.responses()
        responses[1] = WebResponse(web_fixture("github_release_assets.html").replace(
            f'value="sha256:{HASH}"'.encode(), b'value=""',
        ), ASSETS_URL)
        with patch("src.updater._urlopen", side_effect=responses) as request:
            release = fetch_latest_release("arm64")
        self.assertEqual(release.asset.sha256, "")
        self.assertTrue(release.checksum_url.endswith("SHA256SUMS.txt"))
        self.assertEqual(request.call_count, 3)

    def test_missing_architecture_does_not_request_any_package(self) -> None:
        with patch("src.updater._urlopen", side_effect=self.responses()[:2]) as request:
            release = fetch_latest_release("x86_64")
        self.assertIsNone(release.asset)
        self.assertEqual(request.call_count, 2)

    def test_source_archives_are_not_installers(self) -> None:
        responses = self.responses()
        responses[1] = WebResponse(b'<ul><li><a href="/repo/archive/tag.zip">Source code</a></li></ul>', ASSETS_URL)
        with patch("src.updater._urlopen", side_effect=responses[:2]):
            self.assertIsNone(fetch_latest_release("arm64").asset)

    def test_rejects_external_or_unexpected_redirects(self) -> None:
        for url in (PAGE_URL.replace("github.com", "other.test"),
                    PAGE_URL.replace(REPOSITORY, "other/repo"), LATEST_URL,
                    PAGE_URL + "?download=1", PAGE_URL + "#fragment"):
            with self.subTest(url=url), patch("src.updater._urlopen", return_value=WebResponse(
                web_fixture("github_release.html"), url,
            )) as request, self.assertRaises(UpdateError):
                fetch_latest_release("arm64")
            self.assertEqual(request.call_count, 1)
        responses = self.responses()
        responses[1].url = "https://other.test/assets"
        with patch("src.updater._urlopen", side_effect=responses), self.assertRaises(UpdateError):
            fetch_latest_release("arm64")

    def test_rejects_untrusted_download_links_before_head_request(self) -> None:
        for path in (f"https://other.test/{ASSET_NAME}",
                     f"/{REPOSITORY}/releases/download/other/{ASSET_NAME}"):
            responses = self.responses()
            responses[1] = WebResponse(web_fixture("github_release_assets.html").replace(
                f"/{REPOSITORY}/releases/download/{TAG}/{ASSET_NAME}".encode(), path.encode(),
            ), ASSETS_URL)
            with self.subTest(path=path), patch("src.updater._urlopen", side_effect=responses) as request, self.assertRaises(UpdateError):
                fetch_latest_release("arm64")
            self.assertEqual(request.call_count, 2)

    def test_rejects_ambiguous_or_broken_asset_list(self) -> None:
        fragment = web_fixture("github_release_assets.html")
        for body in (b"not HTML", b"<ul><li>incomplete", fragment + fragment):
            responses = self.responses()
            responses[1] = WebResponse(body, ASSETS_URL)
            with self.subTest(body=body[:50]), patch("src.updater._urlopen", side_effect=responses), self.assertRaises(UpdateError):
                fetch_latest_release("arm64")

    def test_rejects_invalid_or_missing_exact_size(self) -> None:
        for size in ("", "0", "-1", "340 MB", str(3 * 1024**3)):
            responses = self.responses()
            responses[2].headers = {"Content-Length": size}
            with self.subTest(size=size), patch("src.updater._urlopen", side_effect=responses), self.assertRaises(UpdateError):
                fetch_latest_release("arm64")

    def test_handles_empty_repository_and_network_failures(self) -> None:
        with patch("src.updater._urlopen", side_effect=HTTPError(LATEST_URL, 404, "", {}, None)):
            self.assertIsNone(fetch_latest_release())
        for error in (HTTPError(LATEST_URL, 429, "", {}, None), URLError("offline"), TimeoutError()):
            with self.subTest(error=error), patch("src.updater._urlopen", side_effect=error), self.assertRaises(UpdateError):
                fetch_latest_release()

    def test_disappearing_assets_are_reported_as_an_error(self) -> None:
        responses = [self.responses()[0], HTTPError(ASSETS_URL, 404, "", {}, None)]
        with patch("src.updater._urlopen", side_effect=responses), self.assertRaises(UpdateError):
            fetch_latest_release("arm64")

    def test_rejects_malformed_and_oversized_responses(self) -> None:
        for body in (b"not HTML", b"<html>challenge</html>", b"x" * (2 * 1024**2 + 1)):
            with patch("src.updater._urlopen", return_value=WebResponse(body, PAGE_URL)), self.assertRaises(UpdateError):
                fetch_latest_release()
        responses = [self.responses()[0], WebResponse(b"x" * (2 * 1024**2 + 1), ASSETS_URL)]
        with patch("src.updater._urlopen", side_effect=responses), self.assertRaises(UpdateError):
            fetch_latest_release("arm64")


class DownloadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.destination = Path(self.temp.name)
        self.release = parse_release(payload(), "arm64")

    def test_atomically_saves_verified_archive_and_reports_progress(self) -> None:
        progress = []
        with patch("src.updater._urlopen", return_value=io.BytesIO(ARCHIVE)):
            path = download_release(self.release, self.destination, lambda *item: progress.append(item))
        self.assertEqual(path.read_bytes(), ARCHIVE)
        self.assertEqual(path.name, ASSET_NAME)
        self.assertEqual(progress[-1], (len(ARCHIVE), len(ARCHIVE)))
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_downloads_legacy_zip(self) -> None:
        with patch("src.updater._urlopen", return_value=io.BytesIO(ARCHIVE)):
            path = download_release(parse_release(payload("zip"), "arm64"), self.destination)
        self.assertEqual(path.suffix, ".zip")
        self.assertEqual(path.read_bytes(), ARCHIVE)

    def test_removes_failed_or_incomplete_downloads(self) -> None:
        for body in (ARCHIVE[:-1], ARCHIVE + b"extra", b"x" * len(ARCHIVE)):
            with self.subTest(body=body), patch("src.updater._urlopen", return_value=io.BytesIO(body)), self.assertRaises(UpdateError):
                download_release(self.release, self.destination)
            self.assertEqual(list(self.destination.iterdir()), [])

    def test_missing_checksum_never_downloads(self) -> None:
        data = payload()
        data["assets"][0].pop("digest")
        with patch("src.updater._urlopen") as request, self.assertRaises(UpdateError):
            download_release(parse_release(data, "arm64"), self.destination)
        request.assert_not_called()

    def test_reads_legacy_checksum_manifest(self) -> None:
        data = payload()
        data["assets"][0].pop("digest")
        data["assets"].append({
            "name": "SHA256SUMS.txt", "state": "uploaded",
            "browser_download_url": f"https://github.com/{REPOSITORY}/releases/download/{TAG}/SHA256SUMS.txt",
        })
        with patch("src.updater._urlopen", side_effect=[
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
        with patch("src.updater._urlopen") as request, self.assertRaises(DownloadCancelled):
            download_release(self.release, self.destination, cancel=cancel)
        request.assert_not_called()
        cancel.clear()
        with patch("src.updater._urlopen", return_value=io.BytesIO(ARCHIVE)), self.assertRaises(DownloadCancelled):
            download_release(self.release, self.destination, lambda *_: cancel.set(), cancel)
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_stream_network_failure_cleans_partial_file(self) -> None:
        class BrokenResponse(io.BytesIO):
            def read(self, _size=-1):
                raise TimeoutError()

        with patch("src.updater._urlopen", return_value=BrokenResponse()), self.assertRaises(UpdateError):
            download_release(self.release, self.destination)
        self.assertEqual(list(self.destination.iterdir()), [])
