"""Sunny Escape: které pobřežní místo bude mít v poledne/odpoledne nejvíc slunce (a nejméně deště)."""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

from madeira.analyzers.daily_weather import sky_level
from madeira.analyzers.scoring_utils import clamp, pw
from madeira.domain.config import EscapeConfig
from madeira.domain.models import ForecastBundle, HourlyPoint

TZ = ZoneInfo("Atlantic/Madeira")
_SKY_CS = {"clear": "jasno", "partly": "polojasno", "cloudy": "zataženo", "rain": "déšť", None: "bez dat"}


def _vals(points: list[HourlyPoint], attr: str) -> list[float]:
    return [v for p in points if (v := getattr(p, attr)) is not None]


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def score_point(points: list[HourlyPoint], day: date, cfg: EscapeConfig) -> dict | None:
    """Skóre jednoho místa pro `day` v okně cfg.hour_from–cfg.hour_to; None = bez dat."""
    window = [
        p
        for p in points
        if (t := p.time_utc.astimezone(TZ)).date() == day and cfg.hour_from <= t.hour < cfg.hour_to
    ]
    if not window:
        return None
    mix = cfg.cloud_mix
    layers = [
        (w, _mean(_vals(window, f"cloud_cover_{name}_pct")))
        for name, w in (("low", mix.low), ("mid", mix.mid), ("high", mix.high))
    ]
    layers = [(w, v) for w, v in layers if v is not None]
    if layers:
        cloud_pct = sum(w * v for w, v in layers) / sum(w for w, _ in layers)
    else:
        cloud_pct = _mean(_vals(window, "cloud_cover_total_pct"))
    cloud_comp = None if cloud_pct is None else clamp(1 - cloud_pct / 100)

    sun_s = _mean(_vals(window, "sunshine_duration_s"))
    radiation = _mean(_vals(window, "direct_radiation_wm2"))
    if sun_s is not None:
        sun_comp = clamp(sun_s / 3600)
    elif radiation is not None:
        sun_comp = clamp(radiation / cfg.max_radiation_wm2)
    else:
        sun_comp = cloud_comp  # bez slunečních dat se spoléháme na oblačnost

    temp = _mean(_vals(window, "temperature_c"))
    temp_comp = None if temp is None else pw(temp, cfg.temperature_curve)
    precip = sum(_vals(window, "precipitation_mm"))

    w = cfg.weights
    used = [(wt, v) for wt, v in ((w.sunshine, sun_comp), (w.clouds, cloud_comp), (w.temperature, temp_comp)) if v is not None]
    if not used:
        return None
    base = sum(wt * v for wt, v in used) / sum(wt for wt, _ in used)
    rainy = precip >= cfg.rain_min_mm
    factor = max(0.0, cfg.rain_base_factor - cfg.rain_per_mm * precip) if rainy else 1.0
    return {
        "sun_score": round(100 * base * factor),
        "temp_c": None if temp is None else round(temp),
        "precip_mm": round(precip, 1),
        "cloud_pct": None if cloud_pct is None else round(cloud_pct),
        "sunshine_pct": None if sun_comp is None else round(100 * sun_comp),
        "sky": "rain" if rainy and precip >= 1 else sky_level(cloud_pct),
    }


def rank_locations(bundle: ForecastBundle, day: date, cfg: EscapeConfig) -> list[dict]:
    """Všechna místa s daty pro `day`, od nejslunnějšího (při shodě skóre méně srážek)."""
    rows = []
    for point in cfg.points:
        fc = bundle.forecasts.get(point.key)
        scored = score_point(fc.hourly, day, cfg) if fc else None
        if scored:
            rows.append({"key": point.key, "name": point.name, "name_gen": point.name_gen or point.name, **scored, "lat": point.lat, "lon": point.lon})
    return sorted(rows, key=lambda r: (-r["sun_score"], r["precip_mm"]))


def recommendation_cs(day_label: str, best: dict) -> str:
    when = "Dnes" if day_label == "today" else "Zítra"
    temp = "? °C" if best["temp_c"] is None else f"{best['temp_c']} °C"
    return f"{when} jeďte do {best.get('name_gen') or best['name']} – {temp}, {_SKY_CS[best['sky']]}, {best['precip_mm']:g} mm srážek"
