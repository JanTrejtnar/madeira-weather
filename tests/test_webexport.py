import json
from datetime import UTC, datetime

from madeira.domain.config import Settings, load_locations
from madeira.fetchers.open_meteo import parse_response
from madeira.services import webexport
from tests.test_analyzers import FIXTURE, NOW


def _bundle():
    return parse_response(json.loads(FIXTURE.read_text()), load_locations(), "best_match", NOW)


def test_payload_shape(tmp_path):
    payload = webexport.build_payload(Settings(), days=3, bundle=_bundle(), now=NOW)
    t = payload["tomorrow"]
    assert t["date"] == "2026-10-13"
    assert t["verdict"] in {"GO", "MAYBE", "NO_GO"}
    assert 0 <= t["score_pct"] <= 100
    assert t["departure_funchal"] < t["sunrise"]
    assert t["inversion_case"] in {"none", "above", "edge", "inside"}
    assert payload["history"] == []
    assert len(payload["days"]) == 3
    out = tmp_path / "docs" / "data.json"
    webexport.write_payload(payload, out)
    assert json.loads(out.read_text(encoding="utf-8"))["tomorrow"] == t
    assert datetime.fromisoformat(payload["generated_at"]).tzinfo == UTC


def test_payload_has_morning_webcam_url():
    payload = webexport.build_payload(Settings(), days=3, bundle=_bundle(), now=NOW)
    day = NOW.astimezone(UTC).date()
    assert payload["morning_webcam_date"] == day.isoformat()
    assert payload["morning_webcam_url"] == (
        "https://www.netmadeira.com/assets/images/services/webcams/timelapses/netmadeira/"
        f"pico-do-arieiro/24h/{day:%Y_%m_%d}_08_00.jpg"
    )


def test_rain_levels():
    from madeira.analyzers.daily_weather import rain_level

    assert rain_level(0.0, 0.0) == "none"
    assert rain_level(0.5, 0.3) == "drizzle"
    assert rain_level(1.5, 0.8) == "light"
    assert rain_level(4, 2) == "moderate"
    assert rain_level(3, 5) == "heavy"
    assert rain_level(12, 1) == "heavy"


def test_payload_has_weather():
    payload = webexport.build_payload(Settings(), days=3, bundle=_bundle(), now=NOW)
    w = payload["tomorrow"]["weather"]["funchal"]
    assert w["rain"] in {"none", "drizzle", "light", "moderate", "heavy"}


def test_read_history_fallback(tmp_path):
    assert webexport.read_history(tmp_path / "missing.json") == []
    f = tmp_path / "data.json"
    f.write_text('{"history": [{"date": "2026-10-13"}]}', encoding="utf-8")
    assert webexport.read_history(f) == [{"date": "2026-10-13"}]
    f.write_text("not json", encoding="utf-8")
    assert webexport.read_history(f) == []


def test_history_rows_include_unrated_days(tmp_path, monkeypatch):
    from datetime import date

    from madeira.storage.db import Store

    monkeypatch.setattr("madeira.storage.db._now", lambda: NOW.isoformat(timespec="seconds"))
    store = Store(tmp_path / "t.db")
    webexport.build_payload(Settings(), days=2, bundle=_bundle(), now=NOW, store=store)
    rows = store.history_rows(date(2026, 10, 20))
    assert [r["target_date"] for r in rows] == ["2026-10-14", "2026-10-13"]
    assert all(r["outcome"] is None and r["score"] is not None for r in rows)
    assert store.history_rows(date(2026, 10, 13))[0]["target_date"] == "2026-10-13"
    assert webexport.history_entries(rows)[0]["outcome"] is None


def test_payload_has_tomorrow_spots():
    payload = webexport.build_payload(Settings(), days=3, bundle=_bundle(), now=NOW)
    spots = payload["tomorrow_spots"]
    assert [s["key"] for s in spots["morning"]] == [
        "arieiro", "pico_ruivo", "bica_da_cana", "ponta_do_rosto"
    ]
    assert [s["key"] for s in spots["evening"]] == ["ponta_do_pargo", "cristo_rei"]
    assert spots["morning"][0]["score_pct"] == payload["tomorrow"]["score_pct"]
    assert spots["morning"][1]["elevation_m"] == 1862
    assert spots["morning"][2]["elevation_m"] == 1560
    pargo = spots["evening"][0]
    assert pargo["date"] == payload["tomorrow"]["date"]
    assert pargo["departure_funchal"] < pargo["sunset"]
    assert pargo["verdict"] in {"GO", "MAYBE", "NO_GO"}
    for spot in spots["morning"] + spots["evening"]:  # mapa potřebuje souřadnice
        assert isinstance(spot["lat"], float) and isinstance(spot["lon"], float)
    json.dumps(payload)  # musí jít serializovat


def test_payload_has_sunrise_and_sunset_roots():
    payload = webexport.build_payload(Settings(), days=3, bundle=_bundle(), now=NOW)
    for key, time_key in (("sunrise", "arieiro"), ("sunset", "ponta_do_pargo")):
        s = payload[key]
        assert s["location"]["key"] == time_key
        for field in ("time", "departure", "verdict", "score", "cloud_sea", "wind",
                      "clouds_canvas", "summary"):
            assert field in s
        assert s["departure"] < s["time"]
        assert set(s["wind"]) == {"kmh", "gust_kmh", "dir_deg"}
    assert payload["sunrise"]["score"] == payload["tomorrow"]["score_pct"]
    assert payload["sunrise"]["cloud_sea"]["probability_pct"] == payload["tomorrow"]["inversion_pct"]
    assert payload["sunrise"]["webcam_url"].endswith("/pico-do-arieiro")
    assert payload["sunset"]["webcam_url"].endswith("/ponta-do-pargo")
    assert payload["tomorrow_spots"]["morning"][2]["webcam_url"] is None  # Bica: bez kamery
    assert payload["sunset"]["cloud_sea"] is None  # pobřežní západ: moře mraků se neskóruje
    json.dumps(payload)


def test_spot_days_cover_every_spot():
    payload = webexport.build_payload(Settings(), days=3, bundle=_bundle(), now=NOW)
    sd = payload["spot_days"]
    assert set(sd) == {
        "arieiro", "pico_ruivo", "bica_da_cana", "ponta_do_rosto", "ponta_do_pargo", "cristo_rei"
    }
    for key, rows in sd.items():
        assert rows and rows[0]["date"] == payload["tomorrow"]["date"]
        assert "weather" in rows[0]


def test_sunrise_and_sunset_sections_list_every_spot():
    payload = webexport.build_payload(Settings(), days=3, bundle=_bundle(), now=NOW)
    expected = {
        "sunrise": {"arieiro", "pico_ruivo", "bica_da_cana", "ponta_do_rosto"},
        "sunset": {"ponta_do_pargo", "cristo_rei"},
    }
    for section, keys in expected.items():
        spots = payload[section]["spots"]
        assert set(spots) == keys
        for s in spots.values():
            assert isinstance(s["location"]["lat"], float) and isinstance(s["location"]["lon"], float)
            for field in ("verdict", "score", "departure", "summary"):
                assert s[field] is not None
            assert s["departure"] < s["time"]
    assert payload["sunrise"]["spots"]["arieiro"]["score"] == payload["sunrise"]["score"]
    assert payload["sunrise"]["spots"]["pico_ruivo"]["cloud_sea"] is not None  # horská inverze
    rosto = payload["sunrise"]["spots"]["ponta_do_rosto"]
    assert rosto["cloud_sea"] is None and rosto["location"]["type"] == "coastal_sunrise"
    assert rosto["horizon_low_cloud_pct"] is not None
    assert payload["sunset"]["spots"]["cristo_rei"]["location"]["elevation_m"] == 180
    json.dumps(payload)
