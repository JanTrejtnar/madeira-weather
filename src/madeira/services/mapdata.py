"""Export hodinové předpovědi pro interaktivní mapu (docs/map.html → docs/map_data.json)."""

from __future__ import annotations

import json
import statistics
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from madeira.domain.config import MapSpot, load_map_spots
from madeira.fetchers.open_meteo import fetch_hourly

TZ = "Atlantic/Madeira"
HOURS = 49  # aktuální hodina + 48 h
CONDITIONS = ("sunny", "cloudy", "rain", "fog", "above_clouds")

RAIN_CODES = {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99}
FOG_CODES = {45, 48}
CLOUDY_FROM_PCT = 60  # celková oblačnost, od které je bod "cloudy"
PEAK_MIN_M = 1400  # od této výšky může být bod nad mraky (body ~1000 m jsou v zóně vaření mraků)
LOWLAND_MAX_M = 400  # "pod mraky" měříme na nížinných bodech
DECK_LOW_CLOUD_PCT = 70  # medián nízké oblačnosti nížin, od kterého existuje mořské mraky
ABOVE_MAX_RH = 65  # vlhčí vrchol je v cárech/mlze, ne nad mraky (suchá inverze: 20–50 %)
ABOVE_MAX_POP = 15  # pravděpodobnost srážek (%) musí být pod touto hranicí

_FIELDS = {  # klíč v JSONu → proměnná Open-Meteo
    "temp": "temperature_2m",
    "feels": "apparent_temperature",
    "rh": "relative_humidity_2m",
    "precip": "precipitation",
    "pop": "precipitation_probability",
    "code": "weather_code",
    "wind": "wind_speed_10m",
    "dir": "wind_direction_10m",
    "gust": "wind_gusts_10m",
    "cloud": "cloud_cover",
}


def condition_type(code, cloud, rh, precip, pop, altitude_m: int, deck: bool) -> str:
    """Zjednodušený stav bodu: rain > fog > above_clouds > cloudy > sunny."""
    code = int(code) if code is not None else None
    if code in RAIN_CODES or (precip or 0) >= 0.2 or ((pop or 0) >= 70 and (precip or 0) > 0):
        return "rain"
    if code in FOG_CODES:
        return "fog"
    if (
        altitude_m >= PEAK_MIN_M
        and deck
        and rh is not None
        and rh <= ABOVE_MAX_RH
        and (pop or 0) < ABOVE_MAX_POP
        and not precip
    ):
        return "above_clouds"
    if (cloud or 0) >= CLOUDY_FROM_PCT or code == 3:
        return "cloudy"
    return "sunny"


def _start_index(times: list[str], now: datetime) -> int:
    """Index první hodiny, která není před `now` (místní čas Madeiry)."""
    key = now.astimezone(ZoneInfo(TZ)).strftime("%Y-%m-%dT%H:00")
    return next((i for i, t in enumerate(times) if t >= key), 0)


def _num(value, digits: int = 0):
    if value is None:
        return None
    return round(value, digits) if digits else round(value)


def build_map_payload(spots: list[MapSpot], raws: list[dict], now: datetime) -> dict:
    """Složí kompaktní sloupcový JSON: časová osa + pro každý bod pole hodnot po hodinách."""
    if len(spots) != len(raws):
        raise ValueError("Počet bodů a odpovědí se liší")
    times = raws[0]["time"]
    start = _start_index(times, now)
    window = slice(start, start + HOURS)
    stamps = times[window]

    low_clouds = [
        [v or 0 for v in raw["cloud_cover_low"][window]]
        for spot, raw in zip(spots, raws, strict=True)
        if spot.altitude_m <= LOWLAND_MAX_M
    ]
    deck = [statistics.median(col) >= DECK_LOW_CLOUD_PCT for col in zip(*low_clouds, strict=True)]
    deck = deck or [False] * len(stamps)

    out_spots = []
    for spot, raw in zip(spots, raws, strict=True):
        cols = {key: raw[var][window] for key, var in _FIELDS.items()}
        cond = [
            condition_type(cols["code"][i], cols["cloud"][i], cols["rh"][i], cols["precip"][i],
                           cols["pop"][i], spot.altitude_m, deck[i])
            for i in range(len(stamps))
        ]
        out_spots.append({
            "id": spot.id,
            "name": spot.name,
            "lat": spot.lat,
            "lon": spot.lon,
            "altitude_m": spot.altitude_m,
            "region": spot.region,
            "webcam": spot.webcam,
            "temp": [_num(v, 1) for v in cols["temp"]],
            "feels": [_num(v, 1) for v in cols["feels"]],
            "rh": [_num(v) for v in cols["rh"]],
            "precip": [_num(v, 1) for v in cols["precip"]],
            "pop": [_num(v) for v in cols["pop"]],
            "code": [_num(v) for v in cols["code"]],
            "wind": [_num(v) for v in cols["wind"]],
            "dir": [_num(v) for v in cols["dir"]],
            "gust": [_num(v) for v in cols["gust"]],
            "cloud": [_num(v) for v in cols["cloud"]],
            "condition_type": cond,
        })
    return {
        "generated_at": now.astimezone(ZoneInfo(TZ)).isoformat(timespec="seconds"),
        "timezone": TZ,
        "timestamps": stamps,
        "spots": out_spots,
    }


def build_map_data(now: datetime | None = None, spots: list[MapSpot] | None = None) -> dict:
    spots = spots if spots is not None else load_map_spots()
    now = now or datetime.now(ZoneInfo(TZ))
    raws = fetch_hourly([(s.lat, s.lon, s.altitude_m) for s in spots])
    return build_map_payload(spots, raws, now)


def write_map_data(payload: dict, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
