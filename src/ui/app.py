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
    NSAppearance,
    NSAppearanceNameAqua,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSBackingStoreBuffered,
    NSImage,
    NSMakeRect,
    NSMenu,
    NSMenuItem,
    NSScrollView,
    NSSegmentedControl,
    NSStatusBar,
    NSTableColumn,
    NSTableView,
    NSTextAlignmentCenter,
    NSVariableStatusItemLength,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskTitled,
    NSWorkspace,
)
from Foundation import NSURL, NSObject, NSTimer

from src.rewards.points_tracker import PointsTracker
from src.ui import theme
from src.ui.history_view import (
    extract_live_progress,
    format_history_row,
    format_running_row,
    format_summary,
)
from src.ui.scheduler import calculate_next_run, calculate_retry_run, find_pending_retry
from src.ui.update_window import UpdateWindowController
from src.utils.config_loader import load_config
from src.utils.storage import bundled_path, ensure_runtime_dirs, project_path
from src.version import installed_version


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
        self.history_rows: list[dict[str, str]] = []
        self.stat_labels: list[Any] = []
        self.update_controller = None
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
        if self.update_controller is not None:
            self.update_controller.stop()

    def windowShouldClose_(self, sender: Any) -> bool:
        sender.orderOut_(None)
        self._set_runtime_status("已隐藏到菜单栏，点击票券图标可重新打开。")
        return False

    @objc.python_method
    def _create_status_item(self) -> None:
        self.status_item = NSStatusBar.systemStatusBar().statusItemWithLength_(
            NSVariableStatusItemLength
        )
        button = self.status_item.button()
        image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "ticket.fill", "Bing Rewards"
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
        self._add_menu_item(menu, "检查更新…", "checkUpdates:")
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
        frame = NSMakeRect(0, 0, 1000, 720)
        style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
        self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            frame, style, NSBackingStoreBuffered, False
        )
        self.window.setTitle_("Bing Rewards 运行概览")
        self.window.setDelegate_(self)
        self.window.setAppearance_(NSAppearance.appearanceNamed_(NSAppearanceNameAqua))
        self.window.setBackgroundColor_(theme.color(theme.BACKGROUND))
        self.window.setContentView_(theme.panel(frame, theme.BACKGROUND, 0))
        self.window.setHidesOnDeactivate_(False)
        self.window.center()
        content = self.window.contentView()

        app_icon = NSImage.alloc().initWithContentsOfFile_(str(bundled_path("assets/AppIcon.icns")))
        if app_icon is not None:
            NSApp.setApplicationIconImage_(app_icon)
        content.addSubview_(theme.label("Bing Rewards", NSMakeRect(32, 654, 400, 34), 27, True, rounded=True))
        content.addSubview_(theme.label("每天的小积累，都值得期待。", NSMakeRect(33, 626, 440, 22), 13, ink=theme.MUTED))
        content.addSubview_(theme.button("检查更新", NSMakeRect(846, 654, 124, 34), self, "checkUpdates:", symbol="arrow.down.circle"))
        version = theme.label(installed_version().label, NSMakeRect(690, 626, 274, 20), 11, ink=theme.MUTED)
        version.setAlignment_(2)
        content.addSubview_(version)

        # A ticket-colored balance area gives the rewards their own visual identity.
        balance = theme.panel(NSMakeRect(30, 446, 352, 157), theme.MINT)
        content.addSubview_(balance)
        balance.addSubview_(theme.label("累计获得积分", NSMakeRect(22, 113, 190, 22), 13, True, theme.ACCENT))
        self.earned_label = theme.label("+0", NSMakeRect(20, 44, 210, 65), 45, True, theme.INK, True)
        balance.addSubview_(self.earned_label)
        balance.addSubview_(theme.label("每一分，都是今天的小收获", NSMakeRect(22, 20, 260, 20), 11, ink=theme.MUTED))
        balance.addSubview_(theme.mascot(NSMakeRect(212, 22, 132, 132)))

        activity = theme.panel(NSMakeRect(398, 446, 572, 157))
        content.addSubview_(activity)
        activity.addSubview_(theme.label("下次运行", NSMakeRect(24, 115, 280, 20), 12, ink=theme.MUTED))
        self.schedule_label = theme.label("正在安排…", NSMakeRect(23, 79, 350, 34), 25, True, rounded=True)
        activity.addSubview_(self.schedule_label)
        self.runtime_status = theme.label("就绪", NSMakeRect(24, 22, 355, 48), 12, ink=theme.MUTED)
        activity.addSubview_(self.runtime_status)
        self.run_button = theme.button("立即运行", NSMakeRect(411, 86, 139, 38), self, "runNow:", primary=True, symbol="play.fill")
        activity.addSubview_(self.run_button)
        self.login_button = theme.button("登录账号", NSMakeRect(411, 40, 139, 34), self, "loginNow:", symbol="person.crop.circle")
        activity.addSubview_(self.login_button)

        metrics = theme.panel(NSMakeRect(30, 346, 940, 84))
        content.addSubview_(metrics)
        headings = ("运行次数", "成功", "部分失败", "失败")
        inks = (theme.INK, theme.ACCENT, theme.WARNING, theme.ERROR)
        for index, (heading, ink) in enumerate(zip(headings, inks, strict=True)):
            x = 24 + index * 236
            metrics.addSubview_(theme.label(heading, NSMakeRect(x, 48, 190, 18), 12, ink=theme.MUTED))
            value = theme.label("0", NSMakeRect(x, 14, 190, 32), 25, True, ink, True)
            metrics.addSubview_(value)
            self.stat_labels.append(value)
            if index:
                theme.divider(metrics, x - 24, 20, 1, 44)
        self.stat_labels.append(self.earned_label)

        content.addSubview_(theme.label("运行记录", NSMakeRect(32, 301, 155, 25), 17, True))
        self.history_count = theme.label("最近 0 条", NSMakeRect(125, 303, 160, 20), 11, ink=theme.MUTED)
        content.addSubview_(self.history_count)
        self.history_filter = NSSegmentedControl.alloc().initWithFrame_(NSMakeRect(631, 297, 234, 30))
        self.history_filter.setSegmentCount_(3)
        for index, name in enumerate(("全部", "成功", "待关注")):
            self.history_filter.setLabel_forSegment_(name, index)
            self.history_filter.setWidth_forSegment_(72, index)
        self.history_filter.setSelectedSegment_(0)
        self.history_filter.setTarget_(self)
        self.history_filter.setAction_("filterHistory:")
        content.addSubview_(self.history_filter)
        content.addSubview_(theme.button("刷新", NSMakeRect(878, 295, 94, 34), self, "refreshHistory:", symbol="arrow.clockwise"))

        content.addSubview_(theme.panel(NSMakeRect(30, 70, 940, 215)))
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(44, 80, 912, 193))
        scroll.setHasVerticalScroller_(True)
        self.table = NSTableView.alloc().initWithFrame_(scroll.bounds())
        self.table.setDelegate_(self)
        self.table.setDataSource_(self)
        self.table.setBackgroundColor_(theme.color(theme.SURFACE))
        self.table.setRowHeight_(36)
        self.table.setIntercellSpacing_((10, 4))
        self.table.setAllowsColumnReordering_(False)
        self.table.setAllowsColumnResizing_(False)
        columns = (
            ("time", "时间", 130),
            ("status", "状态", 80),
            ("trigger", "来源", 60),
            ("earned", "积分", 62),
            ("completed", "完成任务", 154),
            ("skipped", "跳过任务", 120),
            ("failed", "失败任务", 145),
            ("duration", "耗时", 56),
        )
        for identifier, heading, width in columns:
            column = NSTableColumn.alloc().initWithIdentifier_(identifier)
            column.headerCell().setStringValue_(heading)
            column.setWidth_(width)
            column.headerCell().setFont_(theme.font(11, True))
            column.dataCell().setFont_(theme.font(12))
            column.dataCell().setLineBreakMode_(4)
            self.table.addTableColumn_(column)
        scroll.setDocumentView_(self.table)
        content.addSubview_(scroll)
        self.empty_view = theme.panel(NSMakeRect(45, 83, 908, 157))
        self.empty_title = theme.label("第一份积分，从这里开始", NSMakeRect(220, 86, 468, 28), 17, True)
        self.empty_title.setAlignment_(NSTextAlignmentCenter)
        self.empty_view.addSubview_(self.empty_title)
        self.empty_detail = theme.label("先登录 Microsoft 账号，再点击「立即运行」。", NSMakeRect(170, 52, 568, 24), 13, ink=theme.MUTED)
        self.empty_detail.setAlignment_(NSTextAlignmentCenter)
        self.empty_view.addSubview_(self.empty_detail)
        content.addSubview_(self.empty_view)
        content.addSubview_(theme.label("记录仅保存在本机 · 关闭窗口后继续驻留菜单栏", NSMakeRect(32, 26, 630, 20), 11, ink=theme.MUTED))
        content.addSubview_(theme.button("打开配置", NSMakeRect(754, 19, 104, 32), self, "openConfig:", symbol="slider.horizontal.3"))
        content.addSubview_(theme.button("查看日志", NSMakeRect(870, 19, 104, 32), self, "openLogs:", symbol="doc.text"))

    def numberOfRowsInTableView_(self, _table_view: Any) -> int:
        return len(self.rows)

    def tableView_objectValueForTableColumn_row_(
        self, _table_view: Any, table_column: Any, row: int
    ) -> str:
        identifier = str(table_column.identifier())
        return self.rows[row].get(identifier, "")

    def tableView_willDisplayCell_forTableColumn_row_(
        self, _table: Any, cell: Any, column: Any, row: int
    ) -> None:
        ink = theme.INK
        identifier = str(column.identifier())
        value = self.rows[row].get(identifier, "")
        if identifier == "status":
            ink = {"成功": theme.ACCENT, "部分失败": theme.WARNING, "失败": theme.ERROR,
                   "运行中": theme.ACCENT, "登录中": theme.ACCENT}.get(value, theme.MUTED)
        elif identifier == "earned" and value.startswith("+"):
            ink = theme.ACCENT
        elif identifier in ("time", "trigger", "duration"):
            ink = theme.MUTED
        cell.setTextColor_(theme.color(ink))
        cell.setFont_(theme.font(12, identifier in ("status", "earned")))

    def showDashboard_(self, _sender: Any) -> None:
        self.window.makeKeyAndOrderFront_(None)
        self.window.orderFrontRegardless()
        NSApp.activateIgnoringOtherApps_(True)

    def refreshHistory_(self, _sender: Any) -> None:
        tracker = PointsTracker()
        self.history_rows = [format_history_row(record) for record in tracker.get_history(50)]
        for label, text in zip(
            self.stat_labels, format_summary(tracker.get_summary()), strict=True
        ):
            label.setStringValue_(text.rsplit("\n", 1)[-1])
        self._apply_history_filter()
        if self.process is None:
            latest = self.history_rows[0] if self.history_rows else None
            message = (
                f"最近一次：{latest['time']} · {latest['status']}" if latest else "尚无运行记录"
            )
            self._set_runtime_status(message)

    def filterHistory_(self, _sender: Any) -> None:
        self._apply_history_filter()

    @objc.python_method
    def _apply_history_filter(self) -> None:
        selected = self.history_filter.selectedSegment()
        self.rows = [row for row in self.history_rows if (
            selected == 0 or (selected == 1 and row["status"] == "成功")
            or (selected == 2 and row["status"] != "成功")
        )]
        if self.process is not None and self.process.poll() is None:
            self._read_live_progress()
            self.rows.insert(0, self._running_row())
        self.history_count.setStringValue_(f"最近 {len(self.history_rows)} 条")
        self.empty_view.setHidden_(bool(self.rows))
        filtered = bool(self.history_rows)
        self.empty_title.setStringValue_("没有符合条件的记录" if filtered else "第一份积分，从这里开始")
        self.empty_detail.setStringValue_("切换到「全部」查看其他运行记录。" if filtered else "先登录 Microsoft 账号，再点击「立即运行」。")
        self.table.reloadData()

    def checkUpdates_(self, _sender: Any) -> None:
        if self.update_controller is None:
            self.update_controller = UpdateWindowController.alloc().init()
        self.update_controller.show()

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
        try:
            self.process = subprocess.Popen(
                command,
                stdout=self.process_log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                cwd=str(project_path("data")),
            )
        except OSError as exc:
            self.process_log.close()
            self.process_log = None
            self._set_runtime_status(f"无法启动任务：{exc}")
            if kind == "scheduled":
                self._schedule_next_run()
            return
        self.process_kind = kind
        self.process_started_at = datetime.now()
        self.live_progress = "正在打开登录浏览器" if kind == "login" else "正在启动"
        self.run_button.setEnabled_(False)
        self.login_button.setEnabled_(False)
        self.run_menu_item.setEnabled_(False)
        self.login_menu_item.setEnabled_(False)
        message = "正在打开登录浏览器…" if kind == "login" else "任务运行中…"
        self._set_runtime_status(message)
        self._update_running_row()

    def pollProcess_(self, _timer: Any) -> None:
        if self.process is None:
            if not self.smoke_test:
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
        self.login_button.setEnabled_(True)
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
        self.empty_view.setHidden_(True)
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
        if hasattr(self, "schedule_label"):
            if self.next_run is None:
                text = "正在运行" if self.process is not None else "准备运行"
            else:
                day = "今天" if self.next_run.date() == datetime.now().date() else "明天"
                text = f"{day} {self.next_run:%H:%M}"
                if self.next_run_is_retry:
                    text += f" · 重试 {self.retry_number}"
            self.schedule_label.setStringValue_(text)

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
    switch = "--login-worker" if kind == "login" else "--worker"
    if getattr(sys, "frozen", False):
        command = [sys.executable, switch]
    else:
        # The entry point sets sys.path, even when the worker's cwd is data/.
        entry = Path(__file__).resolve().parents[2] / "scripts/menu_bar_app.py"
        command = [sys.executable, str(entry), switch]
    if kind == "scheduled":
        command.extend(["--mode", "headless", "--trigger", "scheduled"])
    return command


_delegate: MenuBarController | None = None


def run_menu_bar_app(smoke_test: bool = False) -> int:
    global _delegate
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    _delegate = MenuBarController.alloc().initWithSmokeTest_(smoke_test)
    app.setDelegate_(_delegate)
    app.run()
    return 0
