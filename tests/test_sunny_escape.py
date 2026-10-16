from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from madeira.analyzers.sunny_escape import rank_locations, recommendation_cs, score_point
from madeira.domain.config import EscapeConfig, Settings, load_settings
from madeira.domain.models import ForecastBundle, HourlyPoint, LocationForecast
from madeira.services import webexport
from tests.test_analyzers import NOW
from tests.test_webexport import _bundle

TZ = ZoneInfo("Atlantic/Madeira")
DAY = date(2026, 10, 13)


def _hours(day: date, **fields) -> list[HourlyPoint]:
    """Hodinové body 8:00–19:00 místního času se stejnými hodnotami."""
    base = datetime(day.year, day.month, day.day, tzinfo=TZ)
    return [
        HourlyPoint(time_utc=(base + timedelta(hours=h)).astimezone(UTC), **fields) for h in range(8, 20)
    ]


SUNNY = {
    "cloud_cover_low_pct": 5, "cloud_cover_mid_pct": 5, "cloud_cover_high_pct": 10,
    "cloud_cover_total_pct": 15, "precipitation_mm": 0.0, "temperature_c": 24,
    "sunshine_duration_s": 3500,
}
CLOUDY = {
    "cloud_cover_low_pct": 90, "cloud_cover_mid_pct": 70, "cloud_cover_high_pct": 50,
    "cloud_cover_total_pct": 95, "precipitation_mm": 0.0, "temperature_c": 19,
    "sunshine_duration_s": 300,
}
RAINY = {**CLOUDY, "precipitation_mm": 1.5}


def _bundle_for(cfg: EscapeConfig, per_point: dict[str, dict], day: date = DAY) -> ForecastBundle:
    forecasts = {
        p.key: LocationForecast(location=p.as_location(), hourly=_hours(day, **per_point[p.key]), daily=[])
        for p in cfg.points
        if p.key in per_point
    }
    return ForecastBundle(model="best_match", fetched_at_utc=NOW, forecasts=forecasts)


def test_weights_yaml_defines_the_five_coastal_points():
    cfg = load_settings().sunny_escape
    assert [p.key for p in cfg.points] == ["ponta_do_sol", "calheta", "machico", "sao_vicente", "funchal"]
    sol = cfg.points[0]
    assert (sol.lat, sol.lon, sol.elevation_m) == (32.6806, -17.1042, 20)
    assert (cfg.hour_from, cfg.hour_to) == (11, 16)


def test_sunny_beats_cloudy_and_rain_crushes_score():
    cfg = EscapeConfig()
    sunny = score_point(_hours(DAY, **SUNNY), DAY, cfg)
    cloudy = score_point(_hours(DAY, **CLOUDY), DAY, cfg)
    rainy = score_point(_hours(DAY, **RAINY), DAY, cfg)
    assert sunny["sun_score"] >= 90 and sunny["sky"] == "clear"
    assert cloudy["sun_score"] < 30 and cloudy["sky"] == "cloudy"
    assert rainy["sun_score"] < cloudy["sun_score"]  # srážky jsou prudká penalizace
    assert rainy["sky"] == "rain" and rainy["precip_mm"] == 7.5  # 5 hodin okna × 1,5 mm


def test_any_rain_above_threshold_drops_otherwise_sunny_point():
    cfg = EscapeConfig()
    dry = score_point(_hours(DAY, **SUNNY), DAY, cfg)
    wet = score_point(_hours(DAY, **{**SUNNY, "precipitation_mm": 0.1}), DAY, cfg)  # 0,5 mm za okno
    assert wet["sun_score"] <= dry["sun_score"] * 0.5


def test_low_and_mid_clouds_weigh_more_than_high_clouds():
    cfg = EscapeConfig()
    base = {**SUNNY, "sunshine_duration_s": None, "cloud_cover_total_pct": None}
    low = score_point(_hours(DAY, **{**base, "cloud_cover_low_pct": 80, "cloud_cover_high_pct": 0}), DAY, cfg)
    high = score_point(_hours(DAY, **{**base, "cloud_cover_low_pct": 5, "cloud_cover_high_pct": 80}), DAY, cfg)
    assert low["sun_score"] < high["sun_score"]


def test_radiation_is_fallback_when_sunshine_is_missing():
    cfg = EscapeConfig()
    fields = {**SUNNY, "sunshine_duration_s": None}
    bright = score_point(_hours(DAY, **fields, direct_radiation_wm2=700), DAY, cfg)
    dark = score_point(_hours(DAY, **fields, direct_radiation_wm2=0), DAY, cfg)
    assert bright["sunshine_pct"] == 100 and dark["sunshine_pct"] == 0
    assert bright["sun_score"] > dark["sun_score"]


def test_only_the_daytime_window_counts():
    cfg = EscapeConfig()
    base = datetime(2026, 10, 13, tzinfo=TZ)
    rainy_morning = [  # déšť jen v 8–10 h, okno 11–16 h je suché
        HourlyPoint(time_utc=(base + timedelta(hours=h)).astimezone(UTC),
                    **{**SUNNY, "precipitation_mm": 5.0 if h < 11 else 0.0})
        for h in range(8, 20)
    ]
    assert score_point(rainy_morning, DAY, cfg)["precip_mm"] == 0.0
    assert score_point(rainy_morning, date(2026, 10, 14), cfg) is None  # jiný den bez dat


def test_ranking_picks_winner_and_sorts_from_sunniest_to_rainiest():
    cfg = EscapeConfig()
    bundle = _bundle_for(cfg, {
        "funchal": RAINY, "sao_vicente": CLOUDY, "ponta_do_sol": SUNNY,
        "calheta": {**SUNNY, "cloud_cover_low_pct": 30, "sunshine_duration_s": 2500}, "machico": CLOUDY,
    })
    rows = rank_locations(bundle, DAY, cfg)
    assert rows[0]["key"] == "ponta_do_sol"
    assert rows[1]["key"] == "calheta"
    assert rows[-1]["key"] == "funchal"
    assert [r["sun_score"] for r in rows] == sorted((r["sun_score"] for r in rows), reverse=True)
    assert {"name", "sun_score", "temp_c", "precip_mm", "sky", "cloud_pct", "lat", "lon"} <= rows[0].keys()
    assert recommendation_cs("today", rows[0]) == "Dnes jeďte do Ponta do Sol – 24 °C, jasno, 0 mm srážek"
    funchal = next(r for r in rows if r["key"] == "funchal")
    assert recommendation_cs("tomorrow", funchal).startswith("Zítra jeďte do Funchalu – ")


def test_section_has_winner_locations_and_picks_default_day():
    cfg = Settings().sunny_escape
    morning = datetime(2026, 10, 13, 9, 0, tzinfo=TZ)
    bundle = _bundle_for(cfg, {k.key: SUNNY for k in cfg.points})
    tomorrow = _bundle_for(cfg, {k.key: CLOUDY for k in cfg.points}, DAY + timedelta(days=1))
    for key, fc in tomorrow.forecasts.items():
        bundle.forecasts[key].hourly += fc.hourly

    section = webexport.sunny_escape_section(cfg, bundle, morning)
    assert section["day"] == "today" and section["winner"]["sun_score"] >= 90
    assert section["winner"]["recommendation"].startswith("Dnes jeďte do ")
    assert len(section["locations"]) == 5
    assert section["tomorrow"]["winner"]["sun_score"] < section["today"]["winner"]["sun_score"]

    evening = datetime(2026, 10, 13, 18, 0, tzinfo=TZ)  # okno už skončilo → doporuč zítřek
    assert webexport.sunny_escape_section(cfg, bundle, evening)["day"] == "tomorrow"


def test_section_is_none_without_data():
    cfg = Settings().sunny_escape
    empty = ForecastBundle(model="best_match", fetched_at_utc=NOW, forecasts={})
    assert webexport.sunny_escape_section(cfg, empty, NOW) is None


def test_payload_includes_sunny_escape_only_when_bundle_given():
    settings = Settings()
    assert "sunny_escape" not in webexport.build_payload(settings, days=1, bundle=_bundle(), now=NOW)
    now = datetime(2026, 10, 12, 9, 0, tzinfo=TZ)
    escape = _bundle_for(settings.sunny_escape, {p.key: SUNNY for p in settings.sunny_escape.points},
                         date(2026, 10, 12))
    payload = webexport.build_payload(settings, days=1, bundle=_bundle(), now=now, escape_bundle=escape)
    assert payload["sunny_escape"]["winner"]["name"]
