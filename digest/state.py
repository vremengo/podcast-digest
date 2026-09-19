"""Стан у JSON-файлі, який GitHub Actions комітить назад у репозиторій (без бази даних)."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

MAX_ATTEMPTS = 3
MAX_ENTRIES = 3000


class State:
    def __init__(self, path: Path):
        self.path = path
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        self.videos: dict[str, dict] = data.get("videos", {})
        self.channel_ids: dict[str, str] = data.get("channel_ids", {})
        self.quota_cooldown_until: str | None = data.get("quota_cooldown_until")

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

    def cooldown_remaining(self, now: datetime) -> timedelta | None:
        """Скільки ще чекати, якщо ми в кулдауні після вичерпання квоти Gemini. None — можна працювати."""
        if not self.quota_cooldown_until:
            return None
        until = datetime.fromisoformat(self.quota_cooldown_until)
        remaining = until - now
        return remaining if remaining.total_seconds() > 0 else None

    def start_quota_cooldown(self, now: datetime, hours: float) -> None:
        until = now + timedelta(hours=hours)
        self.quota_cooldown_until = until.isoformat(timespec="seconds")

    def clear_quota_cooldown(self) -> None:
        self.quota_cooldown_until = None

    def save(self) -> None:
        if len(self.videos) > MAX_ENTRIES:
            keep = sorted(self.videos.items(), key=lambda kv: kv[1]["ts"])[-MAX_ENTRIES:]
            self.videos = dict(keep)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        payload = {"channel_ids": self.channel_ids, "videos": self.videos}
        if self.quota_cooldown_until:
            payload["quota_cooldown_until"] = self.quota_cooldown_until
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True),
            encoding="utf-8",
        )
        tmp.replace(self.path)
