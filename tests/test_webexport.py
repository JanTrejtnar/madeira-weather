import json
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

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
    assert payload["morning_webcam_date"] == day.isoformat()  # NOW = 13:00 místního času → dnešní ráno
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


def test_morning_snapshots_have_second_photo():
    payload = webexport.build_payload(Settings(), days=3, bundle=_bundle(), now=NOW)
    assert payload["morning_webcam_times"] == ["08:00", "08:30"]
    assert payload["morning_webcam_urls"][1].endswith("/08_30.jpg")


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
    assert pargo["date"] == "2026-10-12" and pargo["relative"] == "today"  # NOW = 13:00, západ ještě nenastal
    assert spots["morning"][0]["date"] == payload["tomorrow"]["date"]
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
    assert payload["sunset"]["webcam_url"] is None  # NetMadeira Ponta do Pargo vrací "Invalid webcam"
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
        expected = "2026-10-12" if key in webexport.EVENING_SPOTS else payload["tomorrow"]["date"]
        assert rows and rows[0]["date"] == expected
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


def _seeded_store(tmp_path, keys, days=1):
    """DB se zpětnými predikcemi (model historical_forecast) z fixture pro dané spoty."""
    from datetime import timedelta

    from madeira.services import forecast
    from madeira.storage.db import Store

    class FakeFetcher:
        def fetch_historical(self, locations, start, end):
            return _bundle().model_copy(update={"model": "historical_forecast"})

    store = Store(tmp_path / "t.db")
    end = NOW.date() + timedelta(days=1)
    forecast.fetch_history(Settings(), store, list(keys), days=days, end=end, fetcher=FakeFetcher())
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
    hist = webexport.collect_spot_history(
        Settings(), store, end + timedelta(days=1), previous=tmp_path / "missing.json"
    )
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
    monkeypatch.setattr(forecast, "backfill_from_live", lambda *a, **k: 0)
    hist = webexport.collect_spot_history(
        Settings(), Store(tmp_path / "e.db"), date(2026, 10, 13), previous
    )
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


MADEIRA = ZoneInfo("Atlantic/Madeira")
DAY = date(2026, 10, 12)  # fixture pokrývá 12.–18. 10.


def _at(hour, minute):
    return datetime(2026, 10, 12, hour, minute, tzinfo=MADEIRA)


def _payload_at(hour, minute):
    return webexport.build_payload(Settings(), days=3, bundle=_bundle(), now=_at(hour, minute))


def test_sun_event_times_are_madeira_local():
    assert webexport.sunrise_local(DAY).strftime("%H:%M") in {"08:06", "08:07", "08:08", "08:09", "08:10"}
    assert webexport.sunset_local(DAY).hour == 19
    assert webexport.sunrise_local(DAY).tzinfo is not None


def test_next_event_day_switches_after_the_event():
    assert webexport.next_event_day(_at(6, 0), "sunrise") == DAY
    assert webexport.next_event_day(_at(8, 30), "sunrise") == DAY + timedelta(days=1)
    assert webexport.next_event_day(_at(15, 30), "sunset") == DAY
    assert webexport.next_event_day(_at(21, 15), "sunset") == DAY + timedelta(days=1)


def test_morning_run_0830_targets_tomorrow_sunrise_and_todays_sunset_and_photo():
    p = _payload_at(8, 30)
    assert p["today"] == "2026-10-12" and p["timezone"] == "Atlantic/Madeira"
    assert p["tomorrow"]["target_date"] == "2026-10-13" and p["tomorrow"]["relative"] == "tomorrow"
    assert p["sunrise"]["target_date"] == "2026-10-13" and p["sunrise"]["relative"] == "tomorrow"
    assert p["sunset"]["target_date"] == "2026-10-12" and p["sunset"]["relative"] == "today"
    assert p["morning_webcam_date"] == "2026-10-12" and p["morning_webcam_day"] == "today"
    assert p["morning_webcam_url"].endswith("/pico-do-arieiro/2026-10-12/08_00.jpg")
    assert p["sunset_webcam_date"] == "2026-10-11" and p["sunset_webcam_day"] == "yesterday"


def test_photo_before_0830_is_yesterdays_morning():
    p = _payload_at(8, 29)
    assert p["morning_webcam_date"] == "2026-10-11" and p["morning_webcam_day"] == "yesterday"
    assert p["morning_webcam_url"].endswith("/2026-10-11/08_00.jpg")


def test_before_sunrise_targets_todays_sunrise():
    p = _payload_at(6, 30)
    assert p["tomorrow"]["target_date"] == "2026-10-12" and p["tomorrow"]["relative"] == "today"
    assert p["days"][0]["relative"] == "today" and p["days"][1]["relative"] == "tomorrow"
    assert p["days"][2]["relative"] is None
    assert p["morning_webcam_day"] == "yesterday"


def test_afternoon_run_1530_keeps_todays_sunset_and_tomorrows_sunrise():
    p = _payload_at(15, 30)
    assert p["sunrise"]["target_date"] == "2026-10-13"
    assert p["sunset"]["target_date"] == "2026-10-12" and p["sunset"]["relative"] == "today"
    assert p["morning_webcam_day"] == "today"


def test_evening_run_2115_closes_today_and_targets_tomorrow():
    p = _payload_at(21, 15)
    assert p["sunrise"]["target_date"] == "2026-10-13" and p["sunrise"]["relative"] == "tomorrow"
    assert p["sunset"]["target_date"] == "2026-10-13" and p["sunset"]["relative"] == "tomorrow"
    assert p["sunset_webcam_date"] == "2026-10-12" and p["sunset_webcam_day"] == "today"
    for rows in p["spot_days"].values():
        assert rows[0]["target_date"] == rows[0]["date"] == "2026-10-13"
    assert p["generated_local"].endswith("+01:00")
    assert datetime.fromisoformat(p["generated_at"]).utcoffset() == timedelta(0)


def test_relative_label_uses_madeira_date_not_utc():
    # 00:30 místního času 13. 10. je ještě 12. 10. v UTC → „dnes“ musí být 13. 10.
    late = datetime(2026, 10, 13, 0, 30, tzinfo=MADEIRA)
    assert late.astimezone(UTC).date() == date(2026, 10, 12)
    e = webexport.stamp_target({"date": "2026-10-13"}, late.astimezone(UTC))
    assert e["relative"] == "today" and e["target_date"] == "2026-10-13"


def _seed_week(tmp_path, today, days=7):
    from madeira.services import forecast
    from madeira.storage.db import Store

    class FakeFetcher:
        def fetch_historical(self, locations, start, end):
            return _bundle().model_copy(update={"model": "historical_forecast"})

    store = Store(tmp_path / "w.db")
    keys = list(webexport.MORNING_SPOTS + webexport.EVENING_SPOTS)
    forecast.fetch_history(
        Settings(), store, keys, days=days, end=today - timedelta(days=1), fetcher=FakeFetcher()
    )
    return store


def test_history_starts_with_yesterday_for_every_spot(tmp_path):
    today = date(2026, 10, 19)  # včera = 18. 10., okno 12.–18. 10.
    store = _seed_week(tmp_path, today)
    hist = webexport.collect_spot_history(Settings(), store, today, previous=tmp_path / "none.json")
    window = webexport.history_window(today)
    assert window[0] == "2026-10-18" and window[-1] == "2026-10-12" and len(window) == 7
    for key, rows in hist.items():
        assert [r["date"] for r in rows] == window, key


def test_history_excludes_today(tmp_path):
    today = date(2026, 10, 19)
    store = _seed_week(tmp_path, today)  # včerejšek 18. 10. je v DB
    hist = webexport.collect_spot_history(Settings(), store, date(2026, 10, 18), previous=tmp_path / "none.json")
    assert all(r["date"] <= "2026-10-17" for rows in hist.values() for r in rows)


def test_missing_yesterday_is_backfilled_from_live_model(tmp_path, monkeypatch):
    from madeira.services import forecast

    today = date(2026, 10, 19)
    store = _seed_week(tmp_path, today)
    store.delete_backfilled(date(2026, 10, 18), "arieiro")
    calls = []

    def down(*a, **k):
        raise RuntimeError("API")

    monkeypatch.setattr(forecast, "fetch_history", down)
    monkeypatch.setattr(forecast, "backfill_from_live", lambda s, st, keys, day: calls.append((keys, day)) or 0)
    webexport.collect_spot_history(Settings(), store, today, previous=tmp_path / "none.json")
    assert calls == [(["arieiro"], date(2026, 10, 18))]


def test_previous_export_keeps_yesterday_when_archive_is_down(tmp_path, monkeypatch):
    from madeira.services import forecast
    from madeira.storage.db import Store

    def down(*a, **k):
        raise RuntimeError("API")

    monkeypatch.setattr(forecast, "fetch_history", down)
    monkeypatch.setattr(forecast, "backfill_from_live", lambda *a, **k: 0)
    previous = tmp_path / "data.json"
    previous.write_text(json.dumps({
        "sunrise": {"spots": {"arieiro": {"history": [{"date": "2026-10-18"}, {"date": "2026-10-01"}]}}},
        "sunset": {"spots": {}},
    }), encoding="utf-8")
    hist = webexport.collect_spot_history(Settings(), Store(tmp_path / "e.db"), date(2026, 10, 19), previous)
    assert hist["arieiro"] == [{"date": "2026-10-18"}]  # starší než týden se zahodí


def test_webcams_section_valid():
    payload = webexport.build_payload(Settings(), days=3, bundle=_bundle(), now=NOW)
    cams = payload["webcams"]
    assert len(cams) >= 12
    assert len({c["id"] for c in cams}) == len(cams)
    assert {c["category"] for c in cams} == {"mountain", "forest", "sunset", "coast"}
    for c in cams:
        assert c["snapshot_url"].startswith("https://www.netmadeira.com/") and c["snapshot_url"].endswith(".jpg")
        assert c["fallback_url"] != c["snapshot_url"]
        assert c["live_url"].startswith("https://www.netmadeira.com/webcams-madeira/")
        assert "{" not in c["snapshot_url"]
        assert isinstance(c["altitude_m"], int)
    json.dumps(payload)


def test_webcam_snapshot_is_latest_half_hour_slot():
    local = datetime(2026, 10, 12, 13, 5, tzinfo=ZoneInfo("Atlantic/Madeira"))
    assert [t.strftime("%H:%M") for t in webexport.latest_webcam_slots(local)] == ["12:30", "12:00"]
    cam = webexport.webcams_section(webexport.load_webcams(), local, {"morning": [], "evening": []})[0]
    assert cam["snapshot_url"].endswith("/2026-10-12/12_30.jpg")
    assert cam["fallback_url"].endswith("/2026-10-12/12_00.jpg")
    assert cam["snapshot_time"] == "12:30"


def test_webcam_slot_crosses_midnight():
    local = datetime(2026, 10, 12, 0, 5, tzinfo=ZoneInfo("Atlantic/Madeira"))
    latest, previous = webexport.latest_webcam_slots(local)
    assert (latest.date().isoformat(), latest.strftime("%H_%M")) == ("2026-10-11", "23_30")
    assert previous.strftime("%H:%M") == "23:00"


def test_webcams_pair_with_model_forecast():
    payload = webexport.build_payload(Settings(), days=3, bundle=_bundle(), now=NOW)
    arieiro = next(c for c in payload["webcams"] if c["id"] == "pico-do-arieiro")
    spot = payload["tomorrow_spots"]["morning"][0]
    assert arieiro["forecast"]["kind"] == "sunrise"
    assert arieiro["forecast"]["verdict"] == spot["verdict"]
    assert arieiro["forecast"]["score"] == spot["score_pct"]
    assert next(c for c in payload["webcams"] if c["id"] == "porto-moniz")["forecast"] is None


def test_webcam_fanal_pairing():
    cam = webexport.Webcam(id="r", name="R", altitude_m=1, category="forest", spot_id="fanal", lat=32.7, lon=-17.1,
                           snapshot_url="x", live_url="y")
    fog = {"verdict": "MYSTIC", "score": 75, "relative": "tomorrow", "target_date": "2026-10-21"}
    assert webexport._webcam_forecast(cam, {"morning": [], "evening": []}, fog) == {
        "kind": "fog", "verdict": "MYSTIC", "score": 75, "relative": "tomorrow", "target_date": "2026-10-21"}
    assert webexport._webcam_forecast(cam, {"morning": [], "evening": []}, None) is None


def _raw_wx(**kw):
    return {"temperature_2m": 17.6, "relative_humidity_2m": 71, "apparent_temperature": 16.2, "precipitation": 0.0,
            "weather_code": 2, "wind_speed_10m": 13.6, "wind_gusts_10m": 22.0, **kw}


def test_current_weather_structure():
    w = webexport.current_weather(_raw_wx())
    assert w["temp"] == 18 and w["wind_kmh"] == 14 and w["gusts_kmh"] is None  # nárazy < 35 km/h se skryjí
    assert w["condition"] == "⛅ Polojasno" and w["condition_key"] == "partly"
    assert webexport.current_weather(_raw_wx(wind_gusts_10m=48.4, weather_code=63))["gusts_kmh"] == 48
    assert webexport.current_weather(_raw_wx(weather_code=63))["condition"] == "🌧️ Déšť"
    assert webexport.current_weather({}) is None


def test_webcams_have_current_weather():
    cams = webexport.load_webcams()
    assert all(-90 < c.lat < 90 and -180 < c.lon < 180 for c in cams)
    wx = [webexport.current_weather(_raw_wx()) for _ in cams]
    payload = webexport.build_payload(Settings(), days=3, bundle=_bundle(), now=NOW, webcam_weather=wx)
    for c in payload["webcams"]:
        w = c["current_weather"]
        assert set(w) >= {"temp", "wind_kmh", "gusts_kmh", "condition"}
        assert isinstance(w["temp"], int) and isinstance(w["wind_kmh"], int)
        assert w["gusts_kmh"] is None or w["gusts_kmh"] >= 35
    with_model = {c["id"] for c in payload["webcams"] if c["forecast"]}
    assert with_model <= {"pico-do-arieiro", "rabacal", "canical"}
    json.dumps(payload)


def test_webcam_weather_failure_is_graceful(monkeypatch):
    def boom(points):
        raise RuntimeError("down")
    monkeypatch.setattr(webexport, "fetch_current", boom)
    cams = webexport.load_webcams()
    assert webexport.fetch_webcam_weather(cams) == [None] * len(cams)


def test_fetch_current_batches_one_request():
    import httpx

    from madeira.fetchers.open_meteo import fetch_current

    calls = []

    def handler(request):
        calls.append(request)
        n = len(request.url.params["latitude"].split(","))
        return httpx.Response(200, json=[{"current": _raw_wx()} for _ in range(n)])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    out = fetch_current([(32.7, -16.9, 1800), (32.6, -16.9, 20), (32.8, -17.1, 10)], client=client)
    assert len(calls) == 1 and len(out) == 3
    assert "weather_code" in calls[0].url.params["current"]
