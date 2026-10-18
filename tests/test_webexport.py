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
        f"pico-do-arieiro/{day:%Y-%m-%d}/08_00.jpg"
    )


def test_history_snapshot_links_limited_to_seven_days():
    today = datetime(2026, 10, 18).date()
    rows = [{"date": "2026-10-11"}, {"date": "2026-10-10"}, {"date": "2026-10-18"}]
    out = webexport.with_snapshot_links(rows, today)
    assert out[0]["webcam_url"].endswith("/pico-do-arieiro/2026-10-11/08_00.jpg")
    assert "webcam_url" not in out[1]
    assert out[2]["webcam_url"].endswith("/2026-10-18/08_00.jpg")


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
    assert [s["key"] for s in spots["evening"]] == [
        "ponta_do_pargo", "cristo_rei", "arieiro_sunset", "pico_ruivo_sunset"
    ]
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
        "arieiro", "pico_ruivo", "bica_da_cana", "ponta_do_rosto", "ponta_do_pargo", "cristo_rei",
        "arieiro_sunset", "pico_ruivo_sunset",
    }
    for key, rows in sd.items():
        assert rows and rows[0]["date"] == payload["tomorrow"]["date"]
        assert "weather" in rows[0]


def test_sunrise_and_sunset_sections_list_every_spot():
    payload = webexport.build_payload(Settings(), days=3, bundle=_bundle(), now=NOW)
    expected = {
        "sunrise": {"arieiro", "pico_ruivo", "bica_da_cana", "ponta_do_rosto"},
        "sunset": {"ponta_do_pargo", "cristo_rei", "arieiro_sunset", "pico_ruivo_sunset"},
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


def _seeded_store(tmp_path, keys):
    """DB se zpětnými predikcemi (model historical_forecast) z fixture pro dané spoty."""
    from datetime import timedelta

    from madeira.services import forecast
    from madeira.storage.db import Store

    class FakeFetcher:
        def fetch_historical(self, locations, start, end):
            return _bundle().model_copy(update={"model": "historical_forecast"})

    store = Store(tmp_path / "t.db")
    end = NOW.date() + timedelta(days=1)
    forecast.fetch_history(Settings(), store, list(keys), days=1, end=end, fetcher=FakeFetcher())
    return store, end


def test_history_entries_carry_detail_metrics(tmp_path):
    store, end = _seeded_store(tmp_path, ["arieiro", "ponta_do_pargo"])
    arieiro = webexport.history_entries(store.history_rows(end, 7, "arieiro"), 1818)[0]
    for field in ("date", "score", "verdict", "summary", "actual_verdict", "time", "cloud_base",
                  "cloud_top", "cloud_cover_high", "cloud_cover_low", "humidity_peak",
                  "wind_speed", "wind_gusts", "temp"):
        assert field in arieiro
    assert arieiro["score"] == arieiro["score_pct"] and arieiro["source"] == "backfill"
    assert arieiro["inversion_case"] in {"none", "above", "edge", "inside"}
    assert arieiro["humidity_peak"] is not None and arieiro["actual_verdict"] is None
    assert arieiro["cloud_base"] is None or arieiro["cloud_top"] is None  # odhad základny jen bez vrstvy pod místem
    pargo = webexport.history_entries(store.history_rows(end, 7, "ponta_do_pargo"), 312)[0]
    assert pargo["humidity_peak"] is None and pargo["inversion_case"] is None  # profil jen u hor
    assert pargo["cloud_base"] is not None and pargo["visibility_km"] is not None
    json.dumps([arieiro, pargo])


def test_history_entry_maps_outcome_to_actual_verdict():
    row = {"target_date": "2026-10-10", "outcome": "cloud_sea", "model": None, "score": None,
           "verdict": None, "inversion_pct": None}
    e = webexport.history_entries([row])[0]
    assert e["actual_verdict"] == "GO" and e["summary"] is None and e["cloud_top"] is None


def test_each_spot_has_its_own_history(tmp_path):
    store, end = _seeded_store(tmp_path, forecast_keys := webexport.MORNING_SPOTS + webexport.EVENING_SPOTS)
    hist = webexport.collect_spot_history(Settings(), store, end, previous=tmp_path / "missing.json")
    assert set(hist) == set(forecast_keys) and all(hist[k] for k in forecast_keys)
    payload = webexport.build_payload(
        Settings(), days=3, bundle=_bundle(), now=NOW, spot_history=hist
    )
    for section in ("sunrise", "sunset"):
        for key, spot in payload[section]["spots"].items():
            got = [{k: v for k, v in row.items() if not k.startswith("webcam_")} for row in spot["history"]]
            assert got == hist[key]  # snímky NetMadeira (webcam_url) přibývají jen u Arieira
    assert payload["sunrise"]["history"] == payload["sunrise"]["spots"]["arieiro"]["history"]
    assert "webcam_url" not in payload["sunrise"]["spots"]["pico_ruivo"]["history"][0]
    json.dumps(payload)


def test_collect_spot_history_falls_back_to_previous_export(tmp_path, monkeypatch):
    from madeira.services import forecast
    from madeira.storage.db import Store

    def boom(*a, **k):
        raise RuntimeError("API down")

    monkeypatch.setattr(forecast, "fetch_history", boom)
    previous = tmp_path / "data.json"
    previous.write_text(json.dumps({
        "sunrise": {"spots": {"arieiro": {"history": [{"date": "2026-10-12"}]}}},
        "sunset": {"spots": {}},
    }), encoding="utf-8")
    hist = webexport.collect_spot_history(Settings(), Store(tmp_path / "e.db"), NOW.date(), previous)
    assert hist["arieiro"] == [{"date": "2026-10-12"}] and hist["cristo_rei"] == []


def test_mountain_sunset_spots_use_summit_location_data():
    payload = webexport.build_payload(Settings(), days=3, bundle=_bundle(), now=NOW)
    sunset = payload["sunset"]["spots"]
    arieiro, ruivo = sunset["arieiro_sunset"], sunset["pico_ruivo_sunset"]
    assert arieiro["location"]["elevation_m"] == 1818 and ruivo["location"]["elevation_m"] == 1862
    assert arieiro["location"]["type"] == "mountain_sunset" and arieiro["cloud_sea"] is None
    assert arieiro["webcam_url"].endswith("/pico-do-arieiro")
    assert arieiro["departure"] < arieiro["time"]
    sunrise = payload["sunrise"]["spots"]
    assert (arieiro["location"]["lat"], arieiro["location"]["lon"]) == (
        sunrise["arieiro"]["location"]["lat"], sunrise["arieiro"]["location"]["lon"])
    assert payload["spot_days"]["arieiro_sunset"][0]["weather"].keys() == {"funchal", "arieiro"}


def test_sunset_snapshot_links():
    from datetime import date
    assert webexport.sunset_snapshot_slot(date(2026, 10, 17)) == "19_30"
    assert webexport.sunset_webcam_url(date(2026, 10, 17)).endswith("/pico-do-arieiro/2026-10-17/19_30.jpg")
    rows = webexport.with_snapshot_links([{"date": "2026-10-17"}], date(2026, 10, 18), evening=True)
    assert rows[0]["webcam_time"] == "19:30" and rows[0]["webcam_url"].endswith("/19_30.jpg")
    assert rows[0]["webcam_times"] == ["19:30", "20:00"]
    assert rows[0]["webcam_urls"][1].endswith("/2026-10-17/20_00.jpg")
