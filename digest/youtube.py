"""Нові випуски з RSS YouTube + метадані з YouTube Data API v3 (безкоштовна квота 10 000 од./добу)."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import feedparser
import requests

log = logging.getLogger(__name__)

API = "https://www.googleapis.com/youtube/v3"
RSS = "https://www.youtube.com/feeds/videos.xml?channel_id={}"
UA = {"User-Agent": "Mozilla/5.0 (podcast-digest)", "Accept-Language": "en"}
TIMEOUT = 30


@dataclass
class FeedItem:
    video_id: str
    channel_id: str
    title: str
    published: datetime


@dataclass
class Video:
    video_id: str
    channel_id: str
    title: str
    channel_title: str
    published: datetime
    duration_s: int
    views: int | None
    live: str  # none | live | upcoming
    default_audio_language: str | None = None

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/watch?v={self.video_id}"


@dataclass
class Channel:
    channel_id: str
    title: str
    subscribers: int | None  # None, якщо канал приховує кількість
    handle: str | None

    @property
    def url(self) -> str:
        return f"https://www.youtube.com/{self.handle}" if self.handle else f"https://www.youtube.com/channel/{self.channel_id}"


_DUR = re.compile(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?$")


def parse_iso_duration(value: str) -> int:
    m = _DUR.match(value or "")
    if not m:
        return 0
    d, h, mi, s = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + s


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _api(path: str, api_key: str, **params) -> dict:
    params["key"] = api_key
    r = requests.get(f"{API}/{path}", params=params, timeout=TIMEOUT)
    if r.status_code != 200:
        raise RuntimeError(f"YouTube API {path}: HTTP {r.status_code} {r.text[:300]}")
    return r.json()


def resolve_channel_id(handle: str, api_key: str) -> str | None:
    data = _api("channels", api_key, part="id", forHandle=handle)
    items = data.get("items") or []
    return items[0]["id"] if items else None


def parse_feed(xml: bytes | str, channel_id: str) -> list[FeedItem]:
    feed = feedparser.parse(xml)
    items = []
    for e in feed.entries:
        vid = e.get("yt_videoid")
        if not vid:
            continue
        published = e.get("published") or e.get("updated")
        items.append(FeedItem(vid, channel_id, e.get("title", ""), _parse_dt(published)))
    return items


def fetch_feed(channel_id: str) -> list[FeedItem]:
    r = requests.get(RSS.format(channel_id), headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return parse_feed(r.content, channel_id)


def fetch_videos(video_ids: list[str], api_key: str) -> dict[str, Video]:
    out: dict[str, Video] = {}
    for i in range(0, len(video_ids), 50):
        chunk = video_ids[i : i + 50]
        data = _api("videos", api_key, part="snippet,contentDetails,statistics", id=",".join(chunk), maxResults=50)
        for it in data.get("items", []):
            sn, cd, st = it["snippet"], it.get("contentDetails", {}), it.get("statistics", {})
            out[it["id"]] = Video(
                video_id=it["id"],
                channel_id=sn["channelId"],
                title=sn.get("title", ""),
                channel_title=sn.get("channelTitle", ""),
                published=_parse_dt(sn["publishedAt"]),
                duration_s=parse_iso_duration(cd.get("duration", "")),
                views=int(st["viewCount"]) if "viewCount" in st else None,
                live=sn.get("liveBroadcastContent", "none"),
                default_audio_language=sn.get("defaultAudioLanguage"),
            )
    return out


def fetch_channel(channel_id: str, api_key: str) -> Channel:
    data = _api("channels", api_key, part="snippet,statistics", id=channel_id)
    it = data["items"][0]
    st = it.get("statistics", {})
    subs = None if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount", 0)) or None
    return Channel(
        channel_id=channel_id,
        title=it["snippet"].get("title", ""),
        subscribers=subs,
        handle=it["snippet"].get("customUrl"),
    )
