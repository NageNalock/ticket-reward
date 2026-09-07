"""Parse the public GitHub release page and its lazy-loaded asset list."""
from __future__ import annotations

import re
from html.parser import HTMLParser
from urllib.parse import unquote, urljoin, urlsplit

ASSET_NAME = re.compile(r"Bing-Rewards-macOS-(arm64|x86_64|universal2|universal)\.(dmg|zip)")
_VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}
_BLOCK = {"p", "div", "li", "ul", "ol", "br", "pre", "blockquote", "h1", "h2", "h3", "h4"}


class ReleasePageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title = ""
        self.notes = ""
        self.published_at = ""
        self.fragments: list[str] = []
        self.stack: list[str] = []
        self.capture = ""
        self.capture_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        classes = (attributes.get("class") or "").split()
        if not self.capture:
            if tag == "h1" and "d-inline" in classes and not self.title:
                self.capture = "title"
            elif attributes.get("data-test-selector") == "body-content" and "markdown-body" in classes:
                self.capture = "notes"
            if self.capture:
                self.capture_depth = len(self.stack) + 1
                self.parts = []
        if self.capture == "notes" and tag in _BLOCK:
            self.parts.append("\n• " if tag == "li" else "\n")
        if tag == "relative-time" and self.title and not self.capture and not self.published_at:
            self.published_at = attributes.get("datetime") or ""
        if tag == "include-fragment" and not self.capture:
            self.fragments.append(attributes.get("src") or "")
        if tag not in _VOID:
            self.stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag not in self.stack:
            return
        if self.capture == "notes" and tag in _BLOCK:
            self.parts.append("\n")
        index = len(self.stack) - 1 - self.stack[::-1].index(tag)
        del self.stack[index:]
        if self.capture and len(self.stack) < self.capture_depth:
            value = "\n".join(
                text for line in "".join(self.parts).splitlines()
                if (text := " ".join(line.split()))
            )
            setattr(self, self.capture, value[:16000])
            self.capture = ""

    def handle_data(self, data: str) -> None:
        if self.capture and not {"script", "style", "template"}.intersection(self.stack):
            self.parts.append(data)


class ReleaseAssetsParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.assets: list[dict] = []
        self.has_list = False
        self.row: dict = {}
        self.row_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "ul":
            self.has_list = True
        if tag == "li":
            if not self.row_depth:
                self.row = {}
            self.row_depth += 1
        if not self.row_depth:
            return
        if tag == "a":
            url = urljoin("https://github.com", attributes.get("href") or "")
            name = unquote(urlsplit(url).path).rsplit("/", 1)[-1]
            if ASSET_NAME.fullmatch(name) or name == "SHA256SUMS.txt":
                if "name" in self.row:
                    raise ValueError("Ambiguous release asset row")
                self.row.update(name=name, browser_download_url=url, state="uploaded")
        if tag == "clipboard-copy":
            digest = attributes.get("value") or ""
            if re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
                self.row["digest"] = digest

    def handle_endtag(self, tag: str) -> None:
        if tag == "li" and self.row_depth:
            self.row_depth -= 1
            if not self.row_depth and "name" in self.row:
                if any(asset["name"] == self.row["name"] for asset in self.assets):
                    raise ValueError("Duplicate release asset")
                self.assets.append(self.row)

    def close(self) -> None:
        super().close()
        if not self.has_list or self.row_depth:
            raise ValueError("Invalid release asset list")
