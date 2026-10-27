import json
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from madeira.analyzers import fanal
from madeira.analyzers.fanal import FogFeatures, fog_points, in_sector, score_fog, verdict
from madeira.domain.config import FanalConfig, Settings, load_settings
from madeira.domain.models import ForecastBundle, HourlyPoint, LocationForecast, PressureLevel
from madeira.services import webexport
from tests.test_analyzers import NOW
from tests.test_webexport import _bundle

TZ = ZoneInfo("Atlantic/Madeira")
CFG = FanalConfig()
DAY = date(2026, 10, 13)

# hustá mlha: T − Td = 0,2 °C, RH 98 %, dohlednost 250 m, slabý vítr ze severu, mrholení
MIST = {"temperature_c": 12.0, "dew_point_c": 11.8, "relative_humidity_pct": 98, "visibility_m": 250,
        "precipitation_mm": 0.3, "wind_gusts_kmh": 15, "wind_speed_kmh": 10, "wind_direction_deg": 20,
        "cloud_cover_low_pct": 100, "cloud_cover_total_pct": 100}
DRY_DAY = {**MIST, "relative_humidity_pct": 40, "dew_point_c": 0.0, "visibility_m": 24000, "cloud_cover_low_pct": 0,
           "wind_direction_deg": 270}


def _hours(day: date, **fields) -> list[HourlyPoint]:
    base = datetime(day.year, day.month, day.day, tzinfo=TZ)
    return [HourlyPoint(time_utc=(base + timedelta(hours=h)).astimezone(UTC), **fields) for h in range(24)]


def _fanal_bundle(points: list[HourlyPoint]) -> ForecastBundle:
    loc = CFG.as_location()
    return ForecastBundle(model="best_match", fetched_at_utc=NOW,
                          forecasts={"fanal": LocationForecast(location=loc, hourly=points, daily=[])})


def _f(rh=60.0, deg=270.0, speed=5.0, vis=None, rain=0.0, gust=10.0, low=None, **extra) -> FogFeatures:
    """Rysy bodu; `rh` je vlhkost ve výšce lesa. Bez `hour` a hladin se pravidlo rozbřesku ani brána 900/850 hPa neuplatní."""
    return FogFeatures(humidity_pct=rh, cloud_base_m=None, visibility_m=vis, precip_mm_h=rain, gust_kmh=gust,
                       cloud_cover_pct=low, rh_forest_pct=rh, wind_kmh=speed, wind_dir_deg=deg, **extra)


def test_weights_yaml_defines_fanal_as_fog_seeker():
    cfg = load_settings().fanal
    assert (cfg.lat, cfg.lon, cfg.elevation_m, cfg.type) == (32.8083, -17.1417, 1150, "fog_seeker")
    assert (cfg.verdict.go, cfg.verdict.maybe) == (70, 40)
    assert (cfg.dew.rh_extreme, cfg.dew.rh_likely, cfg.dew.rh_dry, cfg.dew.points_extreme, cfg.dew.points_likely) == (95, 90, 80, 50, 30)
    assert (cfg.dew.dawn_until_hour, cfg.dew.dawn_min_low_cloud_pct, cfg.layer.mystic_rh_min) == (10, 50, 80)
    assert (cfg.wind.bonus, cfg.wind.lee_penalty, cfg.wind.speed_min, cfg.wind.speed_max) == (25, 30, 8, 30)
    assert (cfg.wind.calm_from, cfg.wind.calm_to, cfg.wind.calm_rh_min) == (290, 20, 95)
    assert cfg.webcam_url.startswith("https://www.netmadeira.com/webcams-madeira/")
    assert cfg.as_location().role == "fog_seeker"
    assert cfg == FanalConfig()  # yaml a výchozí hodnoty v config.py se neliší


def test_verdict_boundaries():
    assert [verdict(s, CFG) for s in (70, 69.9, 40, 39.9, 0)] == ["MYSTIC", "PARTIAL", "PARTIAL", "CLEAR", "CLEAR"]


def test_forest_humidity_points():
    pts = lambda rh: fanal.dew_points(_f(rh=rh), CFG)
    assert pts(95) == pts(100) == 50  # saturovaná vrstva = plný zisk
    assert pts(90) == pts(94) == 30
    assert pts(85) == pytest.approx(15)  # mezi 80 a 90 % lineárně
    assert pts(80) == pts(60) == 0
    assert fanal.dew_points(_f(rh=None), CFG) == 0


def test_surface_humidity_is_ignored_when_forest_level_is_known():
    wet_surface = FogFeatures(None, None, None, 0, 10, None, spread_c=0.3, surface_rh_pct=99, rh_forest_pct=70)
    assert fanal.dew_points(wet_surface, CFG) == 0  # přízemní rosa není mlha v lese
    only_surface = FogFeatures(None, None, None, 0, 10, None, surface_rh_pct=96)
    assert fanal.dew_points(only_surface, CFG) == 50  # bez hladin záloha na přízemní RH


def test_sector_wraps_around_north():
    assert [in_sector(d, 320, 70) for d in (320, 359, 0, 45, 70)] == [True] * 5
    assert [in_sector(d, 320, 70) for d in (319, 71, 180, 270)] == [False] * 4
    assert in_sector(180, 140, 220) and not in_sector(139, 140, 220) and not in_sector(221, 140, 220)


def test_trade_wind_gives_orographic_bonus_only_in_range_and_moist_air():
    base = score_fog(_f(rh=97, deg=100, speed=15), CFG)  # východ = bez bonusu
    assert score_fog(_f(rh=97, deg=20, speed=15), CFG) == base + 25
    assert score_fog(_f(rh=97, deg=350, speed=15), CFG) == base + 25  # 320°–070° přes sever
    assert score_fog(_f(rh=97, deg=70, speed=8), CFG) == base + 25
    assert score_fog(_f(rh=97, deg=100, speed=7), CFG) == base  # slabý vítr z východu bonus nedostane
    assert score_fog(_f(rh=97, deg=20, speed=31), CFG) == base  # moc silný vítr
    assert score_fog(_f(rh=50, deg=20, speed=15), CFG) == 0  # v suchém vzduchu bonus nepomůže


def test_calm_northwest_wind_keeps_the_bonus_when_900_hpa_is_saturated():
    calm = lambda deg, speed, rh900: score_fog(_f(rh=97, deg=deg, speed=speed, rh900_pct=rh900), CFG)
    assert calm(300, 3.4, 97) == 75  # NW, bezvětří, nasyceno: 26. 10. 2026
    assert calm(10, 5, 95) == 75  # sever
    assert calm(300, 3.4, 94) == 50  # 900 hPa není plně nasycená
    assert calm(100, 3.4, 99) == 50  # slabý východní vítr se nezjemňuje
    assert calm(300, 3.4, None) == 50  # bez dat o hladině se zjemnění nepoužije
    assert calm(180, 3.4, 99) == 20  # föhn vždy penalizuje


def test_southern_foehn_wind_is_penalized():
    assert score_fog(_f(rh=97, deg=180), CFG) == 50 - 30
    assert score_fog(_f(rh=50, deg=180), CFG) == 0  # nikdy pod nulou


def test_cloud_base_does_not_influence_score():
    # základna mraků 3 000 m vs. 1 150 m: stejné skóre, rozhoduje vlhkost ve výšce lesa a vítr
    low = FogFeatures(95, 1150, None, 0, 10, None, rh_forest_pct=97, wind_kmh=15, wind_dir_deg=20)
    high = FogFeatures(95, 3000, None, 0, 10, None, rh_forest_pct=97, wind_kmh=15, wind_dir_deg=20)
    assert score_fog(low, CFG) == score_fog(high, CFG) == 75
    assert verdict(75, CFG) == "MYSTIC"


def test_thick_orographic_fog_is_mystic_and_dry_day_is_clear():
    assert score_fog(_f(rh=97, deg=30, speed=12), CFG) == 75  # 50 + 25
    assert score_fog(_f(rh=40, deg=270), CFG) == 0


def test_visibility_is_only_a_bonus():
    near = score_fog(_f(rh=90, deg=100, vis=300), CFG)
    assert near == 30 + 25
    assert score_fog(_f(rh=90, deg=100, vis=24000), CFG) == score_fog(_f(rh=90, deg=100, vis=None), CFG) == 30
    assert 30 < score_fog(_f(rh=90, deg=100, vis=1000), CFG) < near
    assert score_fog(_f(rh=97, deg=20, speed=12, vis=200), CFG) == 100  # strop


def test_drizzle_is_fine_but_downpour_kills_the_score():
    s = lambda rain: score_fog(_f(rh=97, deg=20, speed=12, rain=rain), CFG)
    assert s(1.4) == s(0)  # mrholení < 1,5 mm/h nevadí
    assert 0 < s(2.25) < s(1.5)
    assert s(3.0) == 0 and s(8) == 0  # liják > 3 mm/h = 0


def test_only_extreme_gusts_penalize():
    s = lambda gust: score_fog(_f(rh=97, deg=20, speed=12, gust=gust), CFG)
    assert s(10) == s(50) == 75  # 17.–18. 10. byla mlha i při nárazech 39–72 km/h
    assert s(65) < 75
    assert s(120) == pytest.approx(0.3 * 75, abs=0.1)


def test_fog_points_exposes_components():
    b = fog_points(_f(rh=97, deg=20, speed=12, vis=300, gust=65), CFG)
    assert (b["dew"], b["wind"], b["visibility"], b["layer_capped"]) == (50, 25, 25, 0)
    assert b["gust_gate"] == pytest.approx(0.65) and b["score"] == pytest.approx(65)


def test_dawn_moisture_needs_confirmed_low_cloud():
    morning = lambda hour, low: fanal.dew_points(_f(rh=97, low=low, local_hour=hour), CFG)
    assert morning(8, 0) == morning(9, 49) == 0  # nasycení po nočním ochlazení nestačí
    assert morning(8, None) == 0  # nepotvrzená oblačnost = nepotvrzeno
    assert morning(8, 50) == morning(9, 100) == 50  # model oblačnost potvrzuje
    assert morning(10, 0) == morning(14, 0) == 50  # po rozbřesku pravidlo neplatí
    assert fanal.dew_points(_f(rh=97, low=0), CFG) == 50  # bez znalosti hodiny se neuplatní


def test_dry_850_or_unsaturated_layer_forbids_mystic():
    kw = {"rh": 97, "deg": 20, "speed": 12, "vis": 200}  # jinak strop 100
    assert score_fog(_f(**kw), CFG) == 100
    assert score_fog(_f(rh900_pct=97, rh850_pct=80, **kw), CFG) == 100  # obě hladiny ≥ 80 %
    for rh900, rh850 in ((97, 79), (97, 55), (79, 97)):
        capped = fog_points(_f(rh900_pct=rh900, rh850_pct=rh850, **kw), CFG)
        assert capped["layer_capped"] == 1 and capped["score"] == 69
        assert verdict(capped["score"], CFG) == "PARTIAL"
    assert score_fog(_f(rh900_pct=97, rh850_pct=55, rh=97, deg=100), CFG) == 50  # brána jen shora ořezává
    assert score_fog(_f(rh900_pct=None, rh850_pct=None, **kw), CFG) == 100  # bez dat se neblokuje


def test_missing_data_scores_zero_not_crash():
    assert score_fog(FogFeatures(None, None, None, 0, None, None), CFG) == 0


def test_features_from_open_meteo_point():
    p = HourlyPoint(
        time_utc=datetime(2026, 10, 13, 14, 0, tzinfo=TZ).astimezone(UTC), temperature_c=10.0, dew_point_c=8.0,
        relative_humidity_pct=90, wind_speed_kmh=12, wind_direction_deg=30,
        levels={925: PressureLevel(geopotential_height_m=800, relative_humidity_pct=100),
                900: PressureLevel(geopotential_height_m=1050, relative_humidity_pct=99),
                850: PressureLevel(geopotential_height_m=1500, relative_humidity_pct=80)},
    )
    f = fanal.extract_fog_features(p, CFG)
    assert f.spread_c == 2.0 and f.surface_rh_pct == 90 and f.wind_kmh == 12 and f.wind_dir_deg == 30
    assert f.cloud_base_m == pytest.approx(1150 + 125 * 2)  # jen informativní odhad
    assert (f.rh900_pct, f.rh850_pct, f.local_hour) == (99, 80, 14)
    assert f.rh_forest_pct == pytest.approx(99 - 19 * 100 / 450)  # interpolace na 1 150 m
    # vlhkost v lese 94,8 % → +30, severní vítr 12 km/h → +25; obě hladiny ≥ 80 %
    assert score_fog(f, CFG) == 55


def test_negative_dewpoint_spread_is_clamped():
    p = HourlyPoint(time_utc=NOW, temperature_c=10.0, dew_point_c=10.4)
    f = fanal.extract_fog_features(p, CFG)
    assert f.spread_c == 0 and f.cloud_base_m == 1150


def _obs(day: str, hour: int, rh2, low, vis, speed, deg, gust, rh925, rh900, rh850, z900, z850) -> HourlyPoint:
    """Skutečné hodnoty Open-Meteo pro Fanal z týdne 20.–27. 10. 2026 (pozorování kamerou Rabaçal viz scripts/fanal_labels.json)."""
    t = datetime.fromisoformat(f"{day}T{hour:02d}:00").replace(tzinfo=TZ)
    lv = lambda z, rh: PressureLevel(geopotential_height_m=z, relative_humidity_pct=rh)
    return HourlyPoint(time_utc=t.astimezone(UTC), relative_humidity_pct=rh2, cloud_cover_low_pct=low, visibility_m=vis,
                       wind_speed_kmh=speed, wind_direction_deg=deg, wind_gusts_kmh=gust, precipitation_mm=0.0,
                       levels={925: lv(780, rh925), 900: lv(z900, rh900), 850: lv(z850, rh850)})


def _verdict_of(p: HourlyPoint) -> tuple[str, float]:
    score = score_fog(fanal.extract_fog_features(p, CFG), CFG)
    return verdict(score, CFG), score


def test_observed_week_dry_lid_day_is_clear_all_morning():
    # 25. 10.: 850 hPa suché (RH 3 %), kamera celý den jasno; přízemní RH 85 % a vítr 70° tehdy daly 75 bodů
    p = _obs("2026-10-25", 8, 85, 0, 27860, 10.4, 70, 34, 83, 54, 3, 1112, 1594)
    assert _verdict_of(p) == ("CLEAR", 0)
    p = _obs("2026-10-25", 12, 80, 84, 24980, 10.5, 63, 38, 84, 94, 2, 1121, 1603)  # i s nízkou oblačností a vlhkým 900 hPa
    assert _verdict_of(p)[0] != "MYSTIC"


def test_observed_week_morning_fog_with_saturated_layer_is_mystic():
    # 23. 10. v 9 h: hustá mlha, RH 97–100 % v 900 i 850 hPa, nízká oblačnost 81 %, pasát 54° / 11 km/h
    assert _verdict_of(_obs("2026-10-23", 9, 97, 81, 6140, 11.1, 54, 30, 97, 99, 100, 1133, 1610)) == ("MYSTIC", pytest.approx(76.4, abs=0.1))  # +1,4 za dohlednost 6 km


def test_observed_week_dawn_without_low_cloud_and_with_dry_850_is_not_fog():
    # 24. 10. v 8 h: RH 94 % u země, ale nízká oblačnost jen 41 % a 850 hPa jen 73 %; kamera jasno
    assert _verdict_of(_obs("2026-10-24", 8, 94, 41, 22180, 11.4, 66, 44, 92, 91, 73, 1132, 1609)) == ("CLEAR", 0)


def test_observed_week_calm_northwest_fog_is_not_downgraded():
    # 26. 10. v 13 h: hustá mlha při větru 3,4 km/h z 302°, 900 hPa nasycená (97 %); dřív jen PARTIAL 50
    assert _verdict_of(_obs("2026-10-26", 13, 86, 87, 12960, 3.4, 302, 27, 90, 97, 99, 1064, 1545)) == ("MYSTIC", 75)


def test_daily_score_is_peak_slot_not_average():
    pts = _hours(DAY, **DRY_DAY)
    pts[9] = _hours(DAY, **MIST)[9]  # jen 9 h je mlha, 13 a 17 h sucho
    section = webexport.fanal_section(CFG, _fanal_bundle(pts), datetime(2026, 10, 13, 6, 0, tzinfo=TZ))
    scores = [s["score"] for s in section["slots"]]
    assert scores[1:] == [0, 0] and scores[0] >= 70
    assert section["score"] == section["peak_score"] == scores[0]  # průměr by byl pod 40
    assert section["verdict"] == "MYSTIC" and section["best_window"].startswith("Dopoledne 09:00")
    # hodnoty karty jsou z nejlepšího slotu
    assert section["humidity"] == 98 and section["forest_humidity"] == 98 and section["wind_dir_deg"] == 20


def test_peak_tie_prefers_earlier_slot_and_clear_day_has_no_window():
    tie = webexport.fanal_section(CFG, _fanal_bundle(_hours(DAY, **MIST)), datetime(2026, 10, 13, 6, 0, tzinfo=TZ))
    assert tie["best_window"] == "Dopoledne 09:00–12:00"
    clear = webexport.fanal_section(CFG, _fanal_bundle(_hours(DAY, **DRY_DAY)), datetime(2026, 10, 13, 6, 0, tzinfo=TZ))
    assert clear["verdict"] == "CLEAR" and clear["best_window"] is None and clear["best_window_hours"] is None
    evening = webexport.fanal_section(CFG, _fanal_bundle(_hours(DAY, **MIST)), datetime(2026, 10, 13, 15, 0, tzinfo=TZ))
    assert evening["best_window"] == "Večer 17:00–20:00"


def test_section_for_misty_day_has_required_keys():
    now = datetime(2026, 10, 13, 7, 0, tzinfo=TZ)
    section = webexport.fanal_section(CFG, _fanal_bundle(_hours(DAY, **MIST)), now)
    assert section["day"] == "today"
    assert section["verdict"] == "MYSTIC" and section["score"] >= 70
    assert {"verdict", "score", "humidity", "forest_humidity", "cloud_coverage_local", "wind_gusts", "rain", "summary",
            "best_window", "webcam_url", "dew_point_spread", "wind_speed", "wind_dir_deg"} <= section.keys()
    assert section["best_window"] == "Dopoledne 09:00–12:00"
    assert [s["hour"] for s in section["slots"]] == [9, 13, 17]
    assert "mlha" in section["summary"].lower()
    assert section["humidity"] == 98 and section["wind_gusts"] == 15 and section["rain"] == 0.3
    assert section["dew_point_spread"] == 0.2 and section["wind_dir_deg"] == 20


def test_today_slots_in_the_past_fall_back_to_tomorrow():
    pts = _hours(DAY, **MIST) + _hours(DAY + timedelta(days=1), **DRY_DAY)
    late = webexport.fanal_section(CFG, _fanal_bundle(pts), datetime(2026, 10, 13, 18, 0, tzinfo=TZ))
    assert "today" not in late and late["day"] == "tomorrow"
    assert late["verdict"] == "CLEAR" and late["best_window"] is None
    morning = webexport.fanal_section(CFG, _fanal_bundle(pts), datetime(2026, 10, 13, 10, 0, tzinfo=TZ))
    assert [s["hour"] for s in morning["today"]["slots"]] == [13, 17]  # 9:00 už proběhlo


def test_clear_summary_names_the_reason():
    now = datetime(2026, 10, 13, 7, 0, tzinfo=TZ)
    sections = {
        name: webexport.fanal_section(CFG, _fanal_bundle(_hours(DAY, **{**MIST, **change})), now)
        for name, change in {
            "windy": {"wind_gusts_kmh": 150},
            "rainy": {"precipitation_mm": 6.0},
            "foehn": {"wind_direction_deg": 180, "relative_humidity_pct": 80, "dew_point_c": 8.5, "visibility_m": 24000},
        }.items()
    }
    assert all(s["verdict"] == "CLEAR" for s in sections.values())
    assert "vítr" in sections["windy"]["summary"]
    assert "déšť" in sections["rainy"]["summary"]
    assert "föhn" in sections["foehn"]["summary"]


def test_section_is_none_without_data():
    now = datetime(2026, 10, 13, 7, 0, tzinfo=TZ)
    assert webexport.fanal_section(CFG, _fanal_bundle([]), now) is None
    assert webexport.fanal_section(CFG, ForecastBundle(model="x", fetched_at_utc=NOW, forecasts={}), now) is None


def test_history_covers_last_seven_days_with_snapshots():
    today = date(2026, 10, 18)
    pts = []
    for i in range(1, 9):  # 8 dní zpět: poslední se do historie nevejde
        pts += _hours(today - timedelta(days=i), **(MIST if i % 2 else DRY_DAY))
    section = webexport.fanal_section(CFG, _fanal_bundle(pts + _hours(today, **MIST)),
                                      datetime(2026, 10, 18, 12, 0, tzinfo=TZ))
    hist = section["history"]
    assert [h["date"] for h in hist] == [(today - timedelta(days=i)).isoformat() for i in range(1, 8)]
    first = hist[0]  # včera = i 1 = mlha
    assert first["verdict"] == "MYSTIC" and first["score"] >= 70
    assert first["summary"] == "Vlhkost v lese 98 %, pasát ze severu – hustá mlha"
    assert first["metrics"] == {"temp": 12.0, "humidity": 98, "forest_humidity": 98, "dew_point": 11.8, "dew_point_spread": 0.2,
                                "wind_speed": 10, "wind_dir": 20}
    assert [s["time"] for s in first["snapshots"]] == ["11:00", "15:00"]
    assert first["snapshots"][0]["url"] == (
        "https://www.netmadeira.com/assets/images/services/webcams/timelapses/netmadeira/"
        "rabacal-madeira/2026-10-17/11_00.jpg")
    assert first["snapshots"][1]["url"].endswith("/2026-10-17/15_00.jpg")
    assert hist[1]["verdict"] == "CLEAR" and hist[1]["summary"].endswith("bez mlhy")
    assert "föhn" not in hist[1]["summary"] and "západu" in hist[1]["summary"]  # 270° = vítr ze západu


def test_history_skips_days_without_model_data():
    pts = _hours(date(2026, 10, 17), **MIST)
    hist = webexport.fanal_section(CFG, _fanal_bundle(pts), datetime(2026, 10, 18, 12, 0, tzinfo=TZ))
    assert hist is None or [h["date"] for h in hist.get("history", [])] == ["2026-10-17"]


def test_payload_includes_fanal_only_when_bundle_given_and_is_valid_json(tmp_path):
    settings = Settings()
    assert "fanal" not in webexport.build_payload(settings, days=1, bundle=_bundle(), now=NOW)
    now = datetime(2026, 10, 12, 9, 0, tzinfo=TZ)
    fb = _fanal_bundle(_hours(date(2026, 10, 12), **MIST) + _hours(date(2026, 10, 13), **MIST)
                       + _hours(date(2026, 10, 11), **MIST))
    payload = webexport.build_payload(settings, days=1, bundle=_bundle(), now=now, fanal_bundle=fb)
    out = tmp_path / "data.json"
    webexport.write_payload(payload, out)
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["fanal"]["verdict"] == "MYSTIC"
    assert [h["date"] for h in loaded["fanal"]["history"]] == ["2026-10-11"]
    assert {"sunrise", "sunset", "fanal"} <= loaded.keys()
