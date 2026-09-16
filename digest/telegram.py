from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger(__name__)


class TelegramError(Exception):
    pass


def send_message(token: str, chat_id: str, text: str, retries: int = 3) -> int:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    }
    for attempt in range(retries):
        r = requests.post(url, json=payload, timeout=30)
        data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if data.get("ok"):
            return data["result"]["message_id"]
        retry_after = (data.get("parameters") or {}).get("retry_after")
        if r.status_code == 429 and retry_after:
            log.warning("Telegram 429, чекаю %ss", retry_after)
            time.sleep(int(retry_after) + 1)
            continue
        if r.status_code >= 500 and attempt < retries - 1:
            time.sleep(5 * (attempt + 1))
            continue
        # не логуємо URL — у ньому токен
        raise TelegramError(f"HTTP {r.status_code}: {data.get('description') or r.text[:200]}")
    raise TelegramError("Вичерпано спроби надсилання")


def send_post(token: str, chat_id: str, messages: list[str]) -> list[int]:
    ids = []
    for i, m in enumerate(messages):
        ids.append(send_message(token, chat_id, m))
        if i < len(messages) - 1:
            time.sleep(1.5)
    return ids
