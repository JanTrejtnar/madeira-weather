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


def _f(spread=5.0, rh=60.0, deg=270.0, speed=5.0, vis=None, rain=0.0, gust=10.0) -> FogFeatures:
    return FogFeatures(humidity_pct=rh, cloud_base_m=None, visibility_m=vis, precip_mm_h=rain, gust_kmh=gust,
                       cloud_cover_pct=None, spread_c=spread, surface_rh_pct=rh, wind_kmh=speed, wind_dir_deg=deg)


def test_weights_yaml_defines_fanal_as_fog_seeker():
    cfg = load_settings().fanal
    assert (cfg.lat, cfg.lon, cfg.elevation_m, cfg.type) == (32.8083, -17.1417, 1150, "fog_seeker")
    assert (cfg.verdict.go, cfg.verdict.maybe) == (70, 40)
    assert (cfg.dew.spread_extreme, cfg.dew.spread_likely, cfg.dew.spread_dry) == (1.0, 2.0, 3.5)
    assert (cfg.dew.rh_extreme, cfg.dew.rh_likely, cfg.dew.points_extreme, cfg.dew.points_likely) == (92, 85, 50, 30)
    assert (cfg.wind.bonus, cfg.wind.lee_penalty, cfg.wind.speed_min, cfg.wind.speed_max) == (25, 30, 8, 30)
    assert cfg.webcam_url.startswith("https://www.netmadeira.com/webcams-madeira/")
    assert cfg.as_location().role == "fog_seeker"
    assert cfg == FanalConfig()  # yaml a výchozí hodnoty v config.py se neliší


def test_verdict_boundaries():
    assert [verdict(s, CFG) for s in (70, 69.9, 40, 39.9, 0)] == ["MYSTIC", "PARTIAL", "PARTIAL", "CLEAR", "CLEAR"]


def test_dew_point_spread_points():
    pts = lambda spread, rh=50: fanal.dew_points(_f(spread=spread, rh=rh), CFG)  # noqa: E731
    assert pts(0.4) == pts(1.0) == 50  # extrémně vysoká pravděpodobnost
    assert pts(1.5) == pts(2.0) == 30  # pravděpodobné cáry
    assert 0 < pts(2.75) < 30  # mezi 2,0 a 3,5 °C lineárně klesá
    assert pts(2.75) == pytest.approx(15)
    assert pts(3.5) == pts(6) == 0  # sucho


def test_relative_humidity_is_an_alternative_to_spread():
    assert fanal.dew_points(_f(spread=4.0, rh=92), CFG) == 50
    assert fanal.dew_points(_f(spread=4.0, rh=85), CFG) == 30
    assert fanal.dew_points(_f(spread=4.0, rh=84), CFG) == 0
    assert fanal.dew_points(_f(spread=0.5, rh=70), CFG) == 50  # vyhrává lepší z obou
    assert fanal.dew_points(_f(spread=None, rh=None), CFG) == 0


def test_sector_wraps_around_north():
    assert [in_sector(d, 320, 70) for d in (320, 359, 0, 45, 70)] == [True] * 5
    assert [in_sector(d, 320, 70) for d in (319, 71, 180, 270)] == [False] * 4
    assert in_sector(180, 140, 220) and not in_sector(139, 140, 220) and not in_sector(221, 140, 220)


def test_trade_wind_gives_orographic_bonus_only_in_range_and_moist_air():
    base = score_fog(_f(spread=0.5, deg=100, speed=15), CFG)  # východ = bez bonusu
    assert score_fog(_f(spread=0.5, deg=20, speed=15), CFG) == base + 25
    assert score_fog(_f(spread=0.5, deg=350, speed=15), CFG) == base + 25  # 320°–070° přes sever
    assert score_fog(_f(spread=0.5, deg=70, speed=8), CFG) == base + 25
    assert score_fog(_f(spread=0.5, deg=20, speed=7), CFG) == base  # moc slabý vítr
    assert score_fog(_f(spread=0.5, deg=20, speed=31), CFG) == base  # moc silný vítr
    assert score_fog(_f(spread=6, rh=50, deg=20, speed=15), CFG) == 0  # v suchém vzduchu bonus nepomůže


def test_southern_foehn_wind_is_penalized():
    assert score_fog(_f(spread=0.5, deg=180), CFG) == 50 - 30
    assert score_fog(_f(spread=6, rh=50, deg=180), CFG) == 0  # nikdy pod nulou


def test_cloud_base_does_not_influence_score():
    # základna mraků 3 000 m vs. 1 150 m: stejné skóre, rozhoduje rosný bod a vítr
    low = FogFeatures(95, 1150, None, 0, 10, None, spread_c=0.8, surface_rh_pct=95, wind_kmh=15, wind_dir_deg=20)
    high = FogFeatures(95, 3000, None, 0, 10, None, spread_c=0.8, surface_rh_pct=95, wind_kmh=15, wind_dir_deg=20)
    assert score_fog(low, CFG) == score_fog(high, CFG) == 75
    assert verdict(75, CFG) == "MYSTIC"


def test_thick_orographic_fog_is_mystic_and_dry_day_is_clear():
    assert score_fog(_f(spread=0.3, rh=97, deg=30, speed=12), CFG) == 75  # 50 + 25
    assert score_fog(_f(spread=8, rh=40, deg=270), CFG) == 0


def test_visibility_is_only_a_bonus():
    near = score_fog(_f(spread=1.5, deg=100, vis=300), CFG)
    assert near == 30 + 25
    assert score_fog(_f(spread=1.5, deg=100, vis=24000), CFG) == score_fog(_f(spread=1.5, deg=100, vis=None), CFG) == 30
    assert 30 < score_fog(_f(spread=1.5, deg=100, vis=1000), CFG) < near
    assert score_fog(_f(spread=0.3, deg=20, speed=12, vis=200), CFG) == 100  # strop


def test_drizzle_is_fine_but_downpour_kills_the_score():
    s = lambda rain: score_fog(_f(spread=0.3, deg=20, speed=12, rain=rain), CFG)  # noqa: E731
    assert s(1.4) == s(0)  # mrholení < 1,5 mm/h nevadí
    assert 0 < s(2.25) < s(1.5)
    assert s(3.0) == 0 and s(8) == 0  # liják > 3 mm/h = 0


def test_only_extreme_gusts_penalize():
    s = lambda gust: score_fog(_f(spread=0.3, deg=20, speed=12, gust=gust), CFG)  # noqa: E731
    assert s(10) == s(50) == 75  # 17.–18. 10. byla mlha i při nárazech 39–72 km/h
    assert s(65) < 75
    assert s(120) == pytest.approx(0.3 * 75, abs=0.1)


def test_fog_points_exposes_components():
    b = fog_points(_f(spread=0.3, deg=20, speed=12, vis=300, gust=65), CFG)
    assert (b["dew"], b["wind"], b["visibility"]) == (50, 25, 25)
    assert b["gust_gate"] == pytest.approx(0.65) and b["score"] == pytest.approx(65)


def test_missing_data_scores_zero_not_crash():
    assert score_fog(FogFeatures(None, None, None, 0, None, None), CFG) == 0


def test_features_from_open_meteo_point():
    p = HourlyPoint(
        time_utc=NOW, temperature_c=10.0, dew_point_c=8.0, relative_humidity_pct=90, wind_speed_kmh=12,
        wind_direction_deg=30,
        levels={925: PressureLevel(geopotential_height_m=800, relative_humidity_pct=100),
                850: PressureLevel(geopotential_height_m=1500, relative_humidity_pct=80)},
    )
    f = fanal.extract_fog_features(p, CFG)
    assert f.spread_c == 2.0 and f.surface_rh_pct == 90 and f.wind_kmh == 12 and f.wind_dir_deg == 30
    assert f.cloud_base_m == pytest.approx(1150 + 125 * 2)  # jen informativní odhad
    assert fanal.rh_at_height(p, 1150) == pytest.approx(90)
    # T 10, Td 8 → spread 2,0 → +30, severní vítr 12 km/h → +25
    assert score_fog(f, CFG) == 55


def test_negative_dewpoint_spread_is_clamped():
    p = HourlyPoint(time_utc=NOW, temperature_c=10.0, dew_point_c=10.4)
    f = fanal.extract_fog_features(p, CFG)
    assert f.spread_c == 0 and f.cloud_base_m == 1150


def test_mean_direction_is_circular():
    assert fanal.mean_direction([350, 10]) % 360 == pytest.approx(0, abs=1e-6) or fanal.mean_direction([350, 10]) == pytest.approx(360)
    assert fanal.mean_direction([40, 50]) == pytest.approx(45)
    assert fanal.mean_direction([]) is None
    assert fanal.mean_direction([0, 180]) is None


def test_best_window_is_longest_run_above_partial():
    base = datetime(2026, 10, 13, tzinfo=TZ)
    pts = [
        HourlyPoint(time_utc=(base + timedelta(hours=h)).astimezone(UTC), **(MIST if 10 <= h < 14 else DRY_DAY))
        for h in range(24)
    ]
    assert fanal.best_window(pts, DAY, CFG) == (10, 14)
    assert fanal.best_window(pts, DAY, CFG, from_hour=12) == (12, 14)  # proběhlé hodiny se nepočítají
    storm = _hours(DAY, **{**MIST, "wind_gusts_kmh": 150, "precipitation_mm": 5})
    assert fanal.best_window(storm, DAY, CFG) is None


def test_section_for_misty_day_has_required_keys():
    now = datetime(2026, 10, 13, 7, 0, tzinfo=TZ)
    section = webexport.fanal_section(CFG, _fanal_bundle(_hours(DAY, **MIST)), now)
    assert section["day"] == "today"
    assert section["verdict"] == "MYSTIC" and section["score"] >= 70
    assert {"verdict", "score", "humidity", "cloud_coverage_local", "wind_gusts", "rain", "summary",
            "best_window", "webcam_url", "dew_point_spread", "wind_speed", "wind_dir_deg"} <= section.keys()
    assert section["best_window"].startswith("Dopoledne 08:00–")
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
    assert first["summary"] == "Vlhkost 98 %, pasát ze severu – hustá mlha"
    assert first["metrics"] == {"temp": 12.0, "humidity": 98, "dew_point": 11.8, "dew_point_spread": 0.2,
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
