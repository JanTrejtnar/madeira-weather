import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from madeira.analyzers.features import SunriseFeatures, interp
from madeira.analyzers.inversion import analyze_inversion, estimate_deck_top
from madeira.analyzers.microclimate import compare_microclimates
from madeira.analyzers.mountain import high_deck_gate, wind_driven_fog
from madeira.analyzers.scoring_utils import pw
from madeira.analyzers.sunrise import analyze_sunrise, score_sunrise
from madeira.analyzers.sunset import SunsetFeatures, analyze_sunset, score_sunset
from madeira.domain.config import MountainCloudConfig, Settings, load_locations
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


# --- západ slunce (Ponta do Pargo) ------------------------------------------------


def sunset_features(**over) -> SunsetFeatures:
    """Ideální večer: čistý obzor, ~35 % středních/vysokých mraků, bez deště a mlhy."""
    base = {
        "target_date": date(2026, 10, 13),
        "sunset_utc": datetime(2026, 10, 13, 18, 37, tzinfo=UTC),
        "days_ahead": 1,
        "horizon_low_cloud": 0.0,
        "canvas_cloud": 35.0,
        "local_low_cloud": 0.0,
        "precip_mm": 0.0,
        "gust_kmh": 20.0,
        "wind_kmh": 15.0,
        "wind_dir_deg": 20.0,
        "visibility_m": 30000.0,
        "dewpoint_depression_c": 8.0,
        "temp_c": 20.0,
    }
    base.update(over)
    return SunsetFeatures(**base)


def test_sunset_perfect_evening_is_go():
    a = score_sunset(sunset_features(), Settings())
    assert a.verdict == "GO"
    assert a.score_pct == 100.0
    assert a.gates == {"precip": 1.0, "horizon": 1.0, "visibility": 1.0, "overcast": 1.0}


def test_sunset_canvas_curve_peaks_at_35_pct():
    s = Settings()
    score = {c: score_sunset(sunset_features(canvas_cloud=c), s).score_pct for c in (0, 35, 70, 90)}
    # 0 % = obyčejný západ (plátno 50), 35 % = nejlepší, >85 % = zataženo (plátno 0)
    assert score[35] > score[0] > score[70] > score[90]
    assert score[0] == 100 - 0.35 * 100 * 0.5
    assert score[90] == 100 - 0.35 * 100


def test_sunset_rain_is_instant_no_go():
    a = score_sunset(sunset_features(precip_mm=0.2), Settings())
    assert a.score_pct == 0 and a.verdict == "NO_GO"
    assert a.gates["precip"] == 0.0 and a.warnings


def test_sunset_low_clouds_on_horizon_hurt():
    s = Settings()
    clear = score_sunset(sunset_features(), s).score_pct
    blocked = score_sunset(sunset_features(horizon_low_cloud=50.0), s)
    assert blocked.score_pct < 0.5 * clear
    assert blocked.gates["horizon"] < 1.0
    assert blocked.verdict == "NO_GO"


def test_sunset_gale_and_fog_lower_score():
    s = Settings()
    base = score_sunset(sunset_features(), s).score_pct
    assert score_sunset(sunset_features(gust_kmh=70.0), s).score_pct == base - 10
    assert score_sunset(sunset_features(local_low_cloud=70.0), s).score_pct == base - 15
    assert score_sunset(sunset_features(visibility_m=5000.0), s).gates["visibility"] == 0.3


def test_sunset_end_to_end_on_fixture():
    bundle = parse_response(json.loads(FIXTURE.read_text()), load_locations(), "best_match", NOW)
    target = bundle.forecasts["ponta_do_pargo"].daily[1].date
    a = analyze_sunset(bundle, target, Settings(), NOW)
    assert 0 <= a.score_pct <= 100 and a.summary_cs
    assert a.sunset_utc.date() == target
    # Bica da Cana běží stejnou heuristikou východu jako Arieiro, jen s jiným laděním
    b = analyze_sunrise(bundle, target, Settings(), NOW, spot="bica_da_cana")
    assert 0 <= b.score_pct <= 100


def test_bica_tuning_shifts_thresholds():
    # mraky sahají do ~1500 m: pro Arieiro (1818 m) v pohodě, pro Bicu (1560 m, strop 1400 m) ne
    f = features(
        summit_m=1560.0,
        aloft_hpa=850,
        rh={925: 99.0, 850: 98.0, 800: 42.0, 700: 9.0},
        z={925: 860.0, 850: 1500.0, 800: 2040.0, 700: 3175.0},
    )
    plain = analyze_inversion(f)
    bica = analyze_inversion(f, cloud_top_max_m=1400)
    assert bica.probability_pct < plain.probability_pct
    s = Settings()
    assert score_sunrise(f, s, s.bica_da_cana.tuning()).score_pct <= score_sunrise(f, s).score_pct


def test_new_spot_configuration():
    s = Settings()
    locs = {loc.key: loc for loc in load_locations()}
    assert (locs["ponta_do_rosto"].lat, locs["ponta_do_rosto"].lon) == (32.7492, -16.7068)
    assert locs["pico_ruivo"].elevation_m == 1862
    assert locs["cristo_rei"].role == "spot_sunset"
    assert s.spots["ponta_do_rosto"].type == "coastal_sunrise"
    assert s.spots["pico_ruivo"].type == "mountain_inversion"
    assert s.spots["cristo_rei"].type == "coastal_sunset"
    assert s.spots["pico_ruivo"].departure_from_funchal_min > s.spots["arieiro"].departure_from_funchal_min


def test_coastal_sunrise_rewards_clear_east_horizon_and_penalises_gusts():
    from madeira.analyzers.coastal_sunrise import score_coastal_sunrise

    s = Settings()
    clear = features(east_low_cloud=10.0, mid_cloud=30.0, high_cloud=30.0, gust_kmh=15.0,
                     visibility_km=25.0, dewpoint_depression_c=4.0)
    base = score_coastal_sunrise(clear, s)
    assert base.verdict == "GO" and base.gates["horizon"] == 1.0
    blocked = score_coastal_sunrise(features(**{**clear.__dict__, "east_low_cloud": 70.0}), s)
    assert blocked.gates["horizon"] < 1.0 and blocked.score_pct < 0.5 * base.score_pct
    windy = score_coastal_sunrise(features(**{**clear.__dict__, "gust_kmh": 60.0}), s)
    assert windy.score_pct < base.score_pct
    wet = score_coastal_sunrise(features(**{**clear.__dict__, "precip_mm": 0.6}), s)
    assert wet.gates["precip"] == 0.0 and wet.verdict == "NO_GO"


def test_new_spots_end_to_end_on_fixture():
    from madeira.analyzers.coastal_sunrise import score_coastal_sunrise
    from madeira.analyzers.sunrise import extract_spot_features

    bundle = parse_response(json.loads(FIXTURE.read_text()), load_locations(), "best_match", NOW)
    target = bundle.forecasts["ponta_do_pargo"].daily[1].date
    ruivo = analyze_sunrise(bundle, target, Settings(), NOW, spot="pico_ruivo")
    assert 0 <= ruivo.score_pct <= 100
    f, _ = extract_spot_features(bundle, target, Settings(), NOW, "ponta_do_rosto")
    assert 0 <= score_coastal_sunrise(f, Settings()).score_pct <= 100
    cristo = analyze_sunset_at(bundle, target, "cristo_rei")
    assert 0 <= cristo.score_pct <= 100 and cristo.sunset_utc.date() == target


def analyze_sunset_at(bundle, target, key):
    from madeira.analyzers.sunset import extract_sunset_features

    return score_sunset(extract_sunset_features(bundle, target, NOW, Settings(), spot_key=key), Settings())


def test_mountain_sunset_ignores_clouds_below_summit():
    from dataclasses import replace

    s = Settings()
    base = sunset_features(horizon_low_cloud=60.0, local_low_cloud=80.0, dewpoint_depression_c=0.2)
    under_summit = replace(base, summit_clear=1.0)  # vrchol nad mraky: moře mraků pod ním
    in_cloud = replace(base, summit_clear=0.0)  # vrchol v mraku: bez úlevy
    assert score_sunset(base, s).score_pct == score_sunset(in_cloud, s).score_pct
    assert score_sunset(under_summit, s).score_pct > 3 * score_sunset(in_cloud, s).score_pct
    assert score_sunset(under_summit, s).gates["horizon"] == 1.0


def test_mountain_sunset_on_fixture_uses_profile():
    from madeira.analyzers.sunset import extract_sunset_features

    bundle = parse_response(json.loads(FIXTURE.read_text()), load_locations(), "best_match", NOW)
    target = bundle.forecasts["arieiro"].daily[1].date
    f = extract_sunset_features(bundle, target, NOW, Settings(), spot_key="arieiro", mountain=True)
    assert f.summit_clear is not None and 0 <= f.summit_clear <= 1
    assert extract_sunset_features(bundle, target, NOW, Settings(), spot_key="ponta_do_pargo").summit_clear is None


def test_sunset_clear_sky_is_go_and_clouds_add_drama():
    s = Settings()
    clear = score_sunset(sunset_features(canvas_cloud=0.0), s)
    assert clear.verdict == "GO" and 75 <= clear.score_pct <= 85  # čisté nebe není trestáno
    drama = score_sunset(sunset_features(canvas_cloud=35.0), s)
    assert drama.score_pct >= 90  # 20–60 % vysokých/středních mraků = barevné plátno
    assert score_sunset(sunset_features(canvas_cloud=55.0), s).score_pct >= 85


def test_sunset_full_overcast_is_no_go():
    s = Settings()
    stratus = score_sunset(sunset_features(horizon_low_cloud=100.0, west_mid_cloud=100.0), s)
    assert stratus.score_pct < 40 and stratus.verdict == "NO_GO"
    mid_deck = score_sunset(sunset_features(canvas_cloud=50.0, west_mid_cloud=100.0), s)
    assert mid_deck.gates["overcast"] == s.sunset.gates.overcast_gate_min and mid_deck.score_pct < 40


def test_mountain_sunset_fog_gate_only_with_wet_summit():
    from dataclasses import replace

    s = Settings()
    base = replace(
        sunset_features(horizon_low_cloud=60.0, local_low_cloud=70.0, visibility_m=4500.0, canvas_cloud=0.0),
        summit_clear=1.0, rh_aloft=30.0, summit_rh=40.0, deck_top_m=1100.0,
    )
    above = score_sunset(base, s)  # moře mraků pod vrcholem + model hlásí špatnou přízemní dohlednost
    assert above.verdict == "GO" and above.gates["fog"] == 1.0 and above.gates["visibility"] == 1.0
    assert above.score_pct > score_sunset(replace(base, deck_top_m=1700.0), s).score_pct  # bonus jen pod 1 400 m
    foggy = score_sunset(replace(base, rh_aloft=88.0, summit_clear=0.0), s)
    assert foggy.gates["fog"] < 0.6 and foggy.verdict == "NO_GO"
    border = score_sunset(replace(base, rh_aloft=80.0, summit_rh=80.0), s)
    assert border.gates["fog"] == 1.0  # pod 85 % se brána nepoužije


def test_coastal_sunset_still_penalises_low_cloud_on_western_horizon():
    s = Settings()
    assert score_sunset(sunset_features(horizon_low_cloud=60.0), s).score_pct < 0.5 * score_sunset(sunset_features(), s).score_pct


def test_sunset_precip_window_ends_15_min_after_sunset():
    from madeira.analyzers.sunset import extract_sunset_features

    bundle = parse_response(json.loads(FIXTURE.read_text()), load_locations(), "best_match", NOW)
    target = bundle.forecasts["ponta_do_pargo"].daily[1].date
    spot = bundle.forecasts["ponta_do_pargo"]
    sunset = next(d.sunset_utc for d in spot.daily if d.date == target)
    for p in spot.hourly:  # déšť jen v hodině 45+ min po západu → nesmí vstoupit do skóre
        p.precipitation_mm = 5.0 if timedelta(minutes=46) <= p.time_utc - sunset <= timedelta(minutes=120) else 0.0
    f = extract_sunset_features(bundle, target, NOW, Settings())
    assert f.precip_mm == 0.0


# --- horské spoty: mraky nad vrcholem a mlha hnaná větrem (18. 10. 2026) --------------


def features_oct18(**over):
    """18. 10. 2026 Arieiro: základna mraků ~2250 m (nad vrcholem), RH u vrcholu 74 %, v 800 hPa
    77 %, nárazy 54 km/h; na vrcholu hustá mlha, model přitom hlásil GO 75 %."""
    base = {
        "target_date": date(2026, 10, 18),
        "sunrise_utc": datetime(2026, 10, 18, 8, 12, tzinfo=UTC),
        "low_cloud_summit": 35.0,
        "mid_cloud": 17.0,
        "high_cloud": 0.0,
        "east_low_cloud": 5.0,
        "east_mid_cloud": 5.0,
        "lowland_low_cloud": 20.0,
        "precip_prob": 10.0,
        "gust_kmh": 54.0,
        "wind_kmh": 30.0,
        "visibility_km": 20.0,
        "dewpoint_depression_c": 3.5,
        "t": {925: 16.0, 850: 12.0, 800: 9.0, 700: 3.0},
        "rh": {925: 80.0, 850: 71.0, 800: 77.0, 700: 60.0},
    }
    base.update(over)
    return features(**base)


def test_oct18_summit_under_cloud_is_no_go():
    s = Settings()
    a = score_sunrise(features_oct18(), s)
    assert a.verdict == "NO_GO" and a.score_pct < 50, a
    assert 20 <= a.score_pct <= 35 and a.gates["fog"] < 0.5, a  # NO-GO, ale ne nula
    # bez nových pravidel to byl falešný GO (~75 %)
    old = Settings()
    old.mountain_clouds.aloft_rh_fog_windy = 99
    assert score_sunrise(features_oct18(), old).score_pct >= 70


def test_clean_inversion_days_stay_go():
    # 14.–16. 10.: vlhko jen pod vrcholem (mraky do ~1200 m), nahoře sucho
    for over in (
        {"low_cloud_summit": 82.0, "rh": {925: 99.0, 850: 98.0, 800: 27.0, 700: 9.0}},
        {"low_cloud_summit": 49.0, "gust_kmh": 11.0, "rh": {925: 97.0, 850: 90.0, 800: 24.0, 700: 8.0}},
        {"low_cloud_summit": 0.0, "mid_cloud": 0.0, "gust_kmh": 14.0, "rh": {925: 60.0, 850: 50.0, 800: 39.0, 700: 9.0}},
    ):
        a = score_sunrise(features(**over), Settings())
        assert a.verdict == "GO" and a.score_pct >= 80, (over, a.score_pct)
        assert "high_deck" not in a.gates


def test_wind_lowers_summit_humidity_threshold():
    over = {"dewpoint_depression_c": 3.0, "rh": {925: 80.0, 850: 60.0, 800: 77.0, 700: 40.0}}
    calm = score_sunrise(features_oct18(gust_kmh=30.0, **over), Settings())
    windy = score_sunrise(features_oct18(gust_kmh=48.0, **over), Settings())
    assert calm.gates["fog"] > 0.9  # RH 77 % < 80 % → bez penalizace mlhy
    assert windy.gates["fog"] < 0.6  # při nárazech >= 45 km/h práh 68 %
    cfg = MountainCloudConfig()
    assert wind_driven_fog(79.0, 10.0, 40.0, cfg) == 0.0
    assert wind_driven_fog(79.0, 50.0, 40.0, cfg) == cfg.wind_fog_max
    assert wind_driven_fog(79.0, 50.0, 10.0, cfg) == 0.0  # vlhko, ale skoro bez mraků


def test_high_deck_gate_uses_cloud_base_from_sea_level():
    cfg = MountainCloudConfig()
    # T − Td na pobřeží 16 °C → základna ~2000 m nad mořem, tedy nad vrcholem 1818 m
    assert high_deck_gate(1818, 16.0, 35.0, 1.0, cfg) == cfg.high_deck_gate
    assert high_deck_gate(1818, 16.0, 20.0, 1.0, cfg) == 1.0  # skoro čisté nebe
    assert high_deck_gate(1818, 6.0, 80.0, 1.0, cfg) == 1.0  # mraky v ~750 m = vrchol je nad nimi
    assert high_deck_gate(1818, None, 60.0, 1.0, cfg) == 1.0  # bez dat z pobřeží
    assert high_deck_gate(1818, 16.0, 60.0, 0.0, cfg) == 1.0  # sucho nad vrcholem


def test_mountain_sunset_also_penalizes_summit_under_cloud():
    s = Settings()
    f = SunsetFeatures(
        target_date=date(2026, 10, 18),
        sunset_utc=datetime(2026, 10, 18, 18, 40, tzinfo=UTC),
        days_ahead=1, horizon_low_cloud=10.0, canvas_cloud=30.0, local_low_cloud=35.0,
        precip_mm=0.0, gust_kmh=54.0, wind_kmh=30.0, wind_dir_deg=40.0, visibility_m=20000.0,
        dewpoint_depression_c=3.5, temp_c=12.0, summit_clear=1.0, west_mid_cloud=17.0,
        rh_aloft=77.0, summit_rh=74.0, deck_top_m=None, summit_elevation_m=1818.0,
    )
    a = score_sunset(f, s)
    assert a.verdict == "NO_GO" and a.gates["fog"] < 0.5
    dry = replace(f, rh_aloft=30.0, summit_rh=40.0, deck_top_m=1100.0)  # inverze: bez penalizace
    assert score_sunset(dry, s).verdict == "GO"


def test_high_deck_gate_in_sunrise_is_not_stacked_on_fog_gate():
    s = Settings()
    calm = features_oct18(gust_kmh=20.0, coast_dd_c=16.0, rh={925: 80.0, 850: 68.0, 800: 72.0, 700: 60.0})
    a = score_sunrise(calm, s)
    assert a.gates["high_deck"] == s.mountain_clouds.high_deck_gate
    assert a.gates["fog"] > a.gates["high_deck"]  # platí přísnější brána, nesčítají se
    assert 20 <= a.score_pct <= 40
    assert "high_deck" not in score_sunrise(replace(calm, coast_dd_c=5.0), s).gates
