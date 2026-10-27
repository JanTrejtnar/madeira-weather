"""A1/A5: azimut slunce, dynamický bod obzoru a stínění terénem v ose slunce."""

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from madeira.analyzers.sunrise import extract_spot_features, score_sunrise
from madeira.analyzers.sunset import extract_sunset_features, score_sunset
from madeira.domain.config import (
    Settings,
    horizon_key,
    load_locations,
    terrain_cutoff_deg,
    terrain_gate,
    with_sun_horizons,
)
from madeira.domain.solar import destination, in_azimuth, sun_azimuth_deg
from madeira.fetchers.open_meteo import parse_response

FIXTURE = Path(__file__).parent / "fixtures" / "forecast_sample.json"
NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
LAT = 32.7


def test_sun_azimuth_follows_the_seasons():
    assert 60 < sun_azimuth_deg(LAT, date(2026, 6, 21), "sunrise") < 65  # léto: severovýchod
    assert 115 < sun_azimuth_deg(LAT, date(2026, 12, 21), "sunrise") < 120  # zima: jihovýchod
    assert 88 < sun_azimuth_deg(LAT, date(2026, 3, 20), "sunrise") < 92  # rovnodennost: východ
    assert 104 < sun_azimuth_deg(LAT, date(2026, 10, 27), "sunrise") < 107
    # západ je zrcadlo východu
    for d in (date(2026, 1, 5), date(2026, 6, 21), date(2026, 10, 27)):
        assert abs(sun_azimuth_deg(LAT, d, "sunrise") + sun_azimuth_deg(LAT, d, "sunset") - 360) < 1e-9


def test_destination_55_km_east_and_west():
    lat, lon = destination(LAT, -16.9, 90, 55)
    assert abs(lat - LAT) < 0.05 and 0.55 < lon + 16.9 < 0.62  # 55 km / (111,3·cos 32,7°) ≈ 0,59°
    _, lon_w = destination(LAT, -16.9, 270, 55)
    assert -17.5 < lon_w < -17.4
    # přes datovou hranici 180° se zalomí do −180..180
    assert -180 <= destination(0, 179.9, 90, 100)[1] <= 180


def test_in_azimuth_wraps_through_north():
    assert in_azimuth(5, 350, 20) and in_azimuth(355, 350, 20) and not in_azimuth(180, 350, 20)
    assert in_azimuth(90, 90, 105) and in_azimuth(105, 90, 105) and not in_azimuth(105.1, 90, 105)


def test_terrain_cutoff_table_matches_agreed_geometry():
    s = Settings()
    bica, cristo = s.spots["bica_da_cana"], s.spots["cristo_rei"]
    assert terrain_cutoff_deg(bica, 98) == 1.6 and terrain_cutoff_deg(bica, 110) == 0.0
    assert terrain_cutoff_deg(cristo, 250) == 1.5  # zima: nad zálivem
    assert terrain_cutoff_deg(cristo, 268) == 0.0  # mezi sektory: otevřený obzor
    assert terrain_cutoff_deg(cristo, 285) == 7.0  # léto: za svahy nad Funchalem
    assert terrain_cutoff_deg(s.spots["ponta_do_pargo"], 285) == 0.0  # jiné spoty bez stínění


def test_terrain_gate_ramp():
    t = Settings().terrain
    assert terrain_gate(0.0, t) == 1.0 and terrain_gate(1.0, t) == 1.0
    assert 0.85 < terrain_gate(1.6, t) < 0.95  # Bica: slunce jen o pár minut později
    assert terrain_gate(7.0, t) == 0.0  # Cristo Rei v létě: skóre nulováno


def test_dynamic_horizon_points_lie_55_km_along_the_sun_axis():
    s = Settings()
    locs = with_sun_horizons(load_locations(), s, date(2026, 10, 27))
    by = {loc.key: loc for loc in locs}
    origin = by["bica_da_cana"]
    east = by[horizon_key("bica_da_cana")]
    assert east.lon > origin.lon + 0.5 and east.lat < origin.lat  # východ-jihovýchod, ne sever
    west = by[horizon_key("ponta_do_pargo")]
    assert west.lon < by["ponta_do_pargo"].lon - 0.5 and west.lat < by["ponta_do_pargo"].lat
    # Ruivo (sunset) čte svou lokalitu, ne klíč spotu; Arieiro zůstává na pevném bodu
    assert horizon_key("pico_ruivo_sunset") in by and horizon_key("arieiro") not in by
    assert len(locs) == len(load_locations()) + sum(sp.dynamic_horizon for sp in s.spots.values())


def test_missing_dynamic_horizon_falls_back_to_static_point():
    bundle = parse_response(json.loads(FIXTURE.read_text()), load_locations(), "best_match", NOW)
    target = bundle.forecasts["ponta_do_pargo"].daily[1].date
    f, _ = extract_spot_features(bundle, target, Settings(), NOW, "bica_da_cana")
    assert f.east_low_cloud is not None and f.sun_azimuth_deg is not None


def test_terrain_edges_are_soft():
    s = Settings()
    bica = s.spots["bica_da_cana"]
    assert terrain_cutoff_deg(bica, 105.0) == 1.6  # plná hodnota do hrany sektoru
    assert terrain_cutoff_deg(bica, 106.5) == pytest.approx(0.8)  # uprostřed pásma ±3°
    assert terrain_cutoff_deg(bica, 108.0) == 0.0
    assert terrain_cutoff_deg(bica, 104.9) - terrain_cutoff_deg(bica, 105.2) < 0.2  # žádný skok mezi dny
    assert terrain_cutoff_deg(bica, 87.0) == 0.0 and terrain_cutoff_deg(bica, 88.5) == pytest.approx(0.8)
    cristo = s.spots["cristo_rei"]
    assert terrain_cutoff_deg(cristo, 273.5) == pytest.approx(3.5)  # náběh k letnímu sektoru


def test_cristo_rei_is_shaded_in_summer_but_not_in_winter():
    bundle = parse_response(json.loads(FIXTURE.read_text()), load_locations(), "best_match", NOW)
    s = Settings()
    target = bundle.forecasts["cristo_rei"].daily[1].date  # 13. 10.: azimut západu ~262° = mezi sektory
    f = extract_sunset_features(bundle, target, NOW, s, spot_key="cristo_rei", config_key="cristo_rei")
    assert 260 < f.sun_azimuth_deg < 275 and f.terrain_cutoff_deg < 1.0  # ocásek sektoru 240–260° pod prahem
    assert "terrain" not in score_sunset(f, s).gates
    from dataclasses import replace

    winter = replace(f, sun_azimuth_deg=250.0, terrain_cutoff_deg=1.5)
    assert score_sunset(winter, s).gates["terrain"] == 0.9
    summer = replace(f, sun_azimuth_deg=290.0, terrain_cutoff_deg=7.0)
    a = score_sunset(summer, s)
    assert a.score_pct == 0 and a.gates["terrain"] == 0.0
    assert any("terén" in w for w in a.warnings)


def test_bica_sunrise_loses_a_little_to_the_massif_and_arieiro_not_at_all():
    from dataclasses import replace

    from tests.test_analyzers import features

    s = Settings()
    tuning = s.bica_da_cana.tuning()
    base = features(summit_m=1560.0, aloft_hpa=850, z={925: 860.0, 850: 1500.0, 800: 2040.0, 700: 3175.0})
    open_h = score_sunrise(base, s, tuning)
    shaded = score_sunrise(replace(base, sun_azimuth_deg=98.0, terrain_cutoff_deg=1.6), s, tuning)
    assert shaded.gates["terrain"] == 0.88 and shaded.score_pct < open_h.score_pct
    assert "terrain" not in open_h.gates  # Arieiro a otevřený obzor: gates beze změny
