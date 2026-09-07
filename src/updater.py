"""Read GitHub Releases and download a verified macOS installer.

Network work belongs on a background thread. This module never runs an installer
or replaces the running app; the user installs the verified package in Finder.
"""
from __future__ import annotations

import hashlib
import http.client
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
from urllib.parse import quote, unquote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from src.utils.release_pages import ReleaseAssetsParser, ReleasePageParser
from src.version import APP_VERSION, REPOSITORY, InstalledVersion

RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
LATEST_URL = f"{RELEASES_URL}/latest"
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


def _request(url: str, *, method: str = "GET", html: bool = False) -> Request:
    return Request(url, headers={
        "Accept": "text/html" if html else "application/octet-stream",
        "User-Agent": f"ticket-reward/{APP_VERSION}",
    }, method=method)


class _KeepHeadRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        # Python versions that reset HEAD to GET would start a CDN download
        # while we only want Content-Length. Preserve HEAD across every hop.
        if redirected is not None and req.get_method() == "HEAD":
            redirected.method = "HEAD"
        return redirected


def _urlopen(request: Request, *, timeout: int, context: ssl.SSLContext):
    opener = build_opener(_KeepHeadRedirects(), HTTPSHandler(context=context))
    return opener.open(request, timeout=timeout)


def _open(url: str, *, method: str = "GET", html: bool = False):
    context = ssl.create_default_context()
    # A frozen Python may retain the CI machine's OpenSSL certificate path.
    # Also load macOS's system CA bundle so HTTPS works on the recipient's Mac.
    system_ca = Path("/etc/ssl/cert.pem")
    if system_ca.is_file():
        context.load_verify_locations(cafile=str(system_ca))
    return _urlopen(_request(url, method=method, html=html), timeout=TIMEOUT, context=context)


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


def _select_asset(assets: list, machine: str) -> dict | None:
    architectures = {
        "arm64": ("arm64", "universal2", "universal"),
        "aarch64": ("arm64", "universal2", "universal"),
        "x86_64": ("x86_64", "universal2", "universal"),
        "amd64": ("x86_64", "universal2", "universal"),
    }.get(machine.lower(), ())
    by_name = {
        item.get("name"): item for item in assets
        if isinstance(item, dict) and isinstance(item.get("name"), str)
        and item.get("state") == "uploaded"
    }
    return next((
        by_name[f"Bing-Rewards-macOS-{arch}.{extension}"]
        for extension in ("dmg", "zip") for arch in architectures
        if f"Bing-Rewards-macOS-{arch}.{extension}" in by_name
    ), None)


def parse_release(payload: object, machine: str) -> Release:
    """Validate normalized page metadata before exposing a download."""
    if not isinstance(payload, dict) or not isinstance(payload.get("tag_name"), str):
        raise UpdateError("GitHub 返回的版本信息无效。")
    tag = payload["tag_name"]
    if not tag or payload.get("draft") or payload.get("prerelease"):
        raise UpdateError("此版本尚未正式发布。")
    page_url = _github_url(payload.get("html_url"), f"/{REPOSITORY}/releases/tag/{tag}")
    assets = payload.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("GitHub 返回的发布包列表无效。")
    selected = _select_asset(assets, machine)
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
        checksum = next((item for item in assets if isinstance(item, dict)
                         and item.get("name") == "SHA256SUMS.txt"
                         and item.get("state") == "uploaded"), None)
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
    """Read public HTML and HEAD metadata only; never GET an installer here."""
    try:
        try:
            response = _open(LATEST_URL, html=True)
        except HTTPError as exc:
            if exc.code == 404:
                return None
            raise
        with response:
            page_url = response.geturl()
            prefix = f"/{REPOSITORY}/releases/tag/"
            path = unquote(urlsplit(page_url).path)
            if not path.startswith(prefix):
                raise UpdateError("无法从 GitHub 发布页确定最新版本。")
            tag = path[len(prefix):]
            if not tag or len(tag) > 200 or any(part in (".", "..") for part in tag.split("/")):
                raise UpdateError("GitHub 返回的版本标签无效。")
            _github_url(page_url, f"{prefix}{tag}")
            raw = response.read(2 * 1024**2 + 1)
        if len(raw) > 2 * 1024**2:
            raise UpdateError("更新信息过大，已停止读取。")
        page = ReleasePageParser()
        page.feed(raw.decode("utf-8"))
        page.close()
        assets_url = f"{RELEASES_URL}/expanded_assets/{quote(tag, safe='')}"
        if unquote(assets_url) not in (unquote(urljoin(page_url, fragment)) for fragment in page.fragments):
            raise UpdateError("GitHub 发布页格式已变化，请打开发布页查看更新。")
        with _open(assets_url, html=True) as response:
            _github_url(response.geturl(), f"/{REPOSITORY}/releases/expanded_assets/{tag}")
            raw = response.read(2 * 1024**2 + 1)
        if len(raw) > 2 * 1024**2:
            raise UpdateError("更新信息过大，已停止读取。")
        listing = ReleaseAssetsParser()
        listing.feed(raw.decode("utf-8"))
        listing.close()
        architecture = machine or platform.machine()
        selected = _select_asset(listing.assets, architecture)
        if selected is not None:
            url = _github_url(selected["browser_download_url"],
                              f"/{REPOSITORY}/releases/download/{tag}/{selected['name']}")
            # The web page rounds sizes to MB. HEAD gets the exact length without
            # transferring any archive bytes, including across GitHub's CDN redirect.
            with _open(url, method="HEAD") as response:
                length = response.headers.get("Content-Length", "")
            if not re.fullmatch(r"[0-9]{1,10}", length):
                raise UpdateError("无法读取发布包大小，请稍后重试。")
            selected["size"] = int(length)
        return parse_release({
            "tag_name": tag, "html_url": page_url, "name": page.title,
            "body": page.notes, "published_at": page.published_at, "assets": listing.assets,
        }, architecture)
    except HTTPError as exc:
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
    """Expose a verified DMG (or legacy ZIP); remove partial files on failure."""
    asset = release.asset
    if asset is None:
        raise UpdateError("此版本没有适用于这台 Mac 的安装包。")
    if not re.fullmatch(r"Bing-Rewards-macOS-(arm64|x86_64|universal2|universal)\.(dmg|zip)", asset.name):
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
