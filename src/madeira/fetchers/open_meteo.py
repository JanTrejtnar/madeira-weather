from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from madeira.domain.models import (
    DailyInfo,
    ForecastBundle,
    HourlyPoint,
    Location,
    LocationForecast,
    PressureLevel,
)

URL = "https://api.open-meteo.com/v1/forecast"
HISTORICAL_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
HISTORICAL_MODEL = "historical_forecast"
LEVELS = (925, 850, 800, 700)  # 800 hPa ≈ 2050 m: těsně nad vrcholem Arieira (1818 m)
SURFACE_VARS = [
    "temperature_2m",
    "apparent_temperature",
    "dew_point_2m",
    "relative_humidity_2m",
    "cloud_cover",
    "cloud_cover_low",
    "cloud_cover_mid",
    "cloud_cover_high",
    "precipitation",
    "precipitation_probability",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "visibility",
    "pressure_msl",
    "direct_radiation",
    "sunshine_duration",
]
LEVEL_VARS = [
    f"{name}_{lvl}hPa"
    for lvl in LEVELS
    for name in ("temperature", "relative_humidity", "geopotential_height")
]
DAILY_VARS = ["sunrise", "sunset"]

_SURFACE_MAP = {
    "temperature_2m": "temperature_c",
    "apparent_temperature": "apparent_temperature_c",
    "dew_point_2m": "dew_point_c",
    "relative_humidity_2m": "relative_humidity_pct",
    "cloud_cover": "cloud_cover_total_pct",
    "cloud_cover_low": "cloud_cover_low_pct",
    "cloud_cover_mid": "cloud_cover_mid_pct",
    "cloud_cover_high": "cloud_cover_high_pct",
    "precipitation": "precipitation_mm",
    "precipitation_probability": "precipitation_probability_pct",
    "wind_speed_10m": "wind_speed_kmh",
    "wind_direction_10m": "wind_direction_deg",
    "wind_gusts_10m": "wind_gusts_kmh",
    "visibility": "visibility_m",
    "pressure_msl": "pressure_msl_hpa",
    "direct_radiation": "direct_radiation_wm2",
    "sunshine_duration": "sunshine_duration_s",
}
_LEVEL_MAP = {
    "temperature": "temperature_c",
    "relative_humidity": "relative_humidity_pct",
    "geopotential_height": "geopotential_height_m",
}


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def parse_response(
    payload: dict | list, locations: list[Location], model: str, fetched_at: datetime
) -> ForecastBundle:
    """Open-Meteo vrací list pro více lokalit, dict pro jednu; pořadí odpovídá requestu."""
    results = payload if isinstance(payload, list) else [payload]
    if len(results) != len(locations):
        raise ValueError(f"Čekal jsem {len(locations)} lokalit, přišlo {len(results)}")
    forecasts: dict[str, LocationForecast] = {}
    for loc, res in zip(locations, results, strict=True):
        hourly = res["hourly"]
        points = []
        for i, t in enumerate(hourly["time"]):
            fields: dict = {"time_utc": _utc(t)}
            for api_name, field in _SURFACE_MAP.items():
                fields[field] = hourly.get(api_name, [None] * len(hourly["time"]))[i]
            levels = {}
            for lvl in LEVELS:
                levels[lvl] = PressureLevel(
                    **{
                        field: hourly.get(f"{name}_{lvl}hPa", [None] * len(hourly["time"]))[i]
                        for name, field in _LEVEL_MAP.items()
                    }
                )
            points.append(HourlyPoint(**fields, levels=levels))
        daily = res["daily"]
        days = [
            DailyInfo(
                date=date.fromisoformat(d),
                sunrise_utc=_utc(sr),
                sunset_utc=_utc(ss),
            )
            for d, sr, ss in zip(daily["time"], daily["sunrise"], daily["sunset"], strict=True)
        ]
        forecasts[loc.key] = LocationForecast(location=loc, hourly=points, daily=days)
    return ForecastBundle(model=model, fetched_at_utc=fetched_at, forecasts=forecasts)


CURRENT_VARS = [
    "temperature_2m",
    "relative_humidity_2m",
    "apparent_temperature",
    "precipitation",
    "weather_code",
    "wind_speed_10m",
    "wind_gusts_10m",
]


def fetch_current(points: list[tuple[float, float, int]], client: httpx.Client | None = None) -> list[dict]:
    """Aktuální počasí pro více bodů (lat, lon, výška m) jedním dotazem; pořadí odpovídá `points`."""
    params = {
        "latitude": ",".join(str(p[0]) for p in points),
        "longitude": ",".join(str(p[1]) for p in points),
        "elevation": ",".join(str(p[2]) for p in points),
        "current": ",".join(CURRENT_VARS),
        "timezone": "UTC",
        "wind_speed_unit": "kmh",
    }
    payload = OpenMeteoFetcher(client=client)._get(params)
    results = payload if isinstance(payload, list) else [payload]
    if len(results) != len(points):
        raise ValueError(f"Čekal jsem {len(points)} bodů, přišlo {len(results)}")
    return [r["current"] for r in results]


MAP_HOURLY_VARS = [
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "precipitation",
    "precipitation_probability",
    "weather_code",
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "cloud_cover",
    "cloud_cover_low",
]


def fetch_hourly(
    points: list[tuple[float, float, int]],
    days: int = 3,
    timezone: str = "Atlantic/Madeira",
    client: httpx.Client | None = None,
) -> list[dict]:
    """Hodinová předpověď pro více bodů (lat, lon, výška m) jedním dotazem; časy v `timezone`."""
    params = {
        "latitude": ",".join(str(p[0]) for p in points),
        "longitude": ",".join(str(p[1]) for p in points),
        "elevation": ",".join(str(p[2]) for p in points),
        "hourly": ",".join(MAP_HOURLY_VARS),
        "timezone": timezone,
        "forecast_days": days,
        "wind_speed_unit": "kmh",
    }
    payload = OpenMeteoFetcher(client=client)._get(params)
    results = payload if isinstance(payload, list) else [payload]
    if len(results) != len(points):
        raise ValueError(f"Čekal jsem {len(points)} bodů, přišlo {len(results)}")
    return [r["hourly"] for r in results]


class OpenMeteoFetcher:
    def __init__(
        self,
        cache_dir: Path | None = None,
        ttl_s: int = 45 * 60,
        model: str = "best_match",
        client: httpx.Client | None = None,
    ):
        self.cache_dir = cache_dir
        self.ttl_s = ttl_s
        self.model = model
        self._client = client

    def _params(self, locations: list[Location], days: int) -> dict:
        params = {
            "latitude": ",".join(str(loc.lat) for loc in locations),
            "longitude": ",".join(str(loc.lon) for loc in locations),
            "elevation": ",".join(str(loc.elevation_m) for loc in locations),
            "hourly": ",".join(SURFACE_VARS + LEVEL_VARS),
            "daily": ",".join(DAILY_VARS),
            "timezone": "UTC",
            "forecast_days": days,
            "wind_speed_unit": "kmh",
        }
        if self.model != "best_match":
            params["models"] = self.model
        return params

    def _cache_path(self, params: dict) -> Path | None:
        if self.cache_dir is None:
            return None
        digest = hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()[:16]
        return self.cache_dir / f"open_meteo_{digest}.json"

    def fetch(self, locations: list[Location], days: int = 7, past_days: int = 0) -> ForecastBundle:
        params = self._params(locations, days)
        if past_days:  # modelové hodnoty za uplynulé dny (pro historii)
            params["past_days"] = past_days
        return self._fetch(params, URL, self.ttl_s, locations, self.model)

    def fetch_historical(self, locations: list[Location], start: date, end: date) -> ForecastBundle:
        """Archivované předpovědi modelů (Historical Forecast API, od r. 2022) pro zpětné dopočty."""
        params = self._params(locations, 0)
        del params["forecast_days"]
        params["start_date"], params["end_date"] = start.isoformat(), end.isoformat()
        # historická data se nemění → cache na 30 dní
        return self._fetch(params, HISTORICAL_URL, 30 * 24 * 3600, locations, HISTORICAL_MODEL)

    def _fetch(
        self, params: dict, url: str, ttl_s: int, locations: list[Location], model: str
    ) -> ForecastBundle:
        cache = self._cache_path({**params, "_url": url})
        if cache and cache.exists() and time.time() - cache.stat().st_mtime < ttl_s:
            payload = json.loads(cache.read_text(encoding="utf-8"))
            fetched = datetime.fromtimestamp(cache.stat().st_mtime, tz=UTC)
        else:
            payload = self._get(params, url)
            fetched = datetime.now(UTC)
            if cache:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(payload), encoding="utf-8")
        return parse_response(payload, locations, model, fetched)

    def _get(self, params: dict, url: str = URL) -> dict | list:
        client = self._client or httpx.Client(timeout=30)
        last: Exception | None = None
        for attempt in range(3):
            try:
                resp = client.get(url, params=params)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise httpx.HTTPStatusError("retry", request=resp.request, response=resp)
                if resp.status_code >= 400:  # špatný dotaz (např. datum mimo rozsah) se neopakuje
                    reason = resp.json().get("reason", resp.text) if resp.content else resp.text
                    raise RuntimeError(f"Open-Meteo odmítlo dotaz: {reason}")
                return resp.json()
            except httpx.HTTPError as exc:
                last = exc
                time.sleep(2**attempt)
        raise RuntimeError(f"Open-Meteo nedostupné: {last}")
