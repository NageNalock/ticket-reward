from __future__ import annotations

import os
import signal
import subprocess
import sys
from contextlib import suppress
from datetime import date, datetime
from pathlib import Path
from typing import Any

import objc
from AppKit import (
    NSApp,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered,
    NSBezelStyleRounded,
    NSButton,
    NSFloatingWindowLevel,
    NSFont,
    NSFontWeightBold,
    NSFontWeightSemibold,
    NSImage,
    NSLineBorder,
    NSMakeRect,
    NSMenu,
    NSMenuItem,
    NSScrollView,
    NSStatusBar,
    NSTableColumn,
    NSTableView,
    NSTextAlignmentCenter,
    NSTextField,
    NSVariableStatusItemLength,
    NSWindow,
    NSWindowCollectionBehaviorCanJoinAllSpaces,
    NSWindowCollectionBehaviorFullScreenAuxiliary,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
    NSWorkspace,
)
from Foundation import NSURL, NSObject, NSTimer

from src.rewards.points_tracker import PointsTracker
from src.ui.history_view import (
    extract_live_progress,
    format_history_row,
    format_running_row,
    format_summary,
)
from src.ui.scheduler import calculate_next_run, calculate_retry_run, find_pending_retry
from src.utils.config_loader import load_config
from src.utils.storage import ensure_runtime_dirs, project_path


class MenuBarController(NSObject):
    def initWithSmokeTest_(self, smoke_test: bool):
        self = objc.super(MenuBarController, self).init()
        if self is None:
            return None
        self.smoke_test = smoke_test
        self.process: subprocess.Popen[bytes] | None = None
        self.process_kind = ""
        self.process_log: Any = None
        self.process_log_path: Path | None = None
        self.process_log_offset = 0
        self.process_started_at: datetime | None = None
        self.live_progress = ""
        self.rows: list[dict[str, str]] = []
        self.stat_labels: list[Any] = []
        self.next_run: datetime | None = None
        self.next_run_is_retry = False
        self.retry_number = 0
        self.retry_cycle_date: date | None = None
        self.active_retry_number = 0
        self.active_schedule_date: date | None = None
        return self

    def applicationDidFinishLaunching_(self, _notification: Any) -> None:
        ensure_runtime_dirs()
        self._create_status_item()
        self._create_window()
        self._schedule_next_run()
        self._recover_pending_retry()
        self.refreshHistory_(None)
        self.showDashboard_(None)
        self.poll_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0, self, "pollProcess:", None, True
        )
        if self.smoke_test:
            self.performSelector_withObject_afterDelay_("quitForSmoke:", None, 1.5)

    def applicationShouldTerminateAfterLastWindowClosed_(self, _application: Any) -> bool:
        return False

    def applicationWillTerminate_(self, _notification: Any) -> None:
        self._stop_child()

    def windowShouldClose_(self, sender: Any) -> bool:
        sender.orderOut_(None)
        self._set_runtime_status("已隐藏到菜单栏，点击礼物图标可重新打开。")
        return False

    @objc.python_method
    def _create_status_item(self) -> None:
        self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength
        )
        button = self.status_item.button()
        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "gift.fill", "Bing Rewards"
        )
        if image is not None:
            image.setTemplate_(True)
            button.setImage_(image)
        else:
            button.setTitle_("B")
        button.setToolTip_("Bing Rewards")

        menu = NSMenu.alloc().init()
        self._add_menu_item(menu, "打开运行概览", "showDashboard:")
        self.run_menu_item = self._add_menu_item(menu, "立即运行", "runNow:")
        self.login_menu_item = self._add_menu_item(menu, "登录 Microsoft 账号", "loginNow:")
        self.schedule_menu_item = self._add_menu_item(menu, "下次运行：计算中", None)
        self.schedule_menu_item.setEnabled_(False)
        menu.addItem_(NSMenuItem.separatorItem())
        self._add_menu_item(menu, "刷新记录", "refreshHistory:")
        self._add_menu_item(menu, "打开日志目录", "openLogs:")
        self._add_menu_item(menu, "打开配置", "openConfig:")
        menu.addItem_(NSMenuItem.separatorItem())
        self._add_menu_item(menu, "显式退出", "quitApplication:")
        self.status_item.setMenu_(menu)

    @objc.python_method
    def _add_menu_item(self, menu: Any, title: str, action: str | None) -> Any:
        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, "")
        item.setTarget_(self)
        menu.addItem_(item)
        return item

    @objc.python_method
    def _create_window(self) -> None:
        frame = NSMakeRect(0, 0, 860, 550)
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, style, NSBackingStoreBuffered, False
        )
        self.window.setTitle_("Bing Rewards 运行概览")
        self.window.setDelegate_(self)
        self.window.setLevel_(NSFloatingWindowLevel)
        self.window.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorFullScreenAuxiliary
        )
        self.window.setHidesOnDeactivate_(False)
        self.window.center()
        content = self.window.contentView()

        title = self._label("Bing Rewards", NSMakeRect(24, 500, 500, 32), 24, True)
        content.addSubview_(title)
        subtitle = self._label(
            "运行记录保存在本机；关闭窗口后仍会驻留菜单栏并等待下次定时运行。",
            NSMakeRect(25, 476, 700, 20),
            12,
            False,
        )
        content.addSubview_(subtitle)

        stat_width = 154
        for index in range(5):
            stat_box = self._label("—", NSMakeRect(24 + index * 164, 414, stat_width, 52), 15, True)
            stat_box.setAlignment_(NSTextAlignmentCenter)
            stat_box.setBezeled_(True)
            stat_box.setBordered_(True)
            stat_box.setEditable_(False)
            stat_box.setSelectable_(False)
            content.addSubview_(stat_box)
            self.stat_labels.append(stat_box)

        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(24, 94, 812, 304))
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(NSLineBorder)
        self.table = NSTableView.alloc().initWithFrame_(scroll.bounds())
        self.table.setDelegate_(self)
        self.table.setDataSource_(self)
        self.table.setUsesAlternatingRowBackgroundColors_(True)
        columns = (
            ("time", "时间", 120),
            ("status", "状态", 72),
            ("trigger", "来源", 62),
            ("earned", "积分", 54),
            ("completed", "完成任务", 170),
            ("failed", "失败任务", 175),
            ("duration", "耗时", 56),
        )
        for identifier, heading, width in columns:
            column = NSTableColumn.alloc().initWithIdentifier_(identifier)
            column.headerCell().setStringValue_(heading)
            column.setWidth_(width)
            self.table.addTableColumn_(column)
        scroll.setDocumentView_(self.table)
        content.addSubview_(scroll)

        self.run_button = self._button("立即运行", NSMakeRect(24, 46, 100, 32), "runNow:")
        content.addSubview_(self.run_button)
        content.addSubview_(self._button("刷新", NSMakeRect(132, 46, 82, 32), "refreshHistory:"))
        content.addSubview_(self._button("登录账号", NSMakeRect(222, 46, 96, 32), "loginNow:"))
        content.addSubview_(self._button("查看日志", NSMakeRect(326, 46, 96, 32), "openLogs:"))
        self.runtime_status = self._label("就绪", NSMakeRect(438, 51, 398, 22), 12, False)
        self.runtime_status.setAlignment_(2)
        content.addSubview_(self.runtime_status)

    @objc.python_method
    def _label(self, text: str, frame: Any, size: float, bold: bool) -> Any:
        label = NSTextField.alloc().initWithFrame_(frame)
        label.setStringValue_(text)
        label.setBezeled_(False)
        label.setDrawsBackground_(False)
        label.setEditable_(False)
        label.setSelectable_(False)
        weight = NSFontWeightBold if bold else NSFontWeightSemibold
        label.setFont_(NSFont.systemFontOfSize_weight_(size, weight))
        return label

    @objc.python_method
    def _button(self, title: str, frame: Any, action: str) -> Any:
        button = NSButton.alloc().initWithFrame_(frame)
        button.setTitle_(title)
        button.setBezelStyle_(NSBezelStyleRounded)
        button.setTarget_(self)
        button.setAction_(action)
        return button

    def numberOfRowsInTableView_(self, _table_view: Any) -> int:
        return len(self.rows)

    def tableView_objectValueForTableColumn_row_(
        self, _table_view: Any, table_column: Any, row: int
    ) -> str:
        identifier = str(table_column.identifier())
        return self.rows[row].get(identifier, "")

    def showDashboard_(self, _sender: Any) -> None:
        self.window.makeKeyAndOrderFront_(None)
        self.window.orderFrontRegardless()
        NSApp.activateIgnoringOtherApps_(True)

    def refreshHistory_(self, _sender: Any) -> None:
        tracker = PointsTracker()
        self.rows = [format_history_row(record) for record in tracker.get_history(50)]
        if self.process is not None and self.process.poll() is None:
            self._read_live_progress()
            self.rows.insert(0, self._running_row())
        for label, text in zip(
            self.stat_labels, format_summary(tracker.get_summary()), strict=True
        ):
            label.setStringValue_(text)
        self.table.reloadData()
        if self.process is None:
            latest = self.rows[0] if self.rows else None
            message = (
                f"最近一次：{latest['time']} · {latest['status']}" if latest else "尚无运行记录"
            )
            self._set_runtime_status(message)

    def runNow_(self, _sender: Any) -> None:
        self._start_child("run")

    def loginNow_(self, _sender: Any) -> None:
        self._start_child("login")

    @objc.python_method
    def _start_child(self, kind: str) -> None:
        if self.process is not None and self.process.poll() is None:
            self._set_runtime_status("已有任务正在运行。")
            return

        command = _worker_command(kind)
        log_path = project_path("data/logs/menu-bar-worker.log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        self.process_log_path = log_path
        self.process_log_offset = log_path.stat().st_size if log_path.exists() else 0
        self.process_log = log_path.open("ab", buffering=0)
        self.process = subprocess.Popen(
            command,
            stdout=self.process_log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            cwd=str(project_path("data")),
        )
        self.process_kind = kind
        self.process_started_at = datetime.now()
        self.live_progress = "正在打开登录浏览器" if kind == "login" else "正在启动"
        self.run_button.setEnabled_(False)
        self.run_menu_item.setEnabled_(False)
        self.login_menu_item.setEnabled_(False)
        message = "正在打开登录浏览器…" if kind == "login" else "任务运行中…"
        self._set_runtime_status(message)
        self._update_running_row()

    def pollProcess_(self, _timer: Any) -> None:
        if self.process is None:
            self._maybe_start_scheduled_run()
            return
        return_code = self.process.poll()
        if return_code is None:
            self._update_running_row()
            return

        kind = self.process_kind
        active_retry_number = self.active_retry_number
        active_schedule_date = self.active_schedule_date
        self.process = None
        self.process_kind = ""
        self.process_started_at = None
        self.process_log_path = None
        self.process_log_offset = 0
        self.live_progress = ""
        self.active_retry_number = 0
        self.active_schedule_date = None
        if self.process_log is not None:
            self.process_log.close()
            self.process_log = None
        self.run_button.setEnabled_(True)
        self.run_menu_item.setEnabled_(True)
        self.login_menu_item.setEnabled_(True)
        self.refreshHistory_(None)
        action = "登录" if kind == "login" else "任务"
        result = "完成" if return_code == 0 else f"失败（退出码 {return_code}）"
        retry_scheduled = False
        if kind == "scheduled":
            if return_code != 0 and not project_path("data/login_expired.flag").exists():
                retry_scheduled = self._schedule_retry(
                    active_schedule_date or datetime.now().date(),
                    active_retry_number + 1,
                )
            if not retry_scheduled:
                self._schedule_next_run()
        elif kind == "run" and return_code == 0 and self.next_run_is_retry:
            # A successful manual run has already repaired today's failed cycle.
            self._schedule_next_run()

        if retry_scheduled:
            self._set_runtime_status(
                f"{action}{result}，将于 {self.next_run:%H:%M} 重试"
            )
        elif kind == "scheduled" and project_path("data/login_expired.flag").exists():
            self._set_runtime_status("登录已失效，请重新登录后手动运行。")
        else:
            self._set_runtime_status(f"{action}{result}")

    @objc.python_method
    def _read_live_progress(self) -> None:
        if self.process_log_path is None or not self.process_log_path.exists():
            return
        try:
            with self.process_log_path.open("rb") as handle:
                handle.seek(self.process_log_offset)
                chunk = handle.read()
                self.process_log_offset = handle.tell()
            candidate = extract_live_progress(chunk.decode("utf-8", errors="replace"))
            if candidate:
                self.live_progress = candidate
        except OSError:
            return

    @objc.python_method
    def _running_row(self) -> dict[str, str]:
        started = self.process_started_at or datetime.now()
        elapsed = max(0, int((datetime.now() - started).total_seconds()))
        trigger = "scheduled" if self.process_kind == "scheduled" else "ui"
        return format_running_row(
            started.isoformat(timespec="seconds"),
            trigger,
            self.live_progress or "正在启动",
            elapsed,
            login=self.process_kind == "login",
        )

    @objc.python_method
    def _update_running_row(self) -> None:
        if self.process is None or self.process.poll() is not None:
            return
        self._read_live_progress()
        row = self._running_row()
        if self.rows and self.rows[0].get("_running") == "1":
            self.rows[0] = row
        else:
            self.rows.insert(0, row)
        self.table.reloadData()
        self._set_runtime_status(f"{row['completed']} · 已运行 {row['duration']}")

    @objc.python_method
    def _schedule_next_run(self) -> None:
        schedule = load_config()["schedule"]
        self.next_run = calculate_next_run(
            datetime.now(),
            int(schedule["run_hour"]),
            int(schedule["run_minute"]),
            int(schedule["jitter_sec"]),
        )
        self.next_run_is_retry = False
        self.retry_number = 0
        self.retry_cycle_date = self.next_run.date()
        self._update_schedule_menu()

    @objc.python_method
    def _retry_policy(self) -> tuple[int, int]:
        schedule = load_config()["schedule"]
        return int(schedule.get("retry_count", 3)), int(
            schedule.get("retry_interval_min", 30)
        )

    @objc.python_method
    def _update_schedule_menu(self) -> None:
        if hasattr(self, "schedule_menu_item"):
            if self.next_run is None:
                title = "下次运行：计算中"
            elif self.next_run_is_retry:
                retry_count, _interval = self._retry_policy()
                title = (
                    f"下次运行：{self.next_run:%m-%d %H:%M}"
                    f"（重试 {self.retry_number}/{retry_count}）"
                )
            else:
                title = f"下次运行：{self.next_run:%m-%d %H:%M}"
            self.schedule_menu_item.setTitle_(title)

    @objc.python_method
    def _schedule_retry(self, cycle_date: date, retry_number: int) -> bool:
        retry_count, retry_interval_min = self._retry_policy()
        if retry_number > retry_count:
            return False
        candidate = calculate_retry_run(datetime.now(), cycle_date, retry_interval_min)
        if candidate is None:
            return False
        self.next_run = candidate
        self.next_run_is_retry = True
        self.retry_number = retry_number
        self.retry_cycle_date = cycle_date
        self._update_schedule_menu()
        return True

    @objc.python_method
    def _recover_pending_retry(self) -> None:
        if project_path("data/login_expired.flag").exists():
            return
        retry_count, retry_interval_min = self._retry_policy()
        plan = find_pending_retry(
            datetime.now(),
            PointsTracker().get_history(200),
            retry_count,
            retry_interval_min,
        )
        if plan is None:
            return
        self.next_run = plan.run_at
        self.next_run_is_retry = True
        self.retry_number = plan.retry_number
        self.retry_cycle_date = datetime.now().date()
        self._update_schedule_menu()

    @objc.python_method
    def _maybe_start_scheduled_run(self) -> None:
        if self.next_run is None or datetime.now() < self.next_run:
            return
        if self.next_run_is_retry and self.retry_cycle_date != datetime.now().date():
            self._schedule_next_run()
            return
        self.active_retry_number = self.retry_number if self.next_run_is_retry else 0
        self.active_schedule_date = self.retry_cycle_date or self.next_run.date()
        self.next_run = None
        self._update_schedule_menu()
        self._start_child("scheduled")

    def openLogs_(self, _sender: Any) -> None:
        self._open_path(project_path("data/logs"))

    def openConfig_(self, _sender: Any) -> None:
        self._open_path(project_path("config/config.yaml"))

    @objc.python_method
    def _open_path(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        NSWorkspace.sharedWorkspace().openURL_(NSURL.fileURLWithPath_(str(path)))

    @objc.python_method
    def _set_runtime_status(self, message: str) -> None:
        self.runtime_status.setStringValue_(message)
        self.status_item.button().setToolTip_(f"Bing Rewards · {message}")

    def quitApplication_(self, _sender: Any) -> None:
        self._stop_child()
        NSApp.terminate_(None)

    def quitForSmoke_(self, _sender: Any) -> None:
        self.window.performClose_(None)
        if self.window.isVisible() or self.status_item.button() is None:
            os._exit(3)
        print("UI smoke passed: close hides window and status item stays alive", flush=True)
        NSApp.terminate_(None)

    @objc.python_method
    def _stop_child(self) -> None:
        if self.process is not None and self.process.poll() is None:
            with suppress(OSError):
                os.killpg(self.process.pid, signal.SIGTERM)
        if self.process_log is not None:
            self.process_log.close()
            self.process_log = None


def _worker_command(kind: str) -> list[str]:
    if getattr(sys, "frozen", False):
        switch = "--login-worker" if kind == "login" else "--worker"
        if kind == "scheduled":
            return [sys.executable, switch, "--mode", "headless", "--trigger", "scheduled"]
        return [sys.executable, switch]
    if kind == "login":
        return [sys.executable, "-m", "src.login", "--auto"]
    trigger = "scheduled" if kind == "scheduled" else "ui"
    return [
        sys.executable,
        "-m",
        "src.main",
        "--mode",
        "headless",
        "--trigger",
        trigger,
    ]


_delegate: MenuBarController | None = None


def run_menu_bar_app(smoke_test: bool = False) -> int:
    global _delegate
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    _delegate = MenuBarController.alloc().initWithSmokeTest_(smoke_test)
    app.setDelegate_(_delegate)
    app.run()
    return 0
