from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from time import monotonic
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

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
    task_id: str = ""
    section: str = "extra"
    href: str = ""


def classify_task(title: str, description: str = "", href: str = "", *, points: int = 0) -> str:
    content = f"{title} {description}".lower()
    normalized_href = href.lower()
    if any(word in content for word in ("推荐", "朋友", "refer", "friend", "邀请")):
        # A fixed +N offer can reward visiting the referral page. The referral
        # programme itself is separate and must never trigger sending invitations.
        return "visit" if points > 0 else "referral"
    if any(word in content for word in ("拼图", "puzzle", "排列图块", "jigsaw")):
        return "puzzle"
    if any(
        word in content
        for word in ("测验", "quiz", "问答", "答题", "测试", "poll", "答案", "小问题", "trivia")
    ) or any(word in normalized_href for word in ("quiz", "trivia")):
        return "quiz"
    if any(word in content for word in ("视频", "video", "观看", "watch")):
        return "video"
    if "bing.com/search" in normalized_href or "search?q=" in normalized_href:
        return "keyword_search"
    if points > 0:
        return "visit"
    return "unknown"


def find_task(original: TaskCard, candidates: list[TaskCard]) -> TaskCard | None:
    """Rebind only an unambiguous offer, never the first card with the same title."""
    matches = [candidate for candidate in candidates if _same_task(original, candidate)]
    return matches[0] if len(matches) == 1 else None


def _same_task(original: TaskCard, candidate: TaskCard) -> bool:
    if candidate.section != original.section:
        return False
    if original.task_id:
        return candidate.task_id == original.task_id
    return (candidate.title, candidate.description, candidate.href, candidate.points) == (
        original.title,
        original.description,
        original.href,
        original.points,
    )


def task_identity(offer_id: str, dom_id: str, href: str) -> str:
    """Keep stable offer/link identity in memory without exposing tracking URLs."""
    if offer_id:
        return sha256(f"offer:{offer_id}".encode()).hexdigest()
    identity = f"dom:{dom_id}" if dom_id else ""
    if href:
        url = urlsplit(href)
        if url.scheme in {"http", "https", ""} and (url.path or url.netloc):
            query = [
                (key, value)
                for key, value in parse_qsl(url.query, keep_blank_values=True)
                if key.lower() not in {"cvid", "form", "ocid", "mkt", "setlang"}
            ]
            link_identity = urlunsplit(
                (url.scheme.lower(), url.netloc.lower(), url.path, urlencode(sorted(query)), "")
            )
            # Generic DOM IDs can be recycled for another offer after a refresh.
            identity += f"\nlink:{link_identity}"
    return sha256(identity.encode()).hexdigest() if identity else ""


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
    OFFER_CARD_SELECTOR = ".promo_cont, [data-offer-id], [data-offerid], [data-task-id]"

    def __init__(self, page: Any):
        self.page = page

    def open_panel(self) -> bool:
        clicked = False
        for _ in range(12):
            if self._panel_is_ready():
                return True
            if not clicked:
                for selector in self.REWARDS_BUTTON_SELECTORS:
                    candidates = self.page.locator(selector)
                    for index in range(min(candidates.count(), 5)):
                        candidate = candidates.nth(index)
                        try:
                            if candidate.is_visible():
                                candidate.click(timeout=8_000)
                                clicked = True
                                break
                        except PlaywrightError:
                            continue
                    if clicked:
                        break
            self.page.wait_for_timeout(500)
        ready = self._panel_is_ready()
        if not ready:
            logger.warning("Rewards 面板内容未就绪" if clicked else "未找到 Microsoft Rewards 入口")
        return ready

    def _panel_is_ready(self) -> bool:
        """A preloaded/hidden iframe alone does not mean the flyout has opened."""
        try:
            root = self._root()
            if root is not self.page:
                frame_element = root.frame_element()
                try:
                    # Balance/task contents can load later. Their own readers wait
                    # for readiness after the visible flyout has opened.
                    if frame_element.is_visible():
                        return True
                finally:
                    frame_element.dispose()
            markers = root.locator(f"{self.OFFER_CARD_SELECTOR}, #daily_set_card")
            if any(markers.nth(index).is_visible() for index in range(min(markers.count(), 10))):
                return True
            labels = root.get_by_text(POINT_LABEL_PATTERN, exact=True)
            return any(labels.nth(index).is_visible() for index in range(min(labels.count(), 5)))
        except PlaywrightError:
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
        return [task for task in self.parse_tasks() if task.section == "daily"]

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

    def task_state(self, original: TaskCard, *, timeout_ms: int = 0) -> str:
        """Read this offer's state; disappearance alone is not proof of credit."""
        candidates = self.parse_tasks(timeout_ms=timeout_ms)
        task = find_task(original, candidates)
        if task is not None:
            return "completed" if task.completed else "pending"
        if (
            original.section == "daily"
            and not any(_same_task(original, candidate) for candidate in candidates)
            and self.daily_set_is_complete()
        ):
            return "completed"
        return "unknown"

    def parse_tasks(self, *, timeout_ms: int = 0) -> list[TaskCard]:
        """Allow late extra cards to arrive before accepting a stable inventory."""
        deadline = monotonic() + max(0, timeout_ms) / 1_000
        previous = None
        stable_since = monotonic()
        while True:
            tasks = self._parse_tasks_once()
            now = monotonic()
            if now >= deadline:
                return tasks
            inventory = tuple(
                (
                    task.section,
                    task.task_id,
                    task.title,
                    task.description,
                    task.points,
                    task.completed,
                    task.available,
                )
                for task in tasks
            )
            if inventory != previous:
                previous, stable_since = inventory, now
            elif tasks and now - stable_since >= 1:
                return tasks
            self.page.wait_for_timeout(min(500, (deadline - now) * 1_000))

    def _parse_tasks_once(self) -> list[TaskCard]:
        """Read both Daily Set and extra offers, preserving distinct same-title cards."""
        root = self._root()
        cards = root.locator(self.OFFER_CARD_SELECTOR)
        structured = bool(cards.count())
        if not structured:
            # A selector union returns each DOM element once, even if it matches
            # several selectors. Prefer structured cards whenever Bing provides them.
            cards = root.locator(", ".join(self.CARD_SELECTORS))
        parsed: list[TaskCard] = []
        for index in range(min(cards.count(), 100)):
            card = cards.nth(index)
            try:
                if not card.is_visible():
                    continue
                if structured:
                    # Metadata on a nested link belongs to its enclosing promo card.
                    if card.evaluate("node => Boolean(node.parentElement?.closest('.promo_cont'))"):
                        continue
                    if card.locator(".promo_cont").count():
                        continue
                text = card.inner_text(timeout=1_500).strip()
                if not text or (not structured and not self._looks_like_task(text)):
                    continue
                candidate = self._parse_card(card, text)
                if STATUS_TEXT_PATTERN.fullmatch(candidate.title.strip()):
                    continue
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
                "答案",
                "小问题",
                "trivia",
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

        point_chip = card.locator(".point_cont").first
        point_text = ""
        point_aria = ""
        if point_chip.count():
            point_text = point_chip.inner_text(timeout=1_500).strip()
            label = point_chip.locator("[aria-label]").first
            point_aria = point_chip.get_attribute("aria-label") or ""
            if not point_aria and label.count():
                point_aria = label.get_attribute("aria-label") or ""
            # Completed chips may contain only a checkmark and a bare number.
            bare = re.fullmatch(r"[✓✔]?\s*(\d+)", point_text)
            if bare:
                point_text = f"+{bare.group(1)}"
        else:
            # Only a standalone reward chip counts; numbers in promotional copy do not.
            point_text = next(
                (
                    line.strip()
                    for line in text.splitlines()
                    if re.fullmatch(r"\s*\+\s*\d+\s*", line)
                ),
                "",
            )
        points = extract_task_points(point_text, point_aria)

        identity = card.evaluate("""node => {
            const link = node.closest('a[href]') || node.querySelector('a[href]');
            const attrs = ['data-offer-id', 'data-offerid', 'data-task-id'];
            const offerId = attrs.map(attr => node.getAttribute(attr) ||
                link?.getAttribute(attr)).find(Boolean) || '';
            return {offerId, domId: node.id || link?.id || '',
                href: link?.getAttribute('href') || '',
                section: node.closest('#daily_set_card') ? 'daily' : 'extra'};
        }""")
        href = identity["href"]

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
        explicitly_pending = "not completed" in aria_label.lower() or "未完成" in aria_label
        if not explicitly_pending and not completed:
            classes = (card.get_attribute("class") or "").lower().split()
            completed = bool({"completed", "complete"}.intersection(classes))
            if point_chip.count():
                checkmark = point_chip.locator("[class*='checkmark' i]").first
                completed = completed or bool(checkmark.count() and checkmark.is_visible())
                completed = completed or bool(re.match(r"\s*[✓✔]", point_chip.inner_text()))
        locked = bool(re.search(r"\b(?:locked|disabled)\b|已锁定", metadata))
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
            task_type=classify_task(title, description, href, points=points),
            element=card,
            available=not (locked or aria_disabled or has_lock_marker),
            completed=completed,
            task_id=task_identity(identity["offerId"], identity["domId"], href),
            section=identity["section"],
            href=href,
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
