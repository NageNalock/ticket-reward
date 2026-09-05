from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Any

from loguru import logger
from playwright.sync_api import Error as PlaywrightError

from src.utils.storage import project_path

POINT_PATTERN = re.compile(r"(?<!\+)\b(\d{1,3}(?:[,\s.]\d{3})+|\d+)\b")
TASK_POINT_PATTERN = re.compile(r"\+\s*(\d+)")
POINT_LABEL_PATTERN = re.compile(
    r"^(?:我的积分|积分余额|my points|points(?:\s+balance)?)$",
    re.IGNORECASE,
)
STATUS_TEXT_PATTERN = re.compile(
    r"^(?:正在进行中[!！]?|你已获得\s*\d[\d,\.]*\s*积分[!！]?|"
    r"you're on track for star bonus points)$",
    re.IGNORECASE,
)


@dataclass
class TaskCard:
    title: str
    description: str
    points: int
    task_type: str
    element: Any
    available: bool = True
    completed: bool = False


def classify_task(title: str, description: str = "", href: str = "") -> str:
    content = f"{title} {description}".lower()
    normalized_href = href.lower()
    if any(word in content for word in ("推荐", "朋友", "refer", "friend", "邀请")):
        return "referral"
    if any(word in content for word in ("拼图", "puzzle", "排列图块", "jigsaw")):
        return "puzzle"
    if any(word in content for word in ("测验", "quiz", "问答", "答题", "测试", "poll")):
        return "quiz"
    if any(word in content for word in ("视频", "video", "观看", "watch")):
        return "video"
    if "bing.com/search" in normalized_href or "search?q=" in normalized_href:
        return "keyword_search"
    return "unknown"


def extract_current_points(text: str) -> int | None:
    """Extract a balance only when it is adjacent to an explicit balance label."""
    normalized = text.replace("，", ",")
    labelled_patterns = (
        r"([\d,\.\s]+)\s*(?:我的积分|积分余额|points(?:\s+balance)?)",
        r"(?:我的积分|积分余额|points(?:\s+balance)?)\s*([\d,\.\s]+)",
    )
    for pattern in labelled_patterns:
        match = re.search(pattern, normalized, re.IGNORECASE)
        if match:
            prefix = normalized[max(0, match.start(1) - 2) : match.start(1)]
            if "+" in prefix:
                continue
            digits = re.sub(r"\D", "", match.group(1))
            if digits:
                return int(digits)

    return None


def extract_task_points(text: str, aria_label: str = "") -> int:
    """Extract an offer value from a +N chip or its scoped points aria-label."""
    match = TASK_POINT_PATTERN.search(text)
    if match:
        return int(match.group(1))
    labelled = re.fullmatch(
        r"\s*(\d[\d,\s.]*)\s*(?:积分|points?)\s*",
        aria_label,
        re.IGNORECASE,
    )
    if labelled:
        return int(re.sub(r"\D", "", labelled.group(1)))
    return 0


def offer_is_completed(aria_label: str) -> bool:
    """Read Bing Rewards' authoritative offer state from the card label."""
    normalized = re.sub(r"\s+", " ", aria_label).strip().lower()
    if "not completed" in normalized or "未完成" in normalized:
        return False
    return bool(re.search(r"\boffer(?:\s+is)?\s+completed\b", normalized)) or (
        "已完成" in normalized
    )


class PanelParser:
    REWARDS_BUTTON_SELECTORS = (
        "#id_rh",
        "[aria-label*='Microsoft Rewards' i]",
        "[title*='Microsoft Rewards' i]",
        "[data-bm*='Rewards']",
        "[href*='rewards.bing.com']",
    )
    CARD_SELECTORS = (
        "[data-bi-id*='offer' i]",
        "[data-bi-cn*='offer' i]",
        "[class*='offer' i]",
        "[class*='task' i]",
        "[class*='card' i]",
        "a[href*='rewards']",
    )
    DAILY_CARD_SELECTOR = "#daily_set_card .promo_cont[role='banner']"

    def __init__(self, page: Any):
        self.page = page

    def open_panel(self) -> bool:
        for _ in range(12):
            if any(
                frame is not self.page.main_frame and "reward" in frame.url.lower()
                for frame in self.page.frames
            ):
                return True
            for selector in self.REWARDS_BUTTON_SELECTORS:
                candidates = self.page.locator(selector)
                for index in range(min(candidates.count(), 5)):
                    candidate = candidates.nth(index)
                    try:
                        if candidate.is_visible():
                            candidate.click(timeout=8_000)
                            self.page.wait_for_timeout(2_000)
                            return True
                    except Exception:
                        continue
            self.page.wait_for_timeout(500)
        logger.warning("未找到 Microsoft Rewards 入口")
        return False

    def _root(self) -> Any:
        for frame in self.page.frames:
            if frame is not self.page.main_frame and "reward" in frame.url.lower():
                return frame
        return self.page

    def get_current_points(self, *, timeout_ms: int = 0) -> int | None:
        """Wait for a labelled balance, including a late or replaced Rewards frame."""
        deadline = monotonic() + max(0, timeout_ms) / 1_000
        while True:
            try:
                value = self._get_current_points_once()
                if value is not None:
                    return value
            except PlaywrightError as exc:
                # A frame can be replaced while the first authenticated page loads.
                logger.debug(f"积分内容暂不可读取 ({type(exc).__name__})")
            remaining_ms = (deadline - monotonic()) * 1_000
            if remaining_ms <= 0:
                return None
            self.page.wait_for_timeout(min(500, remaining_ms))

    def _get_current_points_once(self) -> int | None:
        roots = [self._root()]
        if roots[0] is not self.page:
            roots.append(self.page)
        for root in roots:
            labels = root.get_by_text(POINT_LABEL_PATTERN, exact=True)
            for index in range(min(labels.count(), 5)):
                label = labels.nth(index)
                try:
                    node = label
                    for _ in range(4):
                        node = node.locator("xpath=..")
                        text = node.inner_text(timeout=1_500).strip()
                        if len(text) > 120:
                            break
                        value = extract_current_points(text)
                        if value is not None:
                            return value
                except Exception:
                    continue

            header = root.locator("#id_rh").first
            if header.count():
                try:
                    metadata = " ".join(
                        filter(
                            None,
                            (
                                header.inner_text(timeout=1_500),
                                header.get_attribute("aria-label") or "",
                                header.get_attribute("title") or "",
                            ),
                        )
                    )
                    value = extract_current_points(metadata)
                    if value is not None:
                        return value
                except Exception:
                    pass
        return None

    def parse_daily_tasks(self) -> list[TaskCard]:
        """Parse only the three authoritative cards inside Bing's Daily Set."""
        cards = self._root().locator(self.DAILY_CARD_SELECTOR)
        tasks: list[TaskCard] = []
        for index in range(min(cards.count(), 10)):
            card = cards.nth(index)
            try:
                if not card.is_visible():
                    continue
                aria_label = card.get_attribute("aria-label") or ""
                if "offer" not in aria_label.lower() and "完成" not in aria_label:
                    continue
                text = card.inner_text(timeout=1_500).strip()
                task = self._parse_card(card, text)
                if task.title and not STATUS_TEXT_PATTERN.fullmatch(task.title.strip()):
                    tasks.append(task)
            except Exception:
                continue
        return tasks

    def daily_set_is_complete(self) -> bool:
        root = self._root()
        daily_set = root.locator("#daily_set_card").first
        if not daily_set.count():
            return False
        completion = daily_set.locator(
            ".dset_completion_comp, .threeOffers_header.complete, "
            "[class~='complete'][aria-labelledby='DailySet']"
        ).first
        return bool(completion.count() and completion.is_visible())

    def daily_task_state(self, title: str) -> str:
        """Return completed/pending/unknown from the freshly loaded Daily Set."""
        tasks = self.parse_daily_tasks()
        for task in tasks:
            if task.title == title:
                return "completed" if task.completed else "pending"
        if self.daily_set_is_complete() or tasks:
            # Bing removes each card as soon as it is credited. Other remaining cards,
            # or the final Daily Set completion panel, prove the target was processed.
            return "completed"
        return "unknown"

    def parse_tasks(self) -> list[TaskCard]:
        root = self._root()
        parsed = self.parse_daily_tasks()
        fingerprints = {re.sub(r"\s+", " ", task.title).strip().lower() for task in parsed}

        for selector in self.CARD_SELECTORS:
            cards = root.locator(selector)
            for index in range(min(cards.count(), 100)):
                card = cards.nth(index)
                try:
                    if not card.is_visible():
                        continue
                    text = card.inner_text(timeout=1_500).strip()
                    if not text or not self._looks_like_task(text):
                        continue
                    candidate = self._parse_card(card, text)
                    fingerprint = re.sub(r"\s+", " ", candidate.title).strip().lower()
                    if fingerprint in fingerprints:
                        continue
                    fingerprints.add(fingerprint)
                    parsed.append(candidate)
                except Exception:
                    continue
        return parsed

    @staticmethod
    def _looks_like_task(text: str) -> bool:
        lowered = text.lower()
        if len(text) > 600 or len(text.splitlines()) > 12:
            return False
        title = next((line.strip() for line in text.splitlines() if line.strip()), "")
        if STATUS_TEXT_PATTERN.fullmatch(title):
            return False
        point_matches = TASK_POINT_PATTERN.findall(text)
        if len(point_matches) > 1:
            return False
        return bool(point_matches) or any(
            word in lowered
            for word in (
                "拼图",
                "puzzle",
                "测验",
                "quiz",
                "推荐",
                "refer",
            )
        )

    def _parse_card(self, card: Any, text: str) -> TaskCard:
        title_node = card.locator(".promo-title").first
        description_node = card.locator(".promo-desc").first
        title = title_node.inner_text(timeout=1_500).strip() if title_node.count() else ""
        description = (
            description_node.inner_text(timeout=1_500).strip() if description_node.count() else ""
        )
        if not title:
            lines = self._content_lines(text)
            title = lines[0] if lines else "未命名任务"
            description = description or (lines[1] if len(lines) > 1 else "")

        point_chip = card.locator(".point_cont [aria-label], .point_cont .point").first
        point_aria = point_chip.get_attribute("aria-label") or "" if point_chip.count() else ""
        points = extract_task_points(text, point_aria)

        href = card.get_attribute("href") or ""
        if not href:
            link = card.locator("a[href]").first
            if link.count():
                href = link.get_attribute("href") or ""

        metadata = " ".join(
            filter(
                None,
                (
                    text,
                    card.get_attribute("aria-label") or "",
                    card.get_attribute("class") or "",
                ),
            )
        ).lower()
        aria_label = card.get_attribute("aria-label") or ""
        completed = offer_is_completed(aria_label)
        if not aria_label:
            completed = any(word in metadata for word in ("已完成", "completed", "checkmark"))
        locked = any(word in metadata for word in ("已锁定", "locked", "disabled"))
        aria_disabled = (card.get_attribute("aria-disabled") or "").lower() == "true"
        lock_marker = card.locator(
            "[aria-label*='lock' i], [aria-label*='锁'], "
            "[class~='lock' i], [class~='locked' i], [class*='lock-icon' i]"
        ).first
        has_lock_marker = bool(lock_marker.count() and lock_marker.is_visible())

        return TaskCard(
            title=title,
            description=description,
            points=points,
            task_type=classify_task(title, description, href),
            element=card,
            available=not (locked or aria_disabled or has_lock_marker),
            completed=completed,
        )

    @staticmethod
    def _content_lines(text: str) -> list[str]:
        lines: list[str] = []
        for raw in text.splitlines():
            line = re.sub(r"\s+", " ", raw).strip()
            line = TASK_POINT_PATTERN.sub("", line).strip()
            if not line:
                continue
            if re.fullmatch(r"(?:locked|已锁定|completed|已完成)", line, re.IGNORECASE):
                continue
            lines.append(line)
        return lines

    def capture_diagnostics(self, reason: str = "panel") -> tuple[str, str]:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        safe_reason = re.sub(r"[^a-zA-Z0-9_-]", "-", reason)[:30]
        screenshot = project_path(f"data/screenshots/{safe_reason}-{stamp}.png")
        html = project_path(f"data/screenshots/{safe_reason}-{stamp}.html")
        screenshot.parent.mkdir(parents=True, exist_ok=True)
        self.page.screenshot(path=str(screenshot), full_page=True)
        html.write_text(self.page.content(), encoding="utf-8")
        return str(screenshot), str(html)
