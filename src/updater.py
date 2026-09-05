"""Read GitHub Releases and download a verified macOS distribution archive.

Network work belongs on a background thread. This module never runs an installer
or replaces the running app; the user installs the verified archive in Finder.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import platform
import re
import shutil
import ssl
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen

from src.version import APP_VERSION, REPOSITORY, InstalledVersion

API_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
MAX_ARCHIVE_SIZE = 2 * 1024**3
TIMEOUT = 20


class UpdateError(Exception):
    """An update failure that can be displayed to the user."""


class DownloadCancelled(UpdateError):
    pass


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    size: int
    sha256: str = ""


@dataclass(frozen=True)
class Release:
    tag: str
    title: str
    notes: str
    page_url: str
    published_at: str
    asset: ReleaseAsset | None
    checksum_url: str = ""


def _request(url: str) -> Request:
    return Request(url, headers={
        "Accept": "application/vnd.github+json" if url == API_URL else "application/octet-stream",
        "User-Agent": f"ticket-reward/{APP_VERSION}",
        "X-GitHub-Api-Version": "2022-11-28",
    })


def _open(url: str):
    context = ssl.create_default_context()
    # A frozen Python may retain the CI machine's OpenSSL certificate path.
    # Also load macOS's system CA bundle so HTTPS works on the recipient's Mac.
    system_ca = Path("/etc/ssl/cert.pem")
    if system_ca.is_file():
        context.load_verify_locations(cafile=str(system_ca))
    return urlopen(_request(url), timeout=TIMEOUT, context=context)


def _network_error(exc: Exception) -> UpdateError:
    if isinstance(exc, HTTPError):
        if exc.code in (403, 429):
            return UpdateError("GitHub 暂时限制了请求，请稍后再试。")
        return UpdateError(f"GitHub 请求失败（HTTP {exc.code}），请稍后重试。")
    return UpdateError("无法连接 GitHub，请检查网络连接后重试。")


def _read_small(url: str, limit: int) -> bytes:
    with _open(url) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise UpdateError("更新信息过大，已停止读取。")
    return data


def _github_url(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise UpdateError("发布包缺少有效的 GitHub 地址。")
    parts = urlsplit(value)
    if (
        parts.scheme != "https" or parts.netloc != "github.com"
        or unquote(parts.path) != path or parts.query or parts.fragment
    ):
        raise UpdateError("发布包地址不属于此 GitHub 仓库，已停止更新。")
    return value


def parse_release(payload: object, machine: str) -> Release:
    if not isinstance(payload, dict) or not isinstance(payload.get("tag_name"), str):
        raise UpdateError("GitHub 返回的版本信息无效。")
    tag = payload["tag_name"]
    if not tag or payload.get("draft") or payload.get("prerelease"):
        raise UpdateError("此版本尚未正式发布。")
    page_url = _github_url(payload.get("html_url"), f"/{REPOSITORY}/releases/tag/{tag}")
    architectures = {
        "arm64": ("arm64", "universal2", "universal"),
        "aarch64": ("arm64", "universal2", "universal"),
        "x86_64": ("x86_64", "universal2", "universal"),
        "amd64": ("x86_64", "universal2", "universal"),
    }.get(machine.lower(), ())
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("GitHub 返回的发布包列表无效。")
    by_name = {
        item.get("name"): item for item in assets
        if isinstance(item, dict) and isinstance(item.get("name"), str)
        and item.get("state") == "uploaded"
    }
    selected = next((
        by_name[f"Bing-Rewards-macOS-{arch}.zip"] for arch in architectures
        if f"Bing-Rewards-macOS-{arch}.zip" in by_name
    ), None)
    asset = None
    checksum_url = ""
    if selected is not None:
        name = selected["name"]
        size = selected.get("size")
        if type(size) is not int or not 0 < size <= MAX_ARCHIVE_SIZE:
            raise UpdateError("发布包大小无效，无法下载。")
        url = _github_url(
            selected.get("browser_download_url"), f"/{REPOSITORY}/releases/download/{tag}/{name}"
        )
        digest = selected.get("digest") or ""
        sha256 = ""
        if isinstance(digest, str) and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
            sha256 = digest.partition(":")[2].lower()
        asset = ReleaseAsset(name, url, size, sha256)
        checksum = by_name.get("SHA256SUMS.txt")
        if checksum is not None:
            checksum_url = _github_url(
                checksum.get("browser_download_url"),
                f"/{REPOSITORY}/releases/download/{tag}/SHA256SUMS.txt",
            )
    return Release(
        tag=tag, title=str(payload.get("name") or tag)[:200],
        notes=str(payload.get("body") or "此版本没有提供更新说明。")[:16000],
        page_url=page_url, published_at=str(payload.get("published_at") or "")[:10],
        asset=asset, checksum_url=checksum_url,
    )


def fetch_latest_release(machine: str | None = None) -> Release | None:
    try:
        raw = _read_small(API_URL, 2 * 1024**2)
        return parse_release(json.loads(raw), machine or platform.machine())
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise _network_error(exc) from exc
    except (URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
        raise _network_error(exc) from exc
    except (ValueError, TypeError) as exc:
        raise UpdateError("无法读取 GitHub 版本信息，请稍后重试。") from exc


def is_newer_release(tag: str, current: InstalledVersion) -> bool | None:
    """None means a local/unknown build cannot be ordered, not 'up to date'."""
    if tag == current.release_tag:
        return False
    build = re.fullmatch(r"build-(\d+)-[0-9a-fA-F]{7,40}", tag)
    if build:
        return int(build[1]) > current.build_number if current.build_number else None
    stable = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:\+[\w.-]+)?", tag)
    local = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", current.version)
    if stable and local:
        return tuple(map(int, stable.group(1, 2, 3))) > tuple(map(int, local.groups()))
    return None


def parse_checksum(contents: str, asset_name: str) -> str:
    matches: set[str] = set()
    for line in contents.splitlines():
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?(.+)", line.strip())
        if match and match[2] in (asset_name, f"dist/{asset_name}", f"./{asset_name}"):
            matches.add(match[1].lower())
    if len(matches) != 1:
        raise UpdateError("发布包缺少唯一的 SHA-256 校验值，已停止下载。")
    return matches.pop()


def download_release(
    release: Release, destination: Path,
    progress: Callable[[int, int], None] | None = None,
    cancel: Event | None = None,
) -> Path:
    """Atomically expose a complete, verified ZIP; remove partial files on failure."""
    asset = release.asset
    if asset is None:
        raise UpdateError("此版本没有适用于这台 Mac 的安装包。")
    if not re.fullmatch(r"Bing-Rewards-macOS-(arm64|x86_64|universal2|universal)\.zip", asset.name):
        raise UpdateError("发布包文件名无效。")
    _github_url(asset.url, f"/{REPOSITORY}/releases/download/{release.tag}/{asset.name}")
    directory: Path | None = None
    try:
        if cancel is not None and cancel.is_set():
            raise DownloadCancelled("下载已取消。")
        expected = asset.sha256
        if not expected:
            if not release.checksum_url:
                raise UpdateError("此发布包没有 SHA-256 校验信息，无法安全下载。")
            _github_url(
                release.checksum_url,
                f"/{REPOSITORY}/releases/download/{release.tag}/SHA256SUMS.txt",
            )
            expected = parse_checksum(
                _read_small(release.checksum_url, 64 * 1024).decode("utf-8"), asset.name
            )
        if not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
            raise UpdateError("SHA-256 校验信息无效。")
        destination.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix="release-", dir=destination))
        partial = directory / f"{asset.name}.part"
        total = 0
        digest = hashlib.sha256()
        with _open(asset.url) as response, partial.open("wb") as output:
            while True:
                if cancel is not None and cancel.is_set():
                    raise DownloadCancelled("下载已取消。")
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > asset.size or total > MAX_ARCHIVE_SIZE:
                    raise UpdateError("下载大小与发布信息不符，请重新检查更新。")
                output.write(chunk)
                digest.update(chunk)
                if progress is not None:
                    progress(total, asset.size)
        if cancel is not None and cancel.is_set():
            raise DownloadCancelled("下载已取消。")
        if total != asset.size:
            raise UpdateError("安装包下载不完整，请重试。")
        if digest.hexdigest() != expected.lower():
            raise UpdateError("安装包 SHA-256 校验失败，文件已删除，请重新下载。")
        archive = directory / asset.name
        partial.replace(archive)
        return archive
    except UpdateError:
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)
        raise
    except (URLError, TimeoutError, OSError, ValueError, http.client.HTTPException) as exc:
        if directory is not None:
            shutil.rmtree(directory, ignore_errors=True)
        if isinstance(exc, (URLError, TimeoutError, http.client.HTTPException)):
            raise _network_error(exc) from exc
        raise UpdateError("下载失败，请检查网络、磁盘空间和目录权限后重试。") from exc
