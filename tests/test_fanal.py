import json
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from madeira.analyzers import fanal
from madeira.analyzers.fanal import FogFeatures, score_fog, verdict
from madeira.domain.config import FanalConfig, Settings, load_settings
from madeira.domain.models import ForecastBundle, HourlyPoint, LocationForecast, PressureLevel
from madeira.services import webexport
from tests.test_analyzers import NOW
from tests.test_webexport import _bundle

TZ = ZoneInfo("Atlantic/Madeira")
CFG = FanalConfig()
DAY = date(2026, 10, 13)

# hustá mlha: RH 98 %, základna ≈ 1 150 m (T − Td = 0), dohlednost 250 m, slabý vítr, mrholení
FOG = FogFeatures(humidity_pct=98, cloud_base_m=1150, visibility_m=250, precip_mm_h=0.4, gust_kmh=15, cloud_cover_pct=100)
DRY = FogFeatures(humidity_pct=50, cloud_base_m=2800, visibility_m=24000, precip_mm_h=0.0, gust_kmh=10, cloud_cover_pct=5)

MIST = {"temperature_c": 12.0, "dew_point_c": 11.8, "relative_humidity_pct": 98, "visibility_m": 250,
        "precipitation_mm": 0.3, "wind_gusts_kmh": 15, "cloud_cover_low_pct": 100, "cloud_cover_total_pct": 100}
DRY_DAY = {**MIST, "relative_humidity_pct": 40, "dew_point_c": 0.0, "visibility_m": 24000, "cloud_cover_low_pct": 0}


def _hours(day: date, **fields) -> list[HourlyPoint]:
    base = datetime(day.year, day.month, day.day, tzinfo=TZ)
    return [HourlyPoint(time_utc=(base + timedelta(hours=h)).astimezone(UTC), **fields) for h in range(24)]


def _fanal_bundle(points: list[HourlyPoint]) -> ForecastBundle:
    loc = CFG.as_location()
    return ForecastBundle(model="best_match", fetched_at_utc=NOW,
                          forecasts={"fanal": LocationForecast(location=loc, hourly=points, daily=[])})


def _score(rh=95, base=1150, vis=250, rain=0, gust=10) -> float:
    return score_fog(FogFeatures(rh, base, vis, rain, gust, None), CFG)


def test_weights_yaml_defines_fanal_as_fog_seeker():
    cfg = load_settings().fanal
    assert (cfg.lat, cfg.lon, cfg.elevation_m, cfg.type) == (32.8083, -17.1417, 1150, "fog_seeker")
    assert (cfg.verdict.go, cfg.verdict.maybe) == (70, 40)
    assert cfg.weights.humidity + cfg.weights.cloud_base + cfg.weights.visibility == pytest.approx(1.0)
    assert cfg.webcam_url.startswith("https://www.netmadeira.com/webcams-madeira/")
    assert cfg.as_location().role == "fog_seeker"


def test_thick_fog_scores_mystic_and_dry_day_scores_clear():
    assert score_fog(FOG, CFG) >= 95
    assert verdict(score_fog(FOG, CFG), CFG) == "MYSTIC"
    assert score_fog(DRY, CFG) == 0
    assert verdict(score_fog(DRY, CFG), CFG) == "CLEAR"


def test_verdict_boundaries():
    assert [verdict(s, CFG) for s in (70, 69.9, 40, 39.9, 0)] == ["MYSTIC", "PARTIAL", "PARTIAL", "CLEAR", "CLEAR"]


def test_humidity_is_maximal_between_85_and_100():
    assert _score(rh=85) == _score(rh=100) == pytest.approx(100)
    assert _score(rh=72.5) < _score(rh=85)
    assert _score(rh=60) < 65  # bez vlhkosti zbývá jen základna a dohlednost


def test_cloud_base_optimum_is_900_to_1300_m():
    assert _score(base=900) == _score(base=1100) == _score(base=1300)
    assert _score(base=1500) < _score(base=1300)
    assert _score(base=2200) < _score(base=1500)


def test_visibility_thresholds():
    assert _score(vis=300) == _score(vis=400) > _score(vis=900) > _score(vis=2000) > _score(vis=24000)


def test_drizzle_is_fine_but_downpour_kills_the_score():
    assert _score(rain=1.4) == _score(rain=0)  # mrholení < 1,5 mm/h nevadí
    assert 0 < _score(rain=2.25) < _score(rain=1.5)
    assert _score(rain=3.0) == 0 and _score(rain=8) == 0  # liják > 3 mm/h = 0


def test_wind_above_35_kmh_penalizes():
    assert _score(gust=35) == _score(gust=10)
    assert _score(gust=45) < _score(gust=35)
    assert _score(gust=80) == pytest.approx(0.2 * _score(gust=10), abs=0.1)  # nad 60 km/h zůstává 20 %


def test_missing_factors_are_dropped_not_zeroed():
    assert score_fog(FogFeatures(95, None, None, 0, None, None), CFG) == 100
    assert score_fog(FogFeatures(None, None, None, 0, None, None), CFG) == 0


def test_features_estimate_cloud_base_and_blend_humidity():
    p = HourlyPoint(
        time_utc=NOW, temperature_c=10.0, dew_point_c=8.0, relative_humidity_pct=90,
        levels={925: PressureLevel(geopotential_height_m=800, relative_humidity_pct=100),
                850: PressureLevel(geopotential_height_m=1500, relative_humidity_pct=80)},
    )
    f = fanal.extract_fog_features(p, CFG)
    assert f.cloud_base_m == pytest.approx(1150 + 125 * 2)  # T − Td = 2 °C
    assert fanal.rh_at_height(p, 1150) == pytest.approx(90)  # 1150 m je přesně uprostřed 925/850 hPa
    assert f.humidity_pct == pytest.approx(90)


def test_negative_dewpoint_spread_is_clamped():
    p = HourlyPoint(time_utc=NOW, temperature_c=10.0, dew_point_c=10.4)
    assert fanal.extract_fog_features(p, CFG).cloud_base_m == 1150


def test_best_window_is_longest_run_above_partial():
    base = datetime(2026, 10, 13, tzinfo=TZ)
    pts = [
        HourlyPoint(time_utc=(base + timedelta(hours=h)).astimezone(UTC), **(MIST if 10 <= h < 14 else DRY_DAY))
        for h in range(24)
    ]
    assert fanal.best_window(pts, DAY, CFG) == (10, 14)
    assert fanal.best_window(pts, DAY, CFG, from_hour=12) == (12, 14)  # proběhlé hodiny se nepočítají
    storm = _hours(DAY, **{**MIST, "wind_gusts_kmh": 90, "precipitation_mm": 5})
    assert fanal.best_window(storm, DAY, CFG) is None


def test_section_for_misty_day_has_required_keys():
    now = datetime(2026, 10, 13, 7, 0, tzinfo=TZ)
    section = webexport.fanal_section(CFG, _fanal_bundle(_hours(DAY, **MIST)), now)
    assert section["day"] == "today"
    assert section["verdict"] == "MYSTIC" and section["score"] >= 70
    assert {"verdict", "score", "humidity", "cloud_coverage_local", "wind_gusts", "rain", "summary",
            "best_window", "webcam_url"} <= section.keys()
    assert section["best_window"].startswith("Dopoledne 08:00–")
    assert [s["hour"] for s in section["slots"]] == [9, 13, 17]
    assert "mlha" in section["summary"].lower()
    assert section["humidity"] == 98 and section["wind_gusts"] == 15 and section["rain"] == 0.3


def test_today_slots_in_the_past_fall_back_to_tomorrow():
    pts = _hours(DAY, **MIST) + _hours(DAY + timedelta(days=1), **DRY_DAY)
    late = webexport.fanal_section(CFG, _fanal_bundle(pts), datetime(2026, 10, 13, 18, 0, tzinfo=TZ))
    assert "today" not in late and late["day"] == "tomorrow"
    assert late["verdict"] == "CLEAR" and late["best_window"] is None
    morning = webexport.fanal_section(CFG, _fanal_bundle(pts), datetime(2026, 10, 13, 10, 0, tzinfo=TZ))
    assert [s["hour"] for s in morning["today"]["slots"]] == [13, 17]  # 9:00 už proběhlo


def test_clear_summary_names_the_reason():
    now = datetime(2026, 10, 13, 7, 0, tzinfo=TZ)
    windy = webexport.fanal_section(CFG, _fanal_bundle(_hours(DAY, **{**MIST, "wind_gusts_kmh": 80})), now)
    rainy = webexport.fanal_section(CFG, _fanal_bundle(_hours(DAY, **{**MIST, "precipitation_mm": 6.0})), now)
    assert windy["verdict"] == rainy["verdict"] == "CLEAR"
    assert "vítr" in windy["summary"] and "déšť" in rainy["summary"]


def test_section_is_none_without_data():
    now = datetime(2026, 10, 13, 7, 0, tzinfo=TZ)
    assert webexport.fanal_section(CFG, _fanal_bundle([]), now) is None
    assert webexport.fanal_section(CFG, ForecastBundle(model="x", fetched_at_utc=NOW, forecasts={}), now) is None


def test_payload_includes_fanal_only_when_bundle_given_and_is_valid_json(tmp_path):
    settings = Settings()
    assert "fanal" not in webexport.build_payload(settings, days=1, bundle=_bundle(), now=NOW)
    now = datetime(2026, 10, 12, 9, 0, tzinfo=TZ)
    fb = _fanal_bundle(_hours(date(2026, 10, 12), **MIST) + _hours(date(2026, 10, 13), **MIST))
    payload = webexport.build_payload(settings, days=1, bundle=_bundle(), now=now, fanal_bundle=fb)
    out = tmp_path / "data.json"
    webexport.write_payload(payload, out)
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["fanal"]["verdict"] == "MYSTIC"
    assert {"sunrise", "sunset", "fanal"} <= loaded.keys()
