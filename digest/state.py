"""Стан у JSON-файлі, який GitHub Actions комітить назад у репозиторій (без бази даних)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

MAX_ATTEMPTS = 3
MAX_ENTRIES = 3000
MIN_BUDGET = 10  # нижче цього бюджет не калібрується — щоб бот не зупинився назавжди
CALIBRATION_MARGIN = 5  # запас нижче реально спостереженої стелі 429


class State:
    def __init__(self, path: Path):
        self.path = path
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.videos: dict[str, dict] = data.get("videos", {})
        self.channel_ids: dict[str, str] = data.get("channel_ids", {})
        self.gemini_day: str | None = data.get("gemini_day")
        self.gemini_calls_today: int = data.get("gemini_calls_today", 0)
        # None, поки жодного разу не впирались у 429 — тоді береться конфігурний дефолт
        self.gemini_daily_budget: int | None = data.get("gemini_daily_budget")

    def is_done(self, video_id: str) -> bool:
        v = self.videos.get(video_id)
        if not v:
            return False
        return v["status"] in ("posted", "skipped") or v.get("attempts", 0) >= MAX_ATTEMPTS

    def mark(self, video_id: str, status: str, note: str = "") -> None:
        prev = self.videos.get(video_id, {})
        attempts = prev.get("attempts", 0) + (1 if status == "failed" else 0)
        self.videos[video_id] = {
            "status": status,
            "attempts": attempts,
            "note": note[:200],
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    def _roll_day(self, now: datetime) -> None:
        day = now.strftime("%Y-%m-%d")
        if self.gemini_day != day:
            self.gemini_day = day
            self.gemini_calls_today = 0

    def record_gemini_calls(self, n: int, now: datetime) -> None:
        """Додає n реальних викликів Gemini API до лічильника поточної UTC-доби."""
        if n <= 0:
            return
        self._roll_day(now)
        self.gemini_calls_today += n

    def budget_remaining(self, now: datetime, default_budget: int) -> int:
        """Скільки ще запитів Gemini можна зробити сьогодні за поточною (можливо, каліброваною) оцінкою."""
        self._roll_day(now)
        budget = self.gemini_daily_budget or default_budget
        return max(0, budget - self.gemini_calls_today)

    def note_quota_hit(self, now: datetime, default_budget: int) -> None:
        """Викликати одразу після record_gemini_calls, коли отримали 429 — каліброває бюджет
        униз до реально спостереженої стелі (з невеликим запасом), а не залишається на здогадці."""
        self._roll_day(now)
        current = self.gemini_daily_budget or default_budget
        observed_ceiling = max(MIN_BUDGET, self.gemini_calls_today - CALIBRATION_MARGIN)
        self.gemini_daily_budget = min(current, observed_ceiling)

    def save(self) -> None:
        if len(self.videos) > MAX_ENTRIES:
            keep = sorted(self.videos.items(), key=lambda kv: kv[1]["ts"])[-MAX_ENTRIES:]
            self.videos = dict(keep)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        payload = {"channel_ids": self.channel_ids, "videos": self.videos}
        if self.gemini_day:
            payload["gemini_day"] = self.gemini_day
            payload["gemini_calls_today"] = self.gemini_calls_today
        if self.gemini_daily_budget is not None:
            payload["gemini_daily_budget"] = self.gemini_daily_budget
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True),
            encoding="utf-8",
        )
        tmp.replace(self.path)
