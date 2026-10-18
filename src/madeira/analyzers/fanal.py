"""Fanal Fog & Mystery Predictor: heuristika "fog_seeker", obrácená oproti horám.

Hledáme hustou mlhu / nízký mrak v lese (1150 m). Globální modely orografickou mlhu z pasátu přes severní
útes nevidí v základně mraků, proto je primární metrika rosný bod (T − Td) a směr větru:
body = rosný bod (max 50) + návětrný směr (+25 / −30) + dohlednost (max 25), pak × brána srážek × brána nárazů.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from madeira.analyzers.scoring_utils import pw
from madeira.domain.config import FanalConfig
from madeira.domain.models import HourlyPoint

TZ = ZoneInfo("Atlantic/Madeira")


@dataclass(frozen=True)
class FogFeatures:
    humidity_pct: float | None  # průměr RH u povrchu a ve výšce lesa (informativní)
    cloud_base_m: float | None  # odhad základny mraků [m n. m.]; jen informativní, do skóre nevstupuje
    visibility_m: float | None
    precip_mm_h: float  # srážky za hodinu [mm/h]
    gust_kmh: float | None
    cloud_cover_pct: float | None  # lokální (nízká) oblačnost
    spread_c: float | None = None  # T − Td u povrchu [°C]
    surface_rh_pct: float | None = None  # relative_humidity_2m
    wind_kmh: float | None = None
    wind_dir_deg: float | None = None
    temp_c: float | None = None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def in_sector(deg: float, start: float, end: float) -> bool:
    """Je směr v sektoru od `start` po `end` ve směru hodinových ručiček (sektor může přecházet přes 0°)?"""
    deg, start, end = deg % 360, start % 360, end % 360
    return start <= deg <= end if start <= end else deg >= start or deg <= end


def rh_at_height(p: HourlyPoint, height_m: float) -> float | None:
    """RH ve výšce `height_m` lineárně z hladin 925/850 hPa (podle geopotenciálu); None bez dat."""
    pts = sorted(
        (lv.geopotential_height_m, lv.relative_humidity_pct)
        for lv in p.levels.values()
        if lv.geopotential_height_m is not None and lv.relative_humidity_pct is not None
    )
    if not pts:
        return None
    if height_m <= pts[0][0]:
        return pts[0][1]
    for (z0, r0), (z1, r1) in zip(pts, pts[1:]):
        if height_m <= z1:
            return r0 + (r1 - r0) * (height_m - z0) / (z1 - z0)
    return pts[-1][1]


def extract_fog_features(p: HourlyPoint, cfg: FanalConfig) -> FogFeatures:
    rh = [v for v in (p.relative_humidity_pct, rh_at_height(p, cfg.elevation_m)) if v is not None]
    spread = base = None
    if p.temperature_c is not None and p.dew_point_c is not None:
        spread = max(0.0, p.temperature_c - p.dew_point_c)
        base = cfg.elevation_m + cfg.cloud_base_m_per_c * spread
    cover = p.cloud_cover_low_pct if p.cloud_cover_low_pct is not None else p.cloud_cover_total_pct
    return FogFeatures(
        humidity_pct=_mean(rh),
        cloud_base_m=base,
        visibility_m=p.visibility_m,
        precip_mm_h=p.precipitation_mm or 0.0,
        gust_kmh=p.wind_gusts_kmh,
        cloud_cover_pct=cover,
        spread_c=spread,
        surface_rh_pct=p.relative_humidity_pct,
        wind_kmh=p.wind_speed_kmh,
        wind_dir_deg=p.wind_direction_deg,
        temp_c=p.temperature_c,
    )


def dew_points(f: FogFeatures, cfg: FanalConfig) -> float:
    """Body za vlhkost: lepší z rosného bodu (spread) a povrchové RH; mezi likely a dry lineárně klesá k 0."""
    d = cfg.dew
    by_spread = 0.0
    if f.spread_c is not None:
        if f.spread_c <= d.spread_extreme:
            by_spread = d.points_extreme
        elif f.spread_c <= d.spread_likely:
            by_spread = d.points_likely
        elif f.spread_c < d.spread_dry:
            by_spread = d.points_likely * (d.spread_dry - f.spread_c) / (d.spread_dry - d.spread_likely)
    by_rh = 0.0
    if f.surface_rh_pct is not None:
        by_rh = d.points_extreme if f.surface_rh_pct >= d.rh_extreme else d.points_likely if f.surface_rh_pct >= d.rh_likely else 0.0
    return max(by_spread, by_rh)


def wind_points(f: FogFeatures, cfg: FanalConfig, moist: bool) -> float:
    """Orografický bonus (pasát z N/NE, 8–30 km/h, jen ve vlhku) a föhnová penalizace (jih)."""
    w = cfg.wind
    if f.wind_dir_deg is None:
        return 0.0
    if in_sector(f.wind_dir_deg, w.lee_from, w.lee_to):
        return -w.lee_penalty
    if moist and f.wind_kmh is not None and w.speed_min <= f.wind_kmh <= w.speed_max             and in_sector(f.wind_dir_deg, w.windward_from, w.windward_to):
        return w.bonus
    return 0.0


def fog_points(f: FogFeatures, cfg: FanalConfig) -> dict[str, float]:
    """Složky skóre (body před branami) a obě brány; `score` = výsledek 0–100."""
    dew = dew_points(f, cfg)
    wind = wind_points(f, cfg, moist=dew > 0)
    vis = 0.0 if f.visibility_m is None else cfg.visibility_points * pw(f.visibility_m, cfg.curves.visibility)
    rain_gate = pw(f.precip_mm_h, cfg.curves.precip)
    wind_gate = 1.0 if f.gust_kmh is None else pw(f.gust_kmh, cfg.curves.gusts)
    raw = max(0.0, min(100.0, dew + wind + vis))
    return {"dew": dew, "wind": wind, "visibility": vis, "rain_gate": rain_gate, "gust_gate": wind_gate,
            "score": round(raw * rain_gate * wind_gate, 1)}


def score_fog(f: FogFeatures, cfg: FanalConfig) -> float:
    """Fog / Mystery Score 0–100 %."""
    return fog_points(f, cfg)["score"]


def verdict(score: float, cfg: FanalConfig) -> str:
    return "MYSTIC" if score >= cfg.verdict.go else "PARTIAL" if score >= cfg.verdict.maybe else "CLEAR"


def _local(p: HourlyPoint) -> datetime:
    return p.time_utc.astimezone(TZ)


def slot_rows(points: list[HourlyPoint], day: date, cfg: FanalConfig, from_hour: int = 0) -> list[dict]:
    """Sloty (cfg.slot_hours) pro `day`; `from_hour` vynechá už proběhlé hodiny (dnešek)."""
    rows = []
    for p in points:
        t = _local(p)
        if t.date() != day or t.hour not in cfg.slot_hours or t.hour < from_hour:
            continue
        f = extract_fog_features(p, cfg)
        score = score_fog(f, cfg)
        rows.append({"hour": t.hour, "score": round(score), "raw": score, "verdict": verdict(score, cfg), "f": f})
    return sorted(rows, key=lambda r: r["hour"])
