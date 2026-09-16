"""Спроба взяти готові субтитри (дешево за токенами). З хмарних IP YouTube часто блокує — тоді повертаємо None
і Gemini «дивиться» відео за посиланням."""
from __future__ import annotations

import logging

from youtube_transcript_api import YouTubeTranscriptApi

log = logging.getLogger(__name__)

PREFERRED = ["en", "en-US", "en-GB", "uk", "ru", "es", "de", "fr"]
MIN_WORDS = 1500  # коротший текст — підозріло, краще дати Gemini саме відео


def get_transcript(video_id: str) -> str | None:
    try:
        api = YouTubeTranscriptApi()
        listing = api.list(video_id)
        try:
            tr = listing.find_manually_created_transcript(PREFERRED)
        except Exception:
            tr = listing.find_generated_transcript(PREFERRED)
        fetched = tr.fetch()
        text = " ".join(s.text.replace("\n", " ") for s in fetched.snippets)
    except Exception as e:  # IpBlocked, RequestBlocked, NoTranscriptFound, ...
        log.info("Субтитри недоступні для %s: %s", video_id, type(e).__name__)
        return None
    if len(text.split()) < MIN_WORDS:
        log.info("Субтитри для %s закороткі (%d слів)", video_id, len(text.split()))
        return None
    return text
