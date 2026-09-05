from __future__ import annotations

import queue
import threading
from pathlib import Path
from typing import Any

import objc
from AppKit import (
    NSApp,
    NSAppearance,
    NSAppearanceNameAqua,
    NSBackingStoreBuffered,
    NSMakeRect,
    NSProgressIndicator,
    NSScrollView,
    NSTextView,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
    NSWorkspace,
)
from Foundation import NSURL, NSObject, NSTimer

from src.ui import theme
from src.updater import (
    RELEASES_URL,
    DownloadCancelled,
    Release,
    UpdateError,
    download_release,
    fetch_latest_release,
    is_newer_release,
)
from src.utils.storage import project_path
from src.version import installed_version


class UpdateWindowController(NSObject):
    def init(self):
        self = objc.super(UpdateWindowController, self).init()
        if self is None:
            return None
        self.current = installed_version()
        self.release: Release | None = None
        self.archive: Path | None = None
        self.busy = False
        self.cancel_event = threading.Event()
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.download_progress = (0, 0)
        self._create_window()
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.15, self, "pollEvents:", None, True
        )
        return self

    @objc.python_method
    def _create_window(self) -> None:
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 580, 510),
            NSWindowStyleMaskTitled | NSWindowStyleMaskClosable, NSBackingStoreBuffered, False,
        )
        self.window.setTitle_("软件更新")
        self.window.setReleasedWhenClosed_(False)
        self.window.setAppearance_(NSAppearance.appearanceNamed_(NSAppearanceNameAqua))
        self.window.setBackgroundColor_(theme.color(theme.BACKGROUND))
        self.window.setContentView_(theme.panel(NSMakeRect(0, 0, 580, 510), theme.BACKGROUND, 0))
        self.window.center()
        content = self.window.contentView()
        content.addSubview_(theme.mascot(NSMakeRect(28, 418, 66, 66)))
        content.addSubview_(theme.label("软件更新", NSMakeRect(110, 450, 420, 30), 23, True))
        content.addSubview_(theme.label(f"当前版本  {self.current.label}", NSMakeRect(111, 423, 420, 22), 12, ink=theme.MUTED))
        self.status = theme.label("准备检查更新", NSMakeRect(30, 372, 520, 32), 17, True)
        content.addSubview_(self.status)
        self.detail = theme.label("从 GitHub Releases 获取正式发布包。", NSMakeRect(30, 321, 520, 48), 12, ink=theme.MUTED)
        content.addSubview_(self.detail)
        content.addSubview_(theme.panel(NSMakeRect(28, 120, 524, 194)))
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(42, 130, 496, 172))
        scroll.setHasVerticalScroller_(True)
        self.notes = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 480, 172))
        self.notes.setEditable_(False)
        self.notes.setRichText_(False)
        self.notes.setFont_(theme.font(13))
        self.notes.setTextColor_(theme.color(theme.INK))
        self.notes.setVerticallyResizable_(True)
        self.notes.setHorizontallyResizable_(False)
        self.notes.textContainer().setWidthTracksTextView_(True)
        self.notes.setString_("检查后将在这里显示版本说明。")
        scroll.setDocumentView_(self.notes)
        content.addSubview_(scroll)
        self.progress = NSProgressIndicator.alloc().initWithFrame_(NSMakeRect(30, 94, 520, 8))
        self.progress.setIndeterminate_(False)
        self.progress.setMinValue_(0)
        self.progress.setMaxValue_(100)
        self.progress.setHidden_(True)
        content.addSubview_(self.progress)
        self.release_button = theme.button("发布页面", NSMakeRect(24, 30, 104, 34), self, "openRelease:", symbol="arrow.up.right")
        content.addSubview_(self.release_button)
        self.check_button = theme.button("重新检查", NSMakeRect(304, 30, 108, 34), self, "check:")
        content.addSubview_(self.check_button)
        self.download_button = theme.button("下载更新", NSMakeRect(422, 30, 136, 34), self, "download:", primary=True)
        self.download_button.setEnabled_(False)
        content.addSubview_(self.download_button)

    @objc.python_method
    def show(self) -> None:
        self.window.makeKeyAndOrderFront_(None)
        NSApp.activateIgnoringOtherApps_(True)
        if not self.busy and self.release is None:
            self.check_(None)

    def check_(self, _sender: Any) -> None:
        if self.busy:
            return
        self.busy = True
        self.release = None
        self.archive = None
        self.check_button.setEnabled_(False)
        self.download_button.setEnabled_(False)
        self.download_button.setTitle_("下载更新")
        self.progress.setHidden_(True)
        self.status.setStringValue_("正在检查更新…")
        self.detail.setStringValue_("正在连接 GitHub，请稍候。")
        self.notes.setString_("正在获取版本说明…")

        def work() -> None:
            try:
                self.events.put(("release", fetch_latest_release()))
            except UpdateError as exc:
                self.events.put(("error", str(exc)))

        threading.Thread(target=work, daemon=True, name="release-check").start()

    def download_(self, _sender: Any) -> None:
        if self.busy:
            self.cancel_event.set()
            self.download_button.setEnabled_(False)
            self.detail.setStringValue_("正在取消下载…")
            return
        if self.archive is not None:
            NSWorkspace.sharedWorkspace().activateFileViewerSelectingURLs_([
                NSURL.fileURLWithPath_(str(self.archive))
            ])
            return
        if self.release is None or self.release.asset is None:
            return
        self.busy = True
        self.cancel_event.clear()
        self.check_button.setEnabled_(False)
        self.download_button.setTitle_("取消下载")
        self.status.setStringValue_("正在下载更新…")
        self.progress.setDoubleValue_(0)
        self.progress.setHidden_(False)
        self.download_progress = (0, self.release.asset.size)
        release = self.release

        def report(done: int, total: int) -> None:
            self.download_progress = (done, total)

        def work() -> None:
            try:
                path = download_release(release, project_path("data/updates"), report, self.cancel_event)
                self.events.put(("downloaded", path))
            except DownloadCancelled as exc:
                self.events.put(("cancelled", str(exc)))
            except UpdateError as exc:
                self.events.put(("error", str(exc)))

        threading.Thread(target=work, daemon=True, name="release-download").start()

    def pollEvents_(self, _timer: Any) -> None:
        done, total = self.download_progress
        if self.busy and total and not self.cancel_event.is_set():
            self.progress.setDoubleValue_(done / total * 100)
            self.detail.setStringValue_(f"{done / 1024**2:.1f} / {total / 1024**2:.1f} MB · 下载完成后自动校验 SHA-256")
        try:
            kind, value = self.events.get_nowait()
        except queue.Empty:
            return
        self.busy = False
        self.download_progress = (0, 0)
        self.check_button.setEnabled_(True)
        self.download_button.setTitle_("下载更新")
        if kind == "release":
            self._display_release(value)
        elif kind == "downloaded":
            self.archive = value
            self.status.setStringValue_("下载完成，校验通过")
            instruction = "打开 DMG" if self.archive.suffix.lower() == ".dmg" else "解压 ZIP"
            self.detail.setStringValue_(f"在 Finder 中{instruction}，退出本应用，再将新版拖入「应用程序」替换。登录与运行记录会保留。")
            self.progress.setDoubleValue_(100)
            self.download_button.setTitle_("在 Finder 中显示")
            self.download_button.setEnabled_(True)
        else:
            self.status.setStringValue_("下载已取消" if kind == "cancelled" else "暂时无法完成更新")
            self.detail.setStringValue_(value)
            self.progress.setHidden_(True)
            self.download_button.setEnabled_(self.release is not None and self.release.asset is not None)
            if self.release is None:
                self.notes.setString_("连接恢复后，点击「重新检查」获取版本说明。")

    @objc.python_method
    def _display_release(self, release: Release | None) -> None:
        self.release = release
        if release is None:
            self.status.setStringValue_("还没有正式发布的版本")
            self.detail.setStringValue_("仓库发布首个正式 Release 后，可在这里获取更新。")
            self.notes.setString_("暂无更新说明。")
            return
        newer = is_newer_release(release.tag, self.current)
        self.notes.setString_(f"{release.title}\n{release.published_at}\n\n{release.notes}")
        if newer is False:
            self.status.setStringValue_("已是最新版本")
            self.detail.setStringValue_(f"最新发布：{release.tag}。当前版本相同或更新。")
            return
        self.status.setStringValue_("发现新版本" if newer else "找到最新发布包")
        if release.asset is None:
            self.detail.setStringValue_("此版本没有适用于这台 Mac 的安装包。可打开发布页面查看详情。")
            return
        size = release.asset.size / 1024**2
        prefix = "本地版本无法自动比较，可手动下载。\n" if newer is None else ""
        self.detail.setStringValue_(f"{prefix}{release.tag} · {size:.1f} MB · 下载后校验 SHA-256")
        self.download_button.setEnabled_(True)

    def openRelease_(self, _sender: Any) -> None:
        url = self.release.page_url if self.release else RELEASES_URL
        NSWorkspace.sharedWorkspace().openURL_(NSURL.URLWithString_(url))

    @objc.python_method
    def stop(self) -> None:
        self.cancel_event.set()
        self.timer.invalidate()
