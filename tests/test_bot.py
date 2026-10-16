import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from madeira.analyzers.sunrise import score_sunrise
from madeira.bot import app as bot
from madeira.domain.config import Settings
from madeira.domain.models import OUTCOME_LABELS
from madeira.presenters import telegram as fmt
from madeira.storage.db import Store
from tests.test_analyzers import features

FETCHED = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "t.db")


def analysis_and_features(**over):
    f = features(**over)
    return score_sunrise(f, Settings()), f


def test_log_prediction_dedupes_same_forecast(store):
    a, f = analysis_and_features()
    assert store.log_prediction(a, f, FETCHED, "best_match", {}) is True
    assert store.log_prediction(a, f, FETCHED, "best_match", {}) is False
    assert store.log_prediction(a, f, FETCHED + timedelta(hours=1), "best_match", {}) is True
    pred = store.latest_prediction(a.target_date)
    assert pred["score"] == a.score_pct
    assert json.loads(pred["features_json"])["low_cloud_summit"] == f.low_cloud_summit


def test_outcome_upsert_and_validation(store):
    d = date(2026, 10, 13)
    store.set_outcome(d, "clear", 1)
    store.set_outcome(d, "cloud_sea", 1)
    assert store.get_outcome(d) == "cloud_sea"
    with pytest.raises(ValueError):
        store.set_outcome(d, "bogus")


def test_calibration_view_uses_last_prediction_before_sunrise(store, monkeypatch):
    # created_at se bere z hodin; zafixujeme ho před východem, ať test nezávisí na dnešku
    monkeypatch.setattr("madeira.storage.db._now", lambda: FETCHED.isoformat(timespec="seconds"))
    a, f = analysis_and_features()
    store.log_prediction(a, f, FETCHED, "best_match", {})
    store.set_outcome(a.target_date, "cloud_sea")
    rows = store.calibration_rows()
    assert len(rows) == 1
    assert rows[0]["outcome"] == "cloud_sea"
    assert rows[0]["score"] == a.score_pct


def test_outcome_without_prediction_still_listed(store):
    store.set_outcome(date(2026, 1, 1), "fail")
    assert store.calibration_rows()[0]["score"] is None


def test_subscribers(store):
    store.subscribe(5)
    store.subscribe(5)
    store.subscribe(7)
    store.unsubscribe(7)
    assert store.subscribers() == [5]


def test_formatters_produce_html_with_key_facts():
    a, _ = analysis_and_features()
    text = fmt.format_sunrise(a)
    assert "GO" in text and f"{a.score_pct:.0f} %" in text
    digest = fmt.format_digest(a, [a], departure_before_min=80)
    assert "Odjezd z Funchalu" in digest
    bad, _ = analysis_and_features(precip_mm=2, precip_prob=95)
    assert "spi" in fmt.format_digest(bad, [bad], 80)
    assert "<pre>" in fmt.format_week([a])


def test_feedback_keyboard_covers_all_outcomes():
    kb = bot.feedback_keyboard(date(2026, 10, 13))
    data = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert all(len(d.encode()) <= 64 for d in data)
    for key in OUTCOME_LABELS:
        assert f"fb:2026-10-13:{key}" in data


def _context(store, allowed=()):
    app = SimpleNamespace(
        bot_data={"store": store, "settings": Settings(), "allowed_chats": set(allowed)}
    )
    return SimpleNamespace(application=app, args=[])


def _callback_update(data, chat_id=42):
    query = MagicMock()
    query.data = data
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    return SimpleNamespace(callback_query=query, effective_chat=SimpleNamespace(id=chat_id)), query


@pytest.fixture
def no_network(monkeypatch):
    """Hodnocení by si jinak stáhlo historická data; nahradíme to atrapou."""
    from madeira.services import forecast as fc

    monkeypatch.setattr(fc, "today_local", lambda: date(2026, 10, 20))
    monkeypatch.setattr(fc, "backfill_prediction", lambda d, s, st: analysis_and_features()[0])


def test_feedback_button_writes_outcome(store, no_network):
    update, query = _callback_update("fb:2026-10-13:cloud_sea")
    asyncio.run(bot.cb_feedback(update, _context(store)))
    assert store.get_outcome(date(2026, 10, 13)) == "cloud_sea"
    assert "Zapsáno" in query.edit_message_text.call_args.args[0]


def test_feedback_skip_and_garbage_write_nothing(store, no_network):
    for data in ("fb:2026-10-13:skip", "fb:nonsense", "fb:2026-10-13:evil"):
        update, _ = _callback_update(data)
        asyncio.run(bot.cb_feedback(update, _context(store)))
    assert store.get_outcome(date(2026, 10, 13)) is None


def test_unauthorized_chat_is_ignored(store, no_network):
    update, query = _callback_update("fb:2026-10-13:clear", chat_id=999)
    asyncio.run(bot.cb_feedback(update, _context(store, allowed={42})))
    assert store.get_outcome(date(2026, 10, 13)) is None
    query.answer.assert_not_called()


def test_application_registers_handlers_and_jobs(store):
    application = bot.build_application("123456:TEST", store, Settings())
    names = {j.name for j in application.job_queue.jobs()}
    assert names == {"digest", "feedback"}
    assert len(application.handlers[0]) == 8  # 7 příkazů + callback


# --- zpětné dopočítání predikce při hodnocení ---------------------------------
from madeira.services import forecast  # noqa: E402


def test_record_outcome_backfills_missing_prediction(store, monkeypatch):
    day = date(2026, 10, 13)
    calls = []

    def fake_backfill(d, settings, st):
        a, f = analysis_and_features()
        st.log_prediction(a, f, FETCHED, "historical_forecast", {})
        calls.append(d)
        return a

    monkeypatch.setattr(forecast, "backfill_prediction", fake_backfill)
    monkeypatch.setattr(forecast, "today_local", lambda: date(2026, 10, 20))

    r = forecast.record_outcome(day, "clear", Settings(), store)
    assert r.backfilled and calls == [day]
    assert store.get_outcome(day) == "clear"
    assert store.calibration_rows()[0]["model"] == "historical_forecast"

    forecast.record_outcome(day, "fail", Settings(), store)  # predikce už je → bez stahování
    assert calls == [day]


def test_record_outcome_saves_even_if_backfill_fails(store, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("API mimo rozsah")

    monkeypatch.setattr(forecast, "backfill_prediction", boom)
    monkeypatch.setattr(forecast, "today_local", lambda: date(2026, 10, 20))
    r = forecast.record_outcome(date(2021, 1, 1), "fail", Settings(), store)
    assert not r.backfilled and "API mimo rozsah" in r.warning
    assert store.get_outcome(date(2021, 1, 1)) == "fail"


def test_future_day_cannot_be_rated(store, monkeypatch):
    monkeypatch.setattr(forecast, "today_local", lambda: date(2026, 10, 12))
    with pytest.raises(ValueError):
        forecast.record_outcome(date(2026, 10, 13), "clear", Settings(), store)
    assert store.get_outcome(date(2026, 10, 13)) is None
