"""Вік людей беремо з Wikidata, а не з LLM (моделі часто помиляються з віком).
Якщо немає однозначного збігу — вік не показуємо."""
from __future__ import annotations

import logging
import unicodedata
from datetime import date

import requests

log = logging.getLogger(__name__)

API = "https://www.wikidata.org/w/api.php"
HEADERS = {"User-Agent": "podcast-digest/1.0 (https://github.com/; telegram digest bot)"}
HUMAN = "Q5"


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    return "".join(c for c in s if not unicodedata.combining(c)).casefold().strip()


def _claim_ids(entity: dict, prop: str) -> list[str]:
    out = []
    for c in entity.get("claims", {}).get(prop, []):
        v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
        if isinstance(v, dict) and "id" in v:
            out.append(v["id"])
    return out


def _birth_date(entity: dict) -> date | None:
    for c in entity.get("claims", {}).get("P569", []):
        v = c.get("mainsnak", {}).get("datavalue", {}).get("value")
        if not v or v.get("precision", 0) < 11:  # потрібна точність до дня
            continue
        t = v["time"]  # "+1984-06-27T00:00:00Z"
        try:
            y, m, d = int(t[1:5]), int(t[6:8]), int(t[9:11])
            return date(y, m, d)
        except ValueError:
            continue
    return None


def age_on(born: date, today: date) -> int:
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def pick_age(name: str, search: list[dict], entities: dict[str, dict], today: date) -> int | None:
    """Чиста логіка вибору: рівно одна жива людина з точним збігом імені та відомою датою народження."""
    target = _norm(name)
    candidates = []
    for s in search:
        labels = {_norm(s.get("label", ""))} | {_norm(a) for a in s.get("aliases", []) or []}
        if s.get("match", {}).get("text"):
            labels.add(_norm(s["match"]["text"]))
        if target not in labels:
            continue
        ent = entities.get(s["id"])
        if not ent or HUMAN not in _claim_ids(ent, "P31"):
            continue
        if ent.get("claims", {}).get("P570"):  # помер(ла)
            continue
        born = _birth_date(ent)
        if born:
            candidates.append(born)
    if len(set(candidates)) != 1:
        return None
    return age_on(candidates[0], today)


def get_age(name: str, today: date | None = None) -> int | None:
    if not name:
        return None
    today = today or date.today()
    try:
        r = requests.get(
            API,
            params={"action": "wbsearchentities", "search": name, "language": "en", "type": "item", "limit": 7, "format": "json"},
            headers=HEADERS,
            timeout=20,
        )
        r.raise_for_status()
        search = r.json().get("search", [])
        if not search:
            return None
        ids = "|".join(s["id"] for s in search)
        r = requests.get(
            API,
            params={"action": "wbgetentities", "ids": ids, "props": "claims", "format": "json"},
            headers=HEADERS,
            timeout=20,
        )
        r.raise_for_status()
        entities = r.json().get("entities", {})
    except Exception as e:
        log.warning("Wikidata недоступна для %s: %s", name, e)
        return None
    return pick_age(name, search, entities, today)
