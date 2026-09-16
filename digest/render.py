"""Формування посту в стилі дайджесту (Telegram HTML) і розбиття на повідомлення ≤ 4096 символів."""
from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from .gemini import Digest

TG_LIMIT = 4096
SAFE_LIMIT = 4000
SEPARATOR = "_" * 24
QUOTE_CHARS = "«»\"„“” "
NUM_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


@dataclass
class PostMeta:
    video_url: str
    channel_title: str
    channel_url: str
    subscribers: int | None
    duration_s: int
    published: datetime  # aware
    views: int | None
    ages: dict[str, int]  # name_en -> вік


def esc(s: str) -> str:
    return html.escape((s or "").strip(), quote=False)


def _trim_num(x: float, decimals: int) -> str:
    s = f"{x:.{decimals}f}".rstrip("0").rstrip(".") if decimals else f"{x:.0f}"
    return s.replace(".", ",")


def human_number(n: int) -> str:
    """6010000 -> '6,01 млн', 34000 -> '34 тис.', 1500 -> '1,5 тис.', 950 -> '950'."""
    if n >= 1_000_000_000:
        v = n / 1_000_000_000
        return f"{_trim_num(v, 2 if v < 10 else 1 if v < 100 else 0)} млрд"
    if n >= 1_000_000:
        v = n / 1_000_000
        return f"{_trim_num(v, 2 if v < 10 else 1 if v < 100 else 0)} млн"
    if n >= 1_000:
        v = n / 1_000
        if round(v, 1 if v < 10 else 0) >= 1000:
            return human_number(1_000_000)
        return f"{_trim_num(v, 1 if v < 10 else 0)} тис."
    return str(n)


def plural_uk(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def subscribers_label(n: int) -> str:
    if n >= 1_000:
        return f"{human_number(n)} підписників"
    return f"{n} {plural_uk(n, 'підписник', 'підписники', 'підписників')}"


def human_duration(seconds: int) -> str:
    minutes = round(seconds / 60)
    h, m = divmod(minutes, 60)
    if h and m:
        return f"{h} год {m} хв"
    if h:
        return f"{h} год"
    return f"{max(m, 1)} хв"


def human_ago(published: datetime, now: datetime) -> str:
    sec = max(0, int((now - published).total_seconds()))
    if sec < 3600:
        return f"{max(1, sec // 60)} хв тому"
    if sec < 86400:
        return f"{sec // 3600} год тому"
    days = sec // 86400
    return f"{days} {plural_uk(days, 'день', 'дні', 'днів')} тому"


def person_line(p, ages: dict[str, int]) -> str:
    icon = "🎤" if p.is_host else "👤"
    line = f"{icon} {esc(p.name_uk)} – {esc(p.role_uk)}"
    age = ages.get(p.name_en)
    if age:
        line += f" ({age})"
    return line


def render_blocks(d: Digest, meta: PostMeta, tz: str, now: datetime) -> list[str]:
    guests = [p for p in d.people if not p.is_host]
    hosts = [p for p in d.people if p.is_host]
    local = meta.published.astimezone(ZoneInfo(tz))

    blocks = [f'🧭 <a href="{html.escape(meta.video_url)}">{esc(d.headline)}</a>']
    if d.people:
        blocks.append("\n".join(person_line(p, meta.ages) for p in guests + hosts))

    info = f'📺 <a href="{html.escape(meta.channel_url)}">{esc(meta.channel_title)}</a>'
    if meta.subscribers:
        info += f" – 👥 {subscribers_label(meta.subscribers)}"
    info_lines = [info, f"⏱ {human_duration(meta.duration_s)}", f"🗓 {local:%d.%m.%y} ({human_ago(meta.published, now)})"]
    if meta.views is not None:
        info_lines.append(f"👁 {human_number(meta.views)}")
    blocks.append("\n".join(info_lines))

    blocks.append(f"📝 {esc(d.summary)}")
    blocks.append(SEPARATOR)
    blocks.append("💡 <b>ГОЛОВНІ ТЕЗИ</b>")
    for i, t in enumerate(d.theses[: len(NUM_EMOJI)]):
        blocks.append(f"{NUM_EMOJI[i]} <b>{esc(t.title)}</b>\n{esc(t.text)}")

    if d.quotes:
        quotes = "\n\n".join(f"«{esc(q.text).strip(QUOTE_CHARS)}»\n— <i>{esc(q.author_uk)}</i>" for q in d.quotes)
        blocks.append(f"💬 <b>ЦИТАТИ</b>\n\n{quotes}")
    if d.takeaway.strip():
        blocks.append(f"🎯 <b>ВИСНОВОК</b>\n{esc(d.takeaway)}")
    return blocks


_TAG = re.compile(r"<[^>]+>")


def visible_len(html_text: str) -> int:
    """Довжина тексту після розбору HTML у UTF-16 одиницях (так рахує Telegram)."""
    text = html.unescape(_TAG.sub("", html_text))
    return len(text.encode("utf-16-le")) // 2


def split_messages(blocks: list[str], limit: int = SAFE_LIMIT) -> list[str]:
    messages: list[str] = []
    current = ""
    for block in blocks:
        if visible_len(block) > limit:  # аварійний випадок: обрізаємо блок без розриву тегів
            plain = esc(html.unescape(_TAG.sub("", block)))
            block = plain[: limit - 10].rsplit(" ", 1)[0] + "…"
        candidate = f"{current}\n\n{block}" if current else block
        if visible_len(candidate) <= limit:
            current = candidate
        else:
            messages.append(current)
            current = block
    if current:
        messages.append(current)
    return messages


def render(d: Digest, meta: PostMeta, tz: str, now: datetime) -> list[str]:
    return split_messages(render_blocks(d, meta, tz, now))
