"""Виклик Gemini (безкоштовний тариф). Спершу пробуємо текст субтитрів (дешево),
інакше передаємо посилання на YouTube — Gemini сам «дивиться» публічне відео."""
from __future__ import annotations

import logging
import time
from pathlib import Path

from google import genai
from google.genai import errors, types
from pydantic import BaseModel, Field

log = logging.getLogger(__name__)

# 1 кадр на 10 с: для «говорячих голів» цього досить, а токенів ~у 2.5 раза менше
VIDEO_FPS = 0.1
MAX_TRANSCRIPT_CHARS = 600_000


class Person(BaseModel):
    name_uk: str
    name_en: str
    role_uk: str
    is_host: bool


class Quote(BaseModel):
    text: str
    author_uk: str
    ts: str = ""  # момент у відео: «ХХ:СС» або «ГГ:ХХ:СС», порожньо — якщо не певен


class Fact(BaseModel):
    text: str
    kind: str = "факт"  # «факт» — перевірюване твердження, «оцінка» — думка чи прогноз спікера
    ts: str = ""


class Digest(BaseModel):
    is_substantive: bool
    category: str = ""
    headline: str
    people: list[Person] = Field(default_factory=list)
    lead: str
    facts: list[Fact] = Field(default_factory=list)
    disagreement: str = ""
    quotes: list[Quote] = Field(default_factory=list)
    implication: str
    watch_next: str = ""


class QuotaExhausted(Exception):
    """Усі моделі зі списку вичерпали безкоштовний ліміт — чекаємо наступного запуску."""


class DigestError(Exception):
    pass


def build_prompt(template_path: Path, title: str, channel: str, published: str) -> str:
    return template_path.read_text(encoding="utf-8").format(title=title, channel=channel, published=published)


def build_contents(prompt: str, video_url: str, transcript: str | None) -> list[types.Part]:
    if transcript:
        return [
            types.Part(text=prompt),
            types.Part(text="Транскрипт випуску:\n\n" + transcript[:MAX_TRANSCRIPT_CHARS]),
        ]
    return [
        types.Part(
            file_data=types.FileData(file_uri=video_url, mime_type="video/*"),
            video_metadata=types.VideoMetadata(fps=VIDEO_FPS),
        ),
        types.Part(text=prompt),
    ]


def validate(d: Digest) -> list[str]:
    problems = []
    if not d.headline.strip():
        problems.append("порожній заголовок")
    if not d.lead.strip():
        problems.append("порожній лід")
    if d.is_substantive and not (3 <= len(d.facts) <= 7):
        problems.append(f"пунктів у «Фактах»: {len(d.facts)}")
    if d.is_substantive and not d.implication.strip():
        problems.append("порожній блок «Що з цього випливає»")
    if d.is_substantive and not d.people:
        problems.append("немає людей")
    return problems


def summarize(
    api_key: str,
    models: list[str],
    prompt: str,
    video_url: str,
    transcript: str | None,
    client: genai.Client | None = None,
) -> tuple[Digest, str]:
    """Повертає (дайджест, назва моделі). Кидає QuotaExhausted або DigestError."""
    client = client or genai.Client(api_key=api_key)
    contents = build_contents(prompt, video_url, transcript)
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=Digest,
        temperature=0.4,
        media_resolution=None if transcript else types.MediaResolution.MEDIA_RESOLUTION_LOW,
    )
    quota_hits = 0
    last_err: Exception | None = None
    for model in models:
        for attempt in range(3):
            try:
                resp = client.models.generate_content(model=model, contents=contents, config=config)
                digest = resp.parsed if isinstance(resp.parsed, Digest) else Digest.model_validate_json(resp.text or "")
                problems = validate(digest)
                if problems:
                    raise DigestError("Невалідна відповідь: " + ", ".join(problems))
                return digest, model
            except errors.ClientError as e:
                last_err = e
                code = getattr(e, "code", None)
                if code == 429:
                    log.warning("%s: ліміт безкоштовного тарифу (429), пробую наступну модель", model)
                    quota_hits += 1
                    break
                if code in (400, 403, 404):
                    # 404 — модель недоступна/перейменована; 400 — напр. відео недоступне для цієї моделі
                    log.warning("%s: %s %s", model, code, str(e)[:300])
                    break
                raise DigestError(str(e)) from e
            except errors.ServerError as e:
                last_err = e
                wait = 20 * (attempt + 1)
                log.warning("%s: помилка сервера %s, повтор через %ss", model, getattr(e, "code", "?"), wait)
                time.sleep(wait)
            except (DigestError, ValueError) as e:
                last_err = e
                log.warning("%s: %s (спроба %d)", model, e, attempt + 1)
    if quota_hits == len(models):
        raise QuotaExhausted(str(last_err))
    raise DigestError(f"Не вдалося отримати дайджест: {last_err}")
