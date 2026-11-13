from datetime import date

import pytest

from madeira.domain.config import Settings
from madeira.services import ratings
from madeira.storage.db import Store

DAY = date(2026, 11, 12)


def test_rate_writes_file_with_event_from_config(tmp_path):
    path = tmp_path / "ratings.json"
    entry = ratings.rate(DAY, "ponta_do_pargo", "go", " pěkný ", settings=Settings(), path=path)
    assert entry == {"verdict": "GO", "event": "sunset", "note": "pěkný"}
    assert ratings.load(path)[(DAY, "ponta_do_pargo")] == entry
    assert "chat_id" not in path.read_text(encoding="utf-8")


def test_rate_overwrites_and_keeps_days_sorted(tmp_path):
    path = tmp_path / "r.json"
    ratings.rate(DAY, "arieiro", "MAYBE", settings=Settings(), path=path)
    ratings.rate(date(2026, 11, 1), "arieiro", "NO_GO", settings=Settings(), path=path)
    ratings.rate(DAY, "arieiro", "GO", settings=Settings(), path=path)
    assert list(ratings.load_raw(path)["ratings"]) == ["2026-11-01", "2026-11-12"]
    assert ratings.load(path)[(DAY, "arieiro")]["verdict"] == "GO"


def test_rate_rejects_bad_input(tmp_path):
    path = tmp_path / "r.json"
    with pytest.raises(ValueError):
        ratings.rate(DAY, "arieiro", "skvělé", settings=Settings(), path=path)
    with pytest.raises(ValueError):  # spot, který model nezná, potřebuje event
        ratings.rate(DAY, "ponta_da_ladeira_sunset", "GO", settings=Settings(), path=path)
    with pytest.raises(ValueError):  # budoucnost
        ratings.rate(date(2026, 11, 20), "arieiro", "GO", settings=Settings(), today=DAY, path=path)
    assert not path.exists()
    ratings.rate(DAY, "ponta_da_ladeira_sunset", "NO_GO", event="sunset", settings=Settings(), path=path)


def test_sync_to_store_skips_unmodeled_spots(tmp_path):
    path = tmp_path / "r.json"
    s = Settings()
    ratings.rate(DAY, "arieiro", "GO", "dobrý", settings=s, path=path)
    ratings.rate(DAY, "bica_da_cana_sunset", "GO", event="sunset", settings=s, path=path)
    store = Store(tmp_path / "t.db")
    assert ratings.sync_to_store(store, s, path) == 1
    assert store.get_outcome(DAY, "arieiro") == "GO"
    assert store.get_outcome(DAY, "bica_da_cana_sunset") is None
    assert store.history_rows(DAY)[0]["note"] == "dobrý"


def test_committed_ratings_file_is_valid():
    """Soubor v repu: platné verdikty a spoty, žádné osobní údaje."""
    raw = ratings.RATINGS_PATH.read_text(encoding="utf-8")
    assert "chat_id" not in raw
    for (_, spot), entry in ratings.load().items():
        assert entry["verdict"] in ratings.VERDICTS and entry["event"] in ("sunrise", "sunset"), spot


def test_history_entry_carries_rating_and_note(tmp_path):
    from madeira.services import webexport

    path = tmp_path / "r.json"
    ratings.rate(DAY, "arieiro", "GO", "dobrý", settings=Settings(), path=path)
    store = Store(tmp_path / "t.db")
    ratings.sync_to_store(store, Settings(), path)
    entry = webexport.history_entries(store.history_rows(DAY))[0]
    assert entry["actual_verdict"] == "GO" and entry["note"] == "dobrý"
