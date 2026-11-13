import json
from datetime import UTC, date, datetime, timedelta

import pytest

from madeira.analyzers.sunrise import score_sunrise
from madeira.domain.config import Settings
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
    store.set_outcome(d, "MAYBE")
    store.set_outcome(d, "GO", notes="pěkné")
    assert store.get_outcome(d) == "GO"
    with pytest.raises(ValueError):
        store.set_outcome(d, "bogus")


def test_calibration_view_uses_last_prediction_before_sunrise(store, monkeypatch):
    # created_at se bere z hodin; zafixujeme ho před východem, ať test nezávisí na dnešku
    monkeypatch.setattr("madeira.storage.db._now", lambda: FETCHED.isoformat(timespec="seconds"))
    a, f = analysis_and_features()
    store.log_prediction(a, f, FETCHED, "best_match", {})
    store.set_outcome(a.target_date, "GO")
    rows = store.calibration_rows()
    assert len(rows) == 1
    assert rows[0]["outcome"] == "GO"
    assert rows[0]["score"] == a.score_pct


def test_outcome_without_prediction_still_listed(store):
    store.set_outcome(date(2026, 1, 1), "NO_GO")
    assert store.calibration_rows()[0]["score"] is None
