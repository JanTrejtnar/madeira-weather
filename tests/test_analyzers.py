import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from madeira.analyzers.features import SunriseFeatures, interp
from madeira.analyzers.inversion import analyze_inversion, estimate_deck_top
from madeira.analyzers.microclimate import compare_microclimates
from madeira.analyzers.scoring_utils import pw
from madeira.analyzers.sunrise import analyze_sunrise, score_sunrise
from madeira.domain.config import Settings, load_locations
from madeira.domain.models import HourlyPoint
from madeira.fetchers.open_meteo import parse_response

FIXTURE = Path(__file__).parent / "fixtures" / "forecast_sample.json"
NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


def features(**over) -> SunriseFeatures:
    """Den s inverzí jako 10. 10. 2026 (hodnoceno "Nádherná inverze"): model u vrcholu hlásí
    mlhu (nízká oblačnost, rosný bod), ale vzduch v 800 hPa nad vrcholem je suchý."""
    base = {
        "target_date": date(2026, 10, 13),
        "sunrise_utc": datetime(2026, 10, 13, 8, 10, tzinfo=UTC),
        "days_ahead": 1,
        "low_cloud_summit": 75.0,
        "mid_cloud": 30.0,
        "high_cloud": 30.0,
        "east_low_cloud": 60.0,
        "east_mid_cloud": 10.0,
        "lowland_low_cloud": 90.0,
        "precip_mm": 0.0,
        "precip_prob": 5.0,
        "gust_kmh": 20.0,
        "wind_kmh": 20.0,
        "wind_dir_deg": 40.0,
        "visibility_km": 0.7,
        "dewpoint_depression_c": 0.2,
        "temp_c": 12.0,
        "apparent_temp_c": 10.0,
        "t": {925: 13.9, 850: 10.0, 800: 9.3, 700: 7.3},
        "rh": {925: 99.0, 850: 98.0, 800: 42.0, 700: 9.0},
        "z": {925: 860.0, 850: 1569.0, 800: 2073.0, 700: 3175.0},
    }
    base.update(over)
    return SunriseFeatures(**base)


def test_pw_clamps_and_interpolates():
    pts = [(0, 1.0), (10, 0.0)]
    assert pw(-5, pts) == 1.0
    assert pw(5, pts) == 0.5
    assert pw(50, pts) == 0.0


def test_interp_between_hours():
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    pts = [
        HourlyPoint(time_utc=t0, temperature_c=10),
        HourlyPoint(time_utc=t0 + timedelta(hours=1), temperature_c=20),
    ]
    assert interp(pts, lambda p: p.temperature_c, t0 + timedelta(minutes=30)) == 15


def test_cloud_sea_day_is_good_despite_low_cloud_over_summit():
    a = score_sunrise(features(), Settings())
    assert a.verdict == "GO"
    assert (
        a.gates["fog"] > 0.9
    )  # přízemní vlhkost nesmí sestřelit den se suchým vzduchem nad vrcholem
    assert a.inversion.probability_pct > 60
    assert a.inversion.summit_above_deck_m > 100


def test_surface_humidity_cannot_trigger_fog_gate_when_air_aloft_is_dry():
    dry_aloft = score_sunrise(features(), Settings())
    moist_aloft = score_sunrise(
        features(rh={925: 99.0, 850: 98.0, 800: 85.0, 700: 40.0}), Settings()
    )
    assert dry_aloft.gates["fog"] > 0.9
    assert moist_aloft.gates["fog"] < 0.5
    assert moist_aloft.score_pct < dry_aloft.score_pct / 2


def test_same_low_cloud_without_inversion_is_fog():
    f = features(
        rh={925: 95.0, 850: 98.0, 700: 97.0},
        dewpoint_depression_c=0.3,
        t={925: 16, 850: 11, 700: 0},
    )
    a = score_sunrise(f, Settings())
    assert a.verdict == "NO_GO"
    assert a.gates["fog"] < 0.5
    assert a.inversion.probability_pct < 40


def test_rain_kills_score():
    a = score_sunrise(features(precip_mm=1.2, precip_prob=90), Settings())
    assert a.gates["precip"] < 0.5
    assert a.verdict == "NO_GO"


def test_cloudsea_neutral_by_default_but_weightable():
    base = Settings()
    assert all(c.name != "cloudsea" for c in score_sunrise(features(), base).components)
    pref = Settings()
    pref.sunrise.weights.cloudsea = 0.2
    names = [c.name for c in score_sunrise(features(), pref).components]
    assert "cloudsea" in names


def test_component_weights_normalised():
    a = score_sunrise(features(), Settings())
    assert abs(sum(c.weight for c in a.components) - 1.0) < 0.01


def test_no_moist_layer_means_no_deck():
    f = features(rh={925: 40.0, 850: 30.0, 700: 20.0})
    assert estimate_deck_top(f) is None
    assert analyze_inversion(f).probability_pct < 50


def test_confidence_drops_with_horizon():
    assert score_sunrise(features(days_ahead=1), Settings()).confidence == "HIGH"
    assert score_sunrise(features(days_ahead=6), Settings()).confidence == "LOW"


def test_parse_and_end_to_end_on_real_fixture():
    locations = load_locations()
    bundle = parse_response(json.loads(FIXTURE.read_text()), locations, "best_match", NOW)
    assert set(bundle.forecasts) == {loc.key for loc in locations}
    arieiro = bundle.forecasts["arieiro"]
    assert len(arieiro.hourly) == 7 * 24
    assert 850 in arieiro.hourly[0].levels

    target = arieiro.daily[1].date
    a = analyze_sunrise(bundle, target, Settings(), arieiro.daily[0].sunrise_utc)
    assert 0 <= a.score_pct <= 100
    assert a.summary_cs

    start = arieiro.daily[1].sunrise_utc
    comp = compare_microclimates(bundle, start, start + timedelta(hours=4))
    assert len(comp.rows) == 5  # bez east_horizon
    assert [r.rank for r in comp.rows] == [1, 2, 3, 4, 5]


def _load_calibration():
    """Reálné dny 5.–12. 10. 2026 s hodnocením z chatu (features zpětně dopočítané z Open-Meteo)."""
    rows = json.loads(
        (Path(__file__).parent / "fixtures" / "calibration_2026-10.json").read_text("utf-8")
    )
    out = []
    for r in rows:
        f = r["features"]
        for key in ("t", "rh", "z"):
            f[key] = {int(k): v for k, v in f[key].items()}
        f["target_date"] = date.fromisoformat(f["target_date"])
        f["sunrise_utc"] = datetime.fromisoformat(f["sunrise_utc"])
        out.append((r["target_date"], r["outcome"], SunriseFeatures(**f)))
    return out


def test_calibration_days_are_ranked_by_real_outcome():
    """Regrese: inverze (10., 11., 12., 6. 10.) musí dostat vysoké skóre, mlha (5., 8., 9. 10.) nízké.

    Pozor: jen 8 dní → prahy mlhy jsou odvozené z malého vzorku; při přibývajících datech je přehodnoť.
    """
    scores = {}
    for day, outcome, f in _load_calibration():
        scores[day] = (outcome, score_sunrise(f, Settings()).score_pct)
    sea = [s for o, s in scores.values() if o == "cloud_sea"]
    fail = [s for o, s in scores.values() if o == "fail"]
    assert len(sea) == 4 and len(fail) == 3
    assert min(sea) >= 65, scores
    assert max(fail) <= 30, scores
