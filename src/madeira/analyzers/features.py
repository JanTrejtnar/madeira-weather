from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import mean

from madeira.domain.config import SunriseWindow
from madeira.domain.models import ForecastBundle, HourlyPoint

Getter = Callable[[HourlyPoint], float | None]
SUMMIT_M = 1818.0


def interp(points: list[HourlyPoint], getter: Getter, t: datetime) -> float | None:
    """Lineární interpolace hodinových dat v čase t (mimo rozsah drží krajní hodnotu)."""
    if not points:
        return None
    if t <= points[0].time_utc:
        return getter(points[0])
    for a, b in zip(points, points[1:]):
        if t <= b.time_utc:
            va, vb = getter(a), getter(b)
            if va is None or vb is None:
                return va if vb is None else vb
            span = (b.time_utc - a.time_utc).total_seconds()
            w = (t - a.time_utc).total_seconds() / span
            return va + (vb - va) * w
    return getter(points[-1])


def window_values(
    points: list[HourlyPoint],
    getter: Getter,
    start: datetime,
    end: datetime,
    step_min: int = 15,
) -> list[float]:
    out = []
    t = start
    while t <= end:
        v = interp(points, getter, t)
        if v is not None:
            out.append(v)
        t += timedelta(minutes=step_min)
    return out


def _avg(vals: list[float]) -> float | None:
    return mean(vals) if vals else None


def _max(vals: list[float]) -> float | None:
    return max(vals) if vals else None


@dataclass
class SunriseFeatures:
    target_date: date
    sunrise_utc: datetime
    days_ahead: int
    low_cloud_summit: float | None
    mid_cloud: float | None
    high_cloud: float | None
    east_low_cloud: float | None  # nízká oblačnost na východním obzoru (blokuje jen nad vrcholem)
    east_mid_cloud: float | None  # střední oblačnost tamtéž (propouští část světla)
    lowland_low_cloud: float | None
    precip_mm: float
    precip_prob: float | None
    gust_kmh: float | None
    wind_kmh: float | None
    wind_dir_deg: float | None
    visibility_km: float | None
    dewpoint_depression_c: float | None
    temp_c: float | None
    apparent_temp_c: float | None
    t: dict[int, float | None]
    rh: dict[int, float | None]
    z: dict[int, float | None]


def extract_sunrise_features(
    bundle: ForecastBundle,
    target_date: date,
    now_utc: datetime,
    window: SunriseWindow,
    summit_key: str = "arieiro",
    east_key: str = "east_horizon",
    lowland_keys: tuple[str, ...] = ("sao_vicente", "funchal"),
) -> SunriseFeatures:
    summit = bundle.forecasts[summit_key]
    day = next((d for d in summit.daily if d.date == target_date), None)
    if day is None:
        raise ValueError(f"Pro {target_date} nemám data (předpověď pokrývá jen několik dní).")
    sunrise = day.sunrise_utc
    start = sunrise - timedelta(minutes=window.before_min)
    end = sunrise + timedelta(minutes=window.after_min)
    pts = summit.hourly

    def avg(getter: Getter) -> float | None:
        return _avg(window_values(pts, getter, start, end))

    # srážky: součet hodinových hodnot v okně ±90 min kolem východu
    precip_vals = [
        p.precipitation_mm or 0.0
        for p in pts
        if sunrise - timedelta(minutes=90) <= p.time_utc <= sunrise + timedelta(minutes=90)
    ]
    prob_vals = [
        p.precipitation_probability_pct
        for p in pts
        if sunrise - timedelta(minutes=90) <= p.time_utc <= sunrise + timedelta(minutes=90)
        and p.precipitation_probability_pct is not None
    ]

    east_low = east_mid = None
    if east_key in bundle.forecasts:
        epts = bundle.forecasts[east_key].hourly
        east_low = _avg(window_values(epts, lambda p: p.cloud_cover_low_pct, start, end))
        east_mid = _avg(window_values(epts, lambda p: p.cloud_cover_mid_pct, start, end))

    lowland = []
    for key in lowland_keys:
        if key in bundle.forecasts:
            v = _avg(
                window_values(
                    bundle.forecasts[key].hourly, lambda p: p.cloud_cover_low_pct, start, end
                )
            )
            if v is not None:
                lowland.append(v)

    temp = avg(lambda p: p.temperature_c)
    dew = avg(lambda p: p.dew_point_c)
    vis = avg(lambda p: p.visibility_m)

    def lvl(level: int, attr: str) -> float | None:
        return avg(lambda p: getattr(p.levels[level], attr) if level in p.levels else None)

    levels = (925, 850, 800, 700)
    return SunriseFeatures(
        target_date=target_date,
        sunrise_utc=sunrise,
        days_ahead=(target_date - now_utc.date()).days,
        low_cloud_summit=_max(window_values(pts, lambda p: p.cloud_cover_low_pct, start, end)),
        mid_cloud=avg(lambda p: p.cloud_cover_mid_pct),
        high_cloud=avg(lambda p: p.cloud_cover_high_pct),
        east_low_cloud=east_low,
        east_mid_cloud=east_mid,
        lowland_low_cloud=_avg(lowland),
        precip_mm=sum(precip_vals),
        precip_prob=max(prob_vals) if prob_vals else None,
        gust_kmh=_max(window_values(pts, lambda p: p.wind_gusts_kmh, start, end)),
        wind_kmh=avg(lambda p: p.wind_speed_kmh),
        wind_dir_deg=avg(lambda p: p.wind_direction_deg),
        visibility_km=None if vis is None else vis / 1000,
        dewpoint_depression_c=None if temp is None or dew is None else temp - dew,
        temp_c=temp,
        apparent_temp_c=avg(lambda p: p.apparent_temperature_c),
        t={lv: lvl(lv, "temperature_c") for lv in levels},
        rh={lv: lvl(lv, "relative_humidity_pct") for lv in levels},
        z={lv: lvl(lv, "geopotential_height_m") for lv in levels},
    )
