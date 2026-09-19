"""Запуск: python -m digest [--dry-run] [--video URL] [--limit N]"""
from __future__ import annotations

import argparse
import logging
import re
import sys
from datetime import datetime, timedelta, timezone

from . import gemini, render, telegram, transcript, wikidata, youtube
from .config import Config, load_config
from .state import State

log = logging.getLogger("digest")

_VID = re.compile(r"(?:v=|youtu\.be/|/live/|/shorts/|/embed/)([\w-]{11})")


def extract_video_id(value: str) -> str:
    m = _VID.search(value)
    if m:
        return m.group(1)
    if re.fullmatch(r"[\w-]{11}", value):
        return value
    raise SystemExit(f"Не схоже на посилання YouTube: {value}")


def collect_candidates(cfg: Config, state: State, now: datetime) -> list[tuple[youtube.Video, str | None]]:
    """Повертає [(відео, назва каналу з конфігу)] — нові, довгі, не трансляції, від старих до нових."""
    since = now - timedelta(hours=cfg.lookback_hours)
    names: dict[str, str | None] = {}
    fresh: list[youtube.FeedItem] = []
    for ch in cfg.channels:
        cid = ch.id or state.channel_ids.get(ch.key)
        if not cid:
            try:
                cid = youtube.resolve_channel_id(ch.handle, cfg.youtube_api_key)
            except Exception as e:
                log.error("Не вдалося знайти канал %s: %s", ch.handle, e)
                continue
            if not cid:
                log.error("Канал %s не знайдено", ch.handle)
                continue
            state.channel_ids[ch.key] = cid
        names[cid] = ch.name
        try:
            items = youtube.fetch_feed(cid)
        except Exception as e:
            log.error("RSS %s недоступний: %s", ch.handle or cid, e)
            continue
        fresh += [i for i in items if i.published >= since and not state.is_done(i.video_id)]

    if not fresh:
        return []
    details = youtube.fetch_videos([i.video_id for i in fresh], cfg.youtube_api_key)
    result = []
    for item in fresh:
        v = details.get(item.video_id)
        if not v:
            continue  # приватне/видалене — спробуємо пізніше, якщо з'явиться
        if v.live != "none":
            continue  # трансляція ще йде/запланована — повернемось пізніше
        if v.duration_s < cfg.min_duration_minutes * 60:
            state.mark(v.video_id, "skipped", f"коротке: {v.duration_s}s")
            continue
        result.append((v, names.get(v.channel_id)))
    result.sort(key=lambda t: t[0].published)
    return result


def process(cfg: Config, state: State, video: youtube.Video, display_name: str | None, now: datetime, dry_run: bool) -> None:
    channel = youtube.fetch_channel(video.channel_id, cfg.youtube_api_key)
    channel_title = display_name or channel.title
    log.info("Обробляю %s — %s (%s)", video.video_id, video.title, channel_title)

    text = transcript.get_transcript(video.video_id)
    log.info("Джерело: %s", "субтитри" if text else "відео за посиланням")
    prompt = gemini.build_prompt(
        cfg.prompt_path, title=video.title, channel=channel_title, published=video.published.strftime("%Y-%m-%d")
    )
    digest, model = gemini.summarize(cfg.gemini_api_key, cfg.gemini_models, prompt, video.url, text)
    log.info("Модель: %s", model)
    if not dry_run and state.quota_cooldown_until:
        state.clear_quota_cooldown()

    if not digest.is_substantive:
        log.info("Пропускаю: не змістовний випуск")
        if not dry_run:
            state.mark(video.video_id, "skipped", "not substantive")
        return

    ages = {}
    for p in digest.people:
        age = wikidata.get_age(p.name_en, today=now.date())
        if age:
            ages[p.name_en] = age

    meta = render.PostMeta(
        video_url=video.url,
        channel_title=channel_title,
        channel_url=channel.url,
        subscribers=channel.subscribers,
        duration_s=video.duration_s,
        published=video.published,
        views=video.views,
        ages=ages,
    )
    messages = render.render(digest, meta, cfg.timezone, now)

    if dry_run:
        for i, m in enumerate(messages, 1):
            print(f"\n===== повідомлення {i}/{len(messages)} ({render.visible_len(m)} симв.) =====\n{m}")
        return

    sent = 0
    try:
        for i, m in enumerate(messages):
            telegram.send_message(cfg.telegram_bot_token, cfg.telegram_chat_id, m)
            sent += 1
    finally:
        if sent:  # хоча б частину опубліковано — не повторюємо, щоб не дублювати пост
            state.mark(video.video_id, "posted", f"{model}; {sent}/{len(messages)} msg")
            state.save()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Дайджест подкастів → Telegram")
    ap.add_argument("--dry-run", action="store_true", help="друкувати пост замість публікації, стан не змінювати")
    ap.add_argument("--video", help="обробити одне конкретне відео (посилання або id)")
    ap.add_argument("--limit", type=int, help="макс. відео за запуск (перекриває конфіг)")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config()
    missing = [n for n, v in [("GEMINI_API_KEY", cfg.gemini_api_key), ("YOUTUBE_API_KEY", cfg.youtube_api_key)] if not v]
    if not args.dry_run:
        missing += [n for n, v in [("TELEGRAM_BOT_TOKEN", cfg.telegram_bot_token), ("TELEGRAM_CHAT_ID", cfg.telegram_chat_id)] if not v]
    if missing:
        log.error("Не задані змінні середовища: %s", ", ".join(missing))
        return 2

    state = State(cfg.state_path)
    now = datetime.now(timezone.utc)

    if not args.video:
        remaining = state.cooldown_remaining(now)
        if remaining is not None:
            log.info("Кулдаун після вичерпання квоти Gemini: ще %d хв — пропускаю цей запуск", remaining.total_seconds() // 60)
            return 0

    if args.video:
        vid = extract_video_id(args.video)
        details = youtube.fetch_videos([vid], cfg.youtube_api_key)
        if vid not in details:
            log.error("Відео %s не знайдено або воно не публічне", vid)
            return 1
        queue = [(details[vid], None)]
    else:
        queue = collect_candidates(cfg, state, now)
        if not args.dry_run:
            state.save()

    limit = args.limit or cfg.max_videos_per_run
    log.info("Нових випусків: %d, обробляю до %d", len(queue), limit)
    failures = 0
    for video, name in queue[:limit]:
        try:
            process(cfg, state, video, name, now, args.dry_run)
        except gemini.QuotaExhausted:
            log.warning(
                "Безкоштовний ліміт Gemini вичерпано — пауза на %.0f год, щоб не бити в той самий ліміт щораз",
                cfg.quota_cooldown_hours,
            )
            if not args.dry_run:
                state.start_quota_cooldown(now, cfg.quota_cooldown_hours)
            break
        except Exception as e:
            failures += 1
            log.exception("Помилка на %s: %s", video.video_id, e)
            if not args.dry_run and not state.is_done(video.video_id):
                state.mark(video.video_id, "failed", f"{type(e).__name__}: {e}")
        finally:
            if not args.dry_run:
                state.save()
    return 1 if failures and failures == min(len(queue), limit) else 0


if __name__ == "__main__":
    sys.exit(main())
