import sqlite3
from datetime import date, timedelta

import pytest
from typer.testing import CliRunner

from madeira.cli import app
from madeira.domain.config import Settings
from madeira.services import evaluation, forecast
from madeira.storage.db import Store
from tests.test_bot import FETCHED
from tests.test_webexport import NOW, _bundle

DAY = date(2026, 10, 13)


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "t.db")


def test_resolve_locations_aliases():
    assert forecast.resolve_locations("pico_arieiro") == ["arieiro"]
    assert forecast.resolve_locations("all") == list(forecast.ALL_LOCATIONS)
    with pytest.raises(ValueError):
        forecast.resolve_locations("nikde")


def test_outcomes_are_per_location(store):
    store.set_outcome(DAY, "GO", location="ponta_do_pargo", notes="top")
    store.set_outcome(DAY, "fail")
    assert store.get_outcome(DAY, "ponta_do_pargo") == "GO"
    assert store.get_outcome(DAY) == "fail"
    assert store.rated_dates("ponta_do_pargo") == [DAY]
    with pytest.raises(ValueError):
        store.set_outcome(DAY, "bogus", location="ponta_do_pargo")


def test_legacy_database_is_migrated(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """CREATE TABLE outcomes (target_date TEXT PRIMARY KEY, outcome TEXT NOT NULL,
               rated_at TEXT NOT NULL, chat_id INTEGER);
           INSERT INTO outcomes VALUES ('2026-10-13', 'clear', '2026-10-13T10:00:00', 1);"""
    )
    conn.commit()
    conn.close()
    store = Store(path)
    assert store.get_outcome(DAY, "arieiro") == "clear"
    assert store.get_outcome(DAY, "ponta_do_pargo") is None


def test_fetch_history_stores_predictions_for_every_spot(store):
    class FakeFetcher:
        def fetch_historical(self, locations, start, end):
            return _bundle()

    end = NOW.date() + timedelta(days=1)
    counts = forecast.fetch_history(
        Settings(), store, list(forecast.ALL_LOCATIONS), days=1, end=end, fetcher=FakeFetcher()
    )
    assert counts == dict.fromkeys(forecast.ALL_LOCATIONS, 1)
    for key in forecast.ALL_LOCATIONS:
        row = store.latest_prediction(end, key)
        assert row is not None
    # opakované stažení nezdvojí predikce zpětně dopočítaného dne
    forecast.fetch_history(Settings(), store, ["ponta_do_pargo"], days=1, end=end, fetcher=FakeFetcher())


def _rows(pairs):
    out = []
    for i, (score, verdict, outcome) in enumerate(pairs):
        out.append({
            "target_date": f"2026-09-{i + 1:02d}", "outcome": outcome, "score": score,
            "verdict": verdict, "notes": None, "components_json": "[]",
            "features_json": '{"horizon_low_cloud": %d}' % (50 if verdict == "GO" else 5),
        })
    return out


def test_evaluate_precision_recall_and_error_lists():
    rows = _rows([
        (90, "GO", "GO"), (85, "GO", "NO_GO"),   # TP, FP
        (20, "NO_GO", "GO"), (10, "NO_GO", "NO_GO"),  # FN, TN
        (50, "MAYBE", "cloud_sea"),  # starý klíč se mapuje na GO -> FN
    ])
    ev = evaluation.evaluate(rows, "ponta_do_pargo", "sunset", 70)
    assert (ev.go["tp"], ev.go["fp"], ev.go["fn"]) == (1, 1, 2)
    assert ev.go["precision"] == 0.5 and ev.go["recall"] == pytest.approx(1 / 3)
    assert [c.date for c in ev.false_positives] == ["2026-09-02"]
    assert len(ev.false_negatives) == 2
    assert ev.recommendations  # málo dat + práh


def test_evaluate_skips_rows_without_prediction():
    rows = [{"target_date": "2026-09-01", "outcome": "GO", "score": None, "verdict": None}]
    ev = evaluation.evaluate(rows, "bica_da_cana", "sunrise", 70)
    assert ev.skipped_no_prediction == 1 and not ev.cases


def test_label_and_import_cli(tmp_path, monkeypatch):
    monkeypatch.setenv("MADEIRA_DB", str(tmp_path / "cli.db"))
    runner = CliRunner()
    csv_file = tmp_path / "l.csv"
    csv_file.write_text(
        "date,location,actual_verdict,notes\n"
        "2026-09-20,ponta_do_pargo,NO-GO,zataženo\n"
        "2026-09-21,ponta_do_pargo,bad,\n",
        encoding="utf-8",
    )
    res = runner.invoke(app, ["import-labels", str(csv_file)])
    assert res.exit_code == 1 and "Importováno: 1" in res.output
    store = Store(tmp_path / "cli.db")
    assert store.get_outcome(date(2026, 9, 20), "ponta_do_pargo") == "NO_GO"
    res = runner.invoke(app, ["evaluate", "--location", "ponta_do_pargo"])
    assert res.exit_code == 0 and "bez predikce" in res.output


def test_log_sunset_prediction(store):
    from madeira.analyzers.sunset import extract_sunset_features, score_sunset

    settings = Settings()
    f = extract_sunset_features(_bundle(), NOW.date() + timedelta(days=1), NOW, settings)
    a = score_sunset(f, settings)
    assert store.log_prediction(a, f, FETCHED, "best_match", {}, location="ponta_do_pargo")
    assert store.latest_prediction(a.target_date, "ponta_do_pargo")["inversion_pct"] is None
    assert store.latest_prediction(a.target_date) is None
