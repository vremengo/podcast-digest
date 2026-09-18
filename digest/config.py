from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent

DEFAULT_MODELS = "gemini-3.8-flash,gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash"


@dataclass
class ChannelEntry:
    handle: str | None = None
    id: str | None = None
    name: str | None = None

    @property
    def key(self) -> str:
        return self.id or (self.handle or "").lower()


@dataclass
class Config:
    channels: list[ChannelEntry]
    timezone: str = "Europe/Kyiv"
    min_duration_minutes: int = 30
    lookback_hours: int = 48
    max_videos_per_run: int = 2
    gemini_models: list[str] = field(default_factory=list)
    gemini_api_key: str = ""
    youtube_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    state_path: Path = ROOT / "data" / "state.json"
    prompt_path: Path = ROOT / "prompts" / "digest_uk.md"


def load_config(path: Path | None = None) -> Config:
    path = path or ROOT / "config" / "channels.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    channels = []
    for item in raw.get("channels") or []:
        entry = ChannelEntry(handle=item.get("handle"), id=item.get("id"), name=item.get("name"))
        if entry.handle and not entry.handle.startswith("@"):
            entry.handle = "@" + entry.handle
        if not (entry.handle or entry.id):
            raise ValueError(f"Канал без handle/id у конфігу: {item}")
        channels.append(entry)

    models = os.getenv("GEMINI_MODELS") or DEFAULT_MODELS
    return Config(
        channels=channels,
        timezone=raw.get("timezone", "Europe/Kyiv"),
        min_duration_minutes=int(raw.get("min_duration_minutes", 30)),
        lookback_hours=int(raw.get("lookback_hours", 48)),
        max_videos_per_run=int(raw.get("max_videos_per_run", 2)),
        gemini_models=[m.strip() for m in models.split(",") if m.strip()],
        gemini_api_key=os.getenv("GEMINI_API_KEY", ""),
        youtube_api_key=os.getenv("YOUTUBE_API_KEY", ""),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
    )
