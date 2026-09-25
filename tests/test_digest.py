from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from digest import __main__ as app
from digest import gemini, render, wikidata, youtube
from digest.config import ChannelEntry, Config
from digest.state import State

NOW = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)
ROOT = Path(__file__).resolve().parent.parent


def sample_digest(n_facts: int = 4, long: bool = False) -> gemini.Digest:
    body = "Конкретний факт із цифрою 42 і прикладом з розмови. " * (40 if long else 3)
    return gemini.Digest(
        is_substantive=True,
        category="Психологія",
        headline="Хлої Кардаш'ян про подолання сорому, терапію та внутрішню силу після 40 років",
        people=[
            gemini.Person(name_uk="Джей Шетті", name_en="Jay Shetty", role_uk="автор бестселерів, ведучий подкасту On Purpose", is_host=True),
            gemini.Person(
                name_uk="Хлої Кардаш'ян", name_en="Khloé Kardashian",
                role_uk="підприємниця, телеведуча, співзасновниця Good American", is_host=False,
                thesis_uk="Сором можна подолати лише через кордони, а не публічність",
            ),
        ],
        lead="Розмова про переосмислення життєвих криз & відмову від <нав'язаного> сорому.",
        facts=[
            gemini.Fact(text=f"Пункт {i}: {body}", kind="оцінка" if i == 2 else "факт", ts="5:07" if i == 1 else "")
            for i in range(1, n_facts + 1)
        ],
        disagreement="Гостя не погоджується з ведучим щодо публічності: він радить відкритість, вона наполягає на межах.",
        quotes=[gemini.Quote(text="«Сором — це не моя ноша»", author_uk="Хлої Кардаш'ян", ts="1:02:03")],
        implication="Кордони — це турбота про себе, а не егоїзм.",
        watch_next="Чи вплине це на наступний сезон її шоу.",
    )


def sample_meta(**kw) -> render.PostMeta:
    base = dict(
        video_url="https://www.youtube.com/watch?v=abcdefghijk",
        channel_title="Jay Shetty · On Purpose",
        channel_url="https://www.youtube.com/@jayshetty",
        subscribers=6_010_000,
        duration_s=70 * 60 + 12,
        published=NOW - timedelta(hours=15),
        views=34_200,
        ages={"Khloé Kardashian": 40, "Jay Shetty": 36},
    )
    base.update(kw)
    return render.PostMeta(**base)


# ---------- форматування ----------

@pytest.mark.parametrize(
    "n,expected",
    [(950, "950"), (1_500, "1,5 тис."), (34_200, "34 тис."), (999_700, "1 млн"), (6_010_000, "6,01 млн"),
     (12_340_000, "12,3 млн"), (2_000_000, "2 млн"), (1_200_000_000, "1,2 млрд")],
)
def test_human_number(n, expected):
    assert render.human_number(n) == expected


def test_duration_and_ago():
    assert render.human_duration(70 * 60 + 12) == "1 год 10 хв"
    assert render.human_duration(45 * 60) == "45 хв"
    assert render.human_duration(2 * 3600) == "2 год"
    assert render.human_ago(NOW - timedelta(hours=15), NOW) == "15 год тому"
    assert render.human_ago(NOW - timedelta(minutes=5), NOW) == "5 хв тому"
    assert render.human_ago(NOW - timedelta(days=2), NOW) == "2 дні тому"
    assert render.human_ago(NOW - timedelta(days=5), NOW) == "5 днів тому"
    assert render.subscribers_label(1) == "1 підписник"
    assert render.subscribers_label(3) == "3 підписники"


def test_render_matches_template():
    msgs = render.render(sample_digest(), sample_meta(), "Europe/Kyiv", NOW)
    assert len(msgs) == 1
    m = msgs[0]
    print(m)
    assert m.startswith('🎙 <a href="https://www.youtube.com/watch?v=abcdefghijk">Хлої Кардаш')
    assert "📌 Психологія" in m
    # гість перед ведучим, з віком
    assert m.index("👤 Хлої Кардаш'ян – підприємниця") < m.index("🎤 Джей Шетті")
    assert "Good American (40)" in m and "On Purpose (36)" in m
    # теза гості є, у ведучого (без thesis_uk) її немає
    assert "↳ Сором можна подолати лише через кордони, а не публічність" in m
    assert m.count("↳") == 1
    assert "· 👥 6,01 млн підписників" in m
    assert "· ⏱ 1 год 10 хв" in m
    assert "🗓 15.09.26 (15 год тому)" in m  # 15:00 за Києвом 15.09
    assert "· 👁 34 тис." in m
    assert "&amp; відмову від &lt;нав'язаного&gt;" in m  # HTML екранується
    assert "📊 <b>ЧИСЛА ТА ФАКТИ</b>" in m and "• Пункт 1:" in m and "• Пункт 4:" in m
    # оцінка спікера — в окремому блоці, не серед фактів
    assert "🗣 <b>ОЦІНКИ ТА ПРОГНОЗИ</b>" in m
    assert m.index("• Пункт 4:") < m.index("🗣") < m.index("• Пункт 2:")
    # таймкод: посилання на 5 секунд раніше, підпис — точний момент
    assert '<a href="https://www.youtube.com/watch?v=abcdefghijk&amp;t=302s">⏱ 5:07</a>' in m
    assert '<a href="https://www.youtube.com/watch?v=abcdefghijk&amp;t=3718s">⏱ 1:02:03</a>' in m
    assert "⚔️ <b>ДЕ РОЗХОДЯТЬСЯ ДУМКИ</b>" in m
    assert "«Сором — це не моя ноша»" in m  # без подвійних лапок
    assert "🎯 <b>ЩО З ЦЬОГО ВИПЛИВАЄ</b>" in m
    assert "👀 <b>ЗА ЧИМ СТЕЖИТИ</b>" in m


def test_render_without_optional_fields():
    d = sample_digest()
    d.quotes = []
    d.disagreement = ""
    d.watch_next = ""
    d.category = ""
    m = render.render(d, sample_meta(subscribers=None, views=None, ages={}), "Europe/Kyiv", NOW)[0]
    assert "👥" not in m and "👁" not in m and "(40)" not in m and "💬" not in m
    assert "⚔️" not in m and "👀" not in m and "📌" not in m


def test_long_post_is_split_on_block_boundaries():
    msgs = render.render(sample_digest(n_facts=5, long=True), sample_meta(), "Europe/Kyiv", NOW)
    assert len(msgs) >= 2
    for m in msgs:
        assert render.visible_len(m) <= render.TG_LIMIT
        assert m.count("<b>") == m.count("</b>")
        assert m.count("<a ") == m.count("</a>")
    joined = "\n\n".join(msgs)
    for i in range(1, 6):
        assert f"• Пункт {i}:" in joined


def test_timestamps():
    assert render.parse_ts("5:07") == 307
    assert render.parse_ts("1:02:03") == 3723
    assert render.parse_ts("90:00") == 5400  # модель може дати хвилини понад 60
    assert render.parse_ts("5:70") is None and render.parse_ts("десь на початку") is None
    assert render.parse_ts("") is None
    url = "https://www.youtube.com/watch?v=abcdefghijk"
    assert render.ts_link("2:00", url, duration_s=4212) == f' <a href="{url}&amp;t=115s">⏱ 2:00</a>'
    assert render.ts_link("0:02", url, duration_s=4212).endswith('t=0s">⏱ 0:02</a>')  # не йдемо в мінус
    assert render.ts_link("3:00:00", url, duration_s=4212) == ""  # поза тривалістю — без посилання
    assert render.ts_link("", url, duration_s=4212) == ""


def test_opinion_detection():
    assert render.is_opinion("оцінка") and render.is_opinion("Оцінка") and render.is_opinion("прогноз")
    assert not render.is_opinion("факт") and not render.is_opinion("")


def test_visible_len_counts_utf16():
    assert render.visible_len("<b>1️⃣</b> &amp;") == len("1️⃣ &".encode("utf-16-le")) // 2


# ---------- YouTube ----------

def test_iso_duration():
    assert youtube.parse_iso_duration("PT1H10M12S") == 4212
    assert youtube.parse_iso_duration("PT59S") == 59
    assert youtube.parse_iso_duration("P1DT2H") == 93600
    assert youtube.parse_iso_duration("") == 0


FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015" xmlns:media="http://search.yahoo.com/mrss/" xmlns="http://www.w3.org/2005/Atom">
 <title>Jay Shetty Podcast</title>
 <entry>
  <id>yt:video:abcdefghijk</id>
  <yt:videoId>abcdefghijk</yt:videoId>
  <yt:channelId>UCchannel000000000000000</yt:channelId>
  <title>Khloé Kardashian on shame</title>
  <published>2026-09-15T12:00:00+00:00</published>
  <updated>2026-09-15T13:00:00+00:00</updated>
 </entry>
 <entry>
  <id>yt:video:oldoldoldol</id>
  <yt:videoId>oldoldoldol</yt:videoId>
  <title>Old one</title>
  <published>2026-09-01T12:00:00+00:00</published>
 </entry>
</feed>"""


def test_parse_feed():
    items = youtube.parse_feed(FEED, "UCchannel000000000000000")
    assert [i.video_id for i in items] == ["abcdefghijk", "oldoldoldol"]
    assert items[0].published == datetime(2026, 9, 15, 12, tzinfo=timezone.utc)


# ---------- Wikidata ----------

def _entity(qid, human=True, born="+1984-06-27T00:00:00Z", precision=11, dead=False):
    claims = {"P31": [{"mainsnak": {"datavalue": {"value": {"id": "Q5" if human else "Q4830453"}}}}]}
    if born:
        claims["P569"] = [{"mainsnak": {"datavalue": {"value": {"time": born, "precision": precision}}}}]
    if dead:
        claims["P570"] = [{"mainsnak": {}}]
    return {"id": qid, "claims": claims}


def test_wikidata_pick_age():
    today = date(2026, 9, 16)
    search = [{"id": "Q1", "label": "Khloé Kardashian"}, {"id": "Q2", "label": "Khloé Kardashian brand"}]
    ents = {"Q1": _entity("Q1"), "Q2": _entity("Q2", human=False)}
    assert wikidata.pick_age("Khloe Kardashian", search, ents, today) == 42  # діакритика ігнорується
    # тільки рік — вік не показуємо
    assert wikidata.pick_age("Khloé Kardashian", search, {"Q1": _entity("Q1", precision=9)}, today) is None
    # дві різні людини з таким іменем — неоднозначно
    s2 = [{"id": "Q1", "label": "John Smith"}, {"id": "Q3", "label": "John Smith"}]
    e2 = {"Q1": _entity("Q1"), "Q3": _entity("Q3", born="+1970-01-01T00:00:00Z")}
    assert wikidata.pick_age("John Smith", s2, e2, today) is None
    # померла людина
    assert wikidata.pick_age("Khloé Kardashian", search, {"Q1": _entity("Q1", dead=True)}, today) is None
    assert wikidata.age_on(date(1990, 9, 17), today) == 35


# ---------- Gemini ----------

class FakeModels:
    def __init__(self, behaviours):
        self.behaviours = list(behaviours)
        self.calls = []

    def generate_content(self, model, contents, config):
        self.calls.append((model, contents))
        b = self.behaviours.pop(0)
        if isinstance(b, Exception):
            raise b
        return SimpleNamespace(parsed=b, text=None)


def _client(behaviours):
    return SimpleNamespace(models=FakeModels(behaviours))


def test_gemini_uses_video_url_without_transcript_and_falls_back_on_quota():
    err429 = gemini.errors.ClientError(429, {"error": {"message": "quota", "status": "RESOURCE_EXHAUSTED"}})
    client = _client([err429, sample_digest()])
    d, model, calls_made = gemini.summarize("k", ["m1", "m2"], "prompt", "https://www.youtube.com/watch?v=abcdefghijk", None, client=client)
    assert model == "m2" and d.headline
    assert calls_made == 2
    part = client.models.calls[0][1][0]
    assert part.file_data.file_uri.endswith("abcdefghijk") and part.video_metadata.fps == gemini.VIDEO_FPS


def test_gemini_quota_exhausted_everywhere():
    err = lambda: gemini.errors.ClientError(429, {"error": {"message": "quota"}})
    with pytest.raises(gemini.QuotaExhausted) as exc_info:
        gemini.summarize("k", ["m1", "m2"], "p", "u", "text", client=_client([err(), err()]))
    assert exc_info.value.calls_made == 2


def test_gemini_too_large_stops_immediately():
    """400 про переповнений контекст — вікно однакове в усіх моделях, решту не пробуємо."""
    err = gemini.errors.ClientError(400, {"error": {
        "code": 400,
        "message": "The input token count exceeds the maximum number of tokens allowed 1048576.",
        "status": "INVALID_ARGUMENT",
    }})
    client = _client([err, sample_digest(), sample_digest(), sample_digest()])
    with pytest.raises(gemini.TooLarge) as exc_info:
        gemini.summarize("k", ["m1", "m2", "m3", "m4"], "p", "u", None, client=client)
    assert exc_info.value.calls_made == 1
    assert len(client.models.calls) == 1  # інші моделі навіть не смикали
    assert isinstance(exc_info.value, gemini.DigestError)


def test_too_large_video_is_skipped_without_failing_the_run(tmp_path, monkeypatch):
    """Задовге відео закривається як «пропущене», а не валить увесь запуск у червоне."""
    cfg = Config(
        channels=[ChannelEntry(handle="@JayShetty")],
        gemini_api_key="g", youtube_api_key="y", telegram_bot_token="t", telegram_chat_id="@c",
        gemini_models=["m1"], state_path=tmp_path / "state.json", lookback_hours=24 * 3650,
    )
    monkeypatch.setattr(app, "load_config", lambda: cfg)
    monkeypatch.setattr(youtube, "resolve_channel_id", lambda h, k: "UCchannel000000000000000")
    monkeypatch.setattr(youtube, "fetch_feed", lambda cid: youtube.parse_feed(FEED, cid)[:1])
    monkeypatch.setattr(youtube, "fetch_videos", lambda ids, key: {
        i: youtube.Video(i, "UCchannel000000000000000", "t", "JS", NOW - timedelta(hours=3), 4212, 34200, "none")
        for i in ids})
    monkeypatch.setattr(youtube, "fetch_channel", lambda cid, k: youtube.Channel(cid, "JS", 100, "@js"))
    monkeypatch.setattr(app.transcript, "get_transcript", lambda vid: None)

    def boom(*a, **k):
        e = gemini.TooLarge("відео не влазить у контекст моделі (1M токенів)")
        e.calls_made = 1
        raise e

    monkeypatch.setattr(gemini, "summarize", boom)
    sent = []
    monkeypatch.setattr(app.telegram, "send_message", lambda tok, chat, text: sent.append(text))

    assert app.main([]) == 0  # запуск зелений, попри те що відео не обробилось
    assert sent == []
    st = json.loads(cfg.state_path.read_text())
    entry = st["videos"]["abcdefghijk"]
    assert entry["status"] == "skipped" and entry["attempts"] == 0
    assert st["gemini_calls_today"] == 1  # витрачений запит усе одно врахований у бюджеті


def test_gemini_retries_invalid_output():
    bad = sample_digest(n_facts=1)
    client = _client([bad, sample_digest()])
    d, _, calls_made = gemini.summarize("k", ["m1"], "p", "u", "transcript", client=client)
    assert len(d.facts) == 4
    assert calls_made == 2
    assert client.models.calls[0][1][1].text.startswith("Транскрипт")


def test_prompt_template_formats():
    p = gemini.build_prompt(ROOT / "prompts" / "digest_uk.md", "T", "C", "2026-09-15")
    assert "Назва на YouTube: T" in p and "{" not in p


# ---------- повний прогін ----------

def test_end_to_end(tmp_path, monkeypatch):
    cfg = Config(
        channels=[ChannelEntry(handle="@JayShetty", name="Jay Shetty · On Purpose")],
        gemini_api_key="g", youtube_api_key="y", telegram_bot_token="t", telegram_chat_id="@c",
        gemini_models=["m1"], state_path=tmp_path / "state.json",
    )
    monkeypatch.setattr(app, "load_config", lambda: cfg)
    monkeypatch.setattr(youtube, "resolve_channel_id", lambda h, k: "UCchannel000000000000000")
    monkeypatch.setattr(youtube, "fetch_feed", lambda cid: youtube.parse_feed(FEED, cid) + [
        youtube.FeedItem("shortshort1", cid, "short", datetime.now(timezone.utc) - timedelta(hours=1)),
        youtube.FeedItem("toolongvid1", cid, "конференція на цілий день", datetime.now(timezone.utc) - timedelta(hours=1))])

    def fake_videos(ids, key):
        now = datetime.now(timezone.utc)
        out = {}
        for i in ids:
            dur = 50 if i == "shortshort1" else 31454 if i == "toolongvid1" else 4212
            out[i] = youtube.Video(i, "UCchannel000000000000000", "t", "JS", now - timedelta(hours=3), dur, 34200, "none")
        return out

    monkeypatch.setattr(youtube, "fetch_videos", fake_videos)
    monkeypatch.setattr(youtube, "fetch_channel", lambda cid, k: youtube.Channel(cid, "Jay Shetty", 6_010_000, "@jayshetty"))
    monkeypatch.setattr(app.transcript, "get_transcript", lambda vid: None)
    monkeypatch.setattr(gemini, "summarize", lambda *a, **k: (sample_digest(), "m1", 1))
    monkeypatch.setattr(wikidata, "get_age", lambda name, today=None: {"Jay Shetty": 38}.get(name))
    sent = []
    monkeypatch.setattr(app.telegram, "send_message", lambda tok, chat, text: sent.append(text) or len(sent))

    # FEED має дату 2026-09-15 — робимо lookback великим, щоб запис потрапив у вікно
    cfg.lookback_hours = 24 * 3650
    assert app.main([]) == 0
    assert len(sent) == 2  # max_videos_per_run = 2: abcdefghijk + oldoldoldol
    assert "(38)" in sent[0]
    st = json.loads(cfg.state_path.read_text())
    assert st["channel_ids"]["@jayshetty"] == "UCchannel000000000000000"
    assert st["videos"]["shortshort1"]["status"] == "skipped"
    assert st["videos"]["abcdefghijk"]["status"] == "posted"
    # 8+ год конференції відсікаються за тривалістю — без жодного запиту до Gemini
    assert st["videos"]["toolongvid1"]["status"] == "skipped"
    assert "задовге" in st["videos"]["toolongvid1"]["note"]

    # повторний запуск нічого не публікує
    assert app.main([]) == 0
    assert len(sent) == 2


def test_failure_is_retried_then_given_up(tmp_path):
    s = State(tmp_path / "s.json")
    for _ in range(3):
        assert not s.is_done("v")
        s.mark("v", "failed", "boom")
    assert s.is_done("v")


def test_budget_tracks_calls_and_rolls_over_at_day_boundary(tmp_path):
    s = State(tmp_path / "s.json")
    assert s.budget_remaining(NOW, default_budget=50) == 50
    s.record_gemini_calls(3, NOW)
    assert s.gemini_calls_today == 3
    assert s.budget_remaining(NOW, default_budget=50) == 47
    # наступна доба — лічильник скидається
    next_day = NOW + timedelta(days=1)
    assert s.budget_remaining(next_day, default_budget=50) == 50
    assert s.gemini_calls_today == 0


def test_budget_calibrates_down_on_quota_hit_with_floor(tmp_path):
    s = State(tmp_path / "s.json")
    s.record_gemini_calls(20, NOW)
    s.note_quota_hit(NOW, default_budget=50)
    # спостережена стеля 20 - запас 5 = 15
    assert s.gemini_daily_budget == 15
    assert s.budget_remaining(NOW, default_budget=50) == 0

    # наступного дня новий (нижчий) бюджет застосовується одразу з ранку
    next_day = NOW + timedelta(days=1)
    assert s.budget_remaining(next_day, default_budget=50) == 15

    # калібрування ніколи не опускає бюджет нижче MIN_BUDGET
    s2 = State(tmp_path / "s2.json")
    s2.record_gemini_calls(2, NOW)
    s2.note_quota_hit(NOW, default_budget=50)
    from digest.state import MIN_BUDGET
    assert s2.gemini_daily_budget == MIN_BUDGET

    # калібрування ніколи не підвищує вже калібрований (нижчий) бюджет
    s.record_gemini_calls(100, next_day)
    s.note_quota_hit(next_day, default_budget=50)
    assert s.gemini_daily_budget == 15


def test_budget_persists_across_save_and_load(tmp_path):
    path = tmp_path / "s.json"
    s = State(path)
    s.record_gemini_calls(5, NOW)
    s.note_quota_hit(NOW, default_budget=50)
    s.save()

    s2 = State(path)
    assert s2.gemini_day == NOW.strftime("%Y-%m-%d")
    assert s2.gemini_calls_today == 5
    assert s2.gemini_daily_budget == s.gemini_daily_budget


def test_extract_video_id():
    assert app.extract_video_id("https://www.youtube.com/watch?v=abcdefghijk&t=1") == "abcdefghijk"
    assert app.extract_video_id("https://youtu.be/abcdefghijk") == "abcdefghijk"
    assert app.extract_video_id("abcdefghijk") == "abcdefghijk"
