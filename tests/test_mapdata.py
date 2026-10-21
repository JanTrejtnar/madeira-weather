import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from madeira.domain.config import MapSpot, load_map_spots, load_webcams
from madeira.services import mapdata

TZ = ZoneInfo("Atlantic/Madeira")
NOW = datetime(2026, 10, 21, 14, 20, tzinfo=TZ)
TIMES = [f"2026-10-{21 + h // 24}T{h % 24:02d}:00" for h in range(72)]
DOCS = Path(__file__).resolve().parents[1] / "docs" / "map_data.json"


def _raw(temp=20.0, code=0, cloud=10, low=0, rh=60, precip=0.0, pop=0):
    n = len(TIMES)
    return {
        "time": TIMES,
        "temperature_2m": [temp] * n,
        "apparent_temperature": [temp - 1] * n,
        "relative_humidity_2m": [rh] * n,
        "precipitation": [precip] * n,
        "precipitation_probability": [pop] * n,
        "weather_code": [code] * n,
        "wind_speed_10m": [12.4] * n,
        "wind_direction_10m": [270] * n,
        "wind_gusts_10m": [30.2] * n,
        "cloud_cover": [cloud] * n,
        "cloud_cover_low": [low] * n,
    }


def _spots(*specs):
    return [MapSpot(id=i, name=i, lat=32.7, lon=-17.0, altitude_m=alt, region=reg) for i, alt, reg in specs]


def test_config_spots_cover_all_regions_and_are_valid():
    spots = load_map_spots()
    assert 15 <= len(spots) <= 20
    assert len({s.id for s in spots}) == len(spots)
    assert {s.region for s in spots} == {"peaks", "north", "south", "west", "east"}
    cams = {c.id for c in load_webcams()}
    assert all(s.webcam in cams for s in spots if s.webcam)
    assert all(32.5 < s.lat < 33.0 and -17.4 < s.lon < -16.6 for s in spots)


def test_payload_starts_at_current_local_hour_and_spans_48h():
    spots = _spots(("a", 10, "south"), ("b", 1800, "peaks"))
    p = mapdata.build_map_payload(spots, [_raw(), _raw()], NOW)
    assert p["timezone"] == "Atlantic/Madeira"
    assert p["timestamps"][0] == "2026-10-21T14:00"
    assert len(p["timestamps"]) == mapdata.HOURS
    assert p["timestamps"][1] == "2026-10-21T15:00"
    assert p["generated_at"].startswith("2026-10-21T14:20:00")


def test_payload_converts_now_from_utc_to_madeira():
    spots = _spots(("a", 10, "south"))
    now = datetime(2026, 10, 21, 14, 20, tzinfo=ZoneInfo("UTC"))  # WEST = UTC+1 → 15:20
    p = mapdata.build_map_payload(spots, [_raw()], now)
    assert p["timestamps"][0] == "2026-10-21T15:00"


def test_spot_columns_align_with_timestamps():
    spots = _spots(("a", 10, "south"))
    p = mapdata.build_map_payload(spots, [_raw(temp=21.46)], NOW)
    s = p["spots"][0]
    n = len(p["timestamps"])
    for key in ("temp", "feels", "rh", "precip", "pop", "code", "wind", "dir", "gust", "cloud", "condition_type"):
        assert len(s[key]) == n, key
    assert s["temp"][0] == 21.5
    assert s["wind"][0] == 12 and s["gust"][0] == 30
    assert {"id", "name", "lat", "lon", "altitude_m", "region", "webcam"} <= set(s)


@pytest.mark.parametrize(
    ("kwargs", "alt", "deck", "expected"),
    [
        ({}, 10, False, "sunny"),
        ({"cloud": 80, "code": 3}, 10, False, "cloudy"),
        ({"code": 63, "cloud": 100}, 10, False, "rain"),
        ({"precip": 0.6}, 10, False, "rain"),
        ({"code": 45, "cloud": 100, "rh": 100}, 1100, False, "fog"),
        ({"code": 0, "cloud": 5, "rh": 50}, 1800, True, "above_clouds"),
        ({"code": 3, "cloud": 100, "rh": 98}, 1800, True, "cloudy"),  # vrchol v mraku, ne nad nimi
        ({"code": 0, "cloud": 5, "rh": 80}, 1800, True, "sunny"),  # RH 80 % → cárý, ne nad mraky
        ({"code": 3, "cloud": 90, "rh": 80}, 1800, True, "cloudy"),
        ({"code": 0, "cloud": 5, "rh": 66}, 1800, True, "sunny"),
        ({"code": 0, "cloud": 5, "rh": 65}, 1800, True, "above_clouds"),
        ({"code": 0, "cloud": 5, "rh": 50, "pop": 15}, 1800, True, "sunny"),
        ({"code": 0, "cloud": 5, "rh": 50, "pop": 14}, 1800, True, "above_clouds"),
        ({"code": 0, "cloud": 5, "rh": 50}, 1000, True, "sunny"),  # 1000 m už není dost vysoko
        ({"code": 0, "cloud": 5, "rh": 50}, 1399, True, "sunny"),
        ({"code": 0, "cloud": 5}, 10, True, "sunny"),  # nížina nikdy "above_clouds"
    ],
)
def test_condition_type(kwargs, alt, deck, expected):
    r = _raw(**kwargs)
    got = mapdata.condition_type(r["weather_code"][0], r["cloud_cover"][0], r["relative_humidity_2m"][0],
                                 r["precipitation"][0], r["precipitation_probability"][0], alt, deck)
    assert got == expected


def test_peak_above_clouds_when_lowlands_under_cloud_deck():
    spots = _spots(("coast", 10, "south"), ("coast2", 20, "north"), ("peak", 1800, "peaks"))
    raws = [_raw(code=3, cloud=100, low=90), _raw(code=3, cloud=100, low=90), _raw(code=0, cloud=5, rh=40)]
    p = mapdata.build_map_payload(spots, raws, NOW)
    assert set(p["spots"][2]["condition_type"]) == {"above_clouds"}
    assert set(p["spots"][0]["condition_type"]) == {"cloudy"}


def test_mismatched_responses_rejected():
    with pytest.raises(ValueError):
        mapdata.build_map_payload(_spots(("a", 10, "south")), [], NOW)


def test_write_map_data_roundtrip(tmp_path):
    p = mapdata.build_map_payload(_spots(("a", 10, "south")), [_raw()], NOW)
    out = tmp_path / "docs" / "map_data.json"
    mapdata.write_map_data(p, out)
    assert json.loads(out.read_text(encoding="utf-8")) == p


def test_fetch_hourly_batches_all_points_in_one_request():
    import httpx

    from madeira.fetchers.open_meteo import MAP_HOURLY_VARS, fetch_hourly

    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(request.url.params)
        n = len(request.url.params["latitude"].split(","))
        return httpx.Response(200, json=[{"hourly": _raw()} for _ in range(n)])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    out = fetch_hourly([(32.7, -17.0, 10), (32.8, -16.9, 1800)], client=client)
    assert len(out) == 2
    assert seen["timezone"] == "Atlantic/Madeira"
    assert seen["hourly"] == ",".join(MAP_HOURLY_VARS)
    assert seen["elevation"] == "10,1800"
    for var in ("temperature_2m", "relative_humidity_2m", "precipitation_probability", "weather_code",
                "wind_speed_10m", "wind_direction_10m", "cloud_cover"):
        assert var in MAP_HOURLY_VARS


@pytest.mark.skipif(not DOCS.exists(), reason="docs/map_data.json ještě nebyl vyexportován")
def test_published_map_data_is_valid():
    data = json.loads(DOCS.read_text(encoding="utf-8"))
    stamps = data["timestamps"]
    assert data["timezone"] == "Atlantic/Madeira"
    assert 24 <= len(stamps) <= 49
    assert stamps == sorted(stamps)
    assert {s["id"] for s in data["spots"]} == {s.id for s in load_map_spots()}
    for s in data["spots"]:
        for key in ("temp", "code", "wind", "gust", "condition_type"):
            assert len(s[key]) == len(stamps), (s["id"], key)
        assert set(s["condition_type"]) <= set(mapdata.CONDITIONS)
