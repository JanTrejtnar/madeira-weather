"""Jednoduchá denní předpověď (déšť, obloha, teplota, vítr) pro web. Neovlivňuje skóre východu."""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

from madeira.domain.models import ForecastBundle, HourlyPoint

TZ = ZoneInfo("Atlantic/Madeira")
DAY_FROM, DAY_TO = 7, 20  # "den" = 7:00–20:00 místního času
PLACES = ("funchal", "arieiro")


def rain_level(total_mm: float, peak_mm_h: float) -> str:
    """none | drizzle | light | moderate | heavy (lidské kategorie podle úhrnu a špičky)."""
    if total_mm < 0.2:
        return "none"
    if peak_mm_h >= 4 or total_mm >= 10:
        return "heavy"
    if peak_mm_h >= 1.5 or total_mm >= 3:
        return "moderate"
    if total_mm >= 1:
        return "light"
    return "drizzle"


def sky_level(cloud_pct: float | None) -> str | None:
    if cloud_pct is None:
        return None
    return "clear" if cloud_pct < 25 else "partly" if cloud_pct < 60 else "cloudy"


def _vals(points: list[HourlyPoint], getter) -> list[float]:
    return [v for p in points if (v := getter(p)) is not None]


def place_weather(points: list[HourlyPoint], day: date) -> dict | None:
    pts = [
        p
        for p in points
        if (t := p.time_utc.astimezone(TZ)).date() == day and DAY_FROM <= t.hour <= DAY_TO
    ]
    if not pts:
        return None
    precip = _vals(pts, lambda p: p.precipitation_mm)
    prob = _vals(pts, lambda p: p.precipitation_probability_pct)
    cloud = _vals(pts, lambda p: p.cloud_cover_total_pct)
    temp = _vals(pts, lambda p: p.temperature_c)
    gust = _vals(pts, lambda p: p.wind_gusts_kmh)
    total, peak = sum(precip), max(precip, default=0.0)
    rainy = [p for p in pts if (p.precipitation_mm or 0) >= 0.2]
    first = rainy[0].time_utc.astimezone(TZ).hour if rainy else None
    last = rainy[-1].time_utc.astimezone(TZ).hour + 1 if rainy else None
    return {
        "rain": rain_level(total, peak),
        "rain_mm": round(total, 1),
        "rain_prob_pct": round(max(prob)) if prob else None,
        "rain_from": first,
        "rain_to": last,
        "sky": sky_level(sum(cloud) / len(cloud)) if cloud else None,
        "temp_min_c": round(min(temp)) if temp else None,
        "temp_max_c": round(max(temp)) if temp else None,
        "gust_max_kmh": round(max(gust)) if gust else None,
    }


def day_weather(bundle: ForecastBundle, day: date, places: tuple[str, ...] = PLACES) -> dict:
    out = {}
    for key in places:
        fc = bundle.forecasts.get(key)
        w = place_weather(fc.hourly, day) if fc else None
        if w:
            out[key] = w
    return out
