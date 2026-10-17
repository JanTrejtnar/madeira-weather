"""Fanal Fog & Mystery Predictor: heuristika "fog_seeker", obrácená oproti horám.

Hledáme hustou mlhu / nízký mrak v lese (1150 m), ale ne vichr a liják:
skóre = vážený součet (vlhkost, základna mraků, dohlednost) × brána srážek × brána nárazů.
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
    humidity_pct: float | None  # průměr RH u povrchu a ve výšce lesa
    cloud_base_m: float | None  # základna mraků [m n. m.] odhadnutá z T − rosný bod
    visibility_m: float | None
    precip_mm_h: float  # srážky za hodinu [mm/h]
    gust_kmh: float | None
    cloud_cover_pct: float | None  # lokální (nízká) oblačnost


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


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
    base = None
    if p.temperature_c is not None and p.dew_point_c is not None:
        base = cfg.elevation_m + cfg.cloud_base_m_per_c * max(0.0, p.temperature_c - p.dew_point_c)
    cover = p.cloud_cover_low_pct if p.cloud_cover_low_pct is not None else p.cloud_cover_total_pct
    return FogFeatures(
        humidity_pct=_mean(rh),
        cloud_base_m=base,
        visibility_m=p.visibility_m,
        precip_mm_h=p.precipitation_mm or 0.0,
        gust_kmh=p.wind_gusts_kmh,
        cloud_cover_pct=cover,
    )


def score_fog(f: FogFeatures, cfg: FanalConfig) -> float:
    """Fog / Mystery Score 0–100 %. Chybějící faktor se z váženého součtu vyřadí (váhy se přepočtou)."""
    c, w = cfg.curves, cfg.weights
    parts = [
        (w.humidity, None if f.humidity_pct is None else pw(f.humidity_pct, c.humidity)),
        (w.cloud_base, None if f.cloud_base_m is None else pw(f.cloud_base_m, c.cloud_base)),
        (w.visibility, None if f.visibility_m is None else pw(f.visibility_m, c.visibility)),
    ]
    used = [(wt, v) for wt, v in parts if v is not None]
    if not used:
        return 0.0
    base = sum(wt * v for wt, v in used) / sum(wt for wt, _ in used)
    rain_gate = pw(f.precip_mm_h, c.precip)
    wind_gate = 1.0 if f.gust_kmh is None else pw(f.gust_kmh, c.gusts)
    return round(100 * base * rain_gate * wind_gate, 1)


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
        rows.append({"hour": t.hour, "score": round(score), "verdict": verdict(score, cfg), "f": f})
    return sorted(rows, key=lambda r: r["hour"])


def best_window(points: list[HourlyPoint], day: date, cfg: FanalConfig, from_hour: int = 0) -> tuple[int, int] | None:
    """Nejdelší souvislý úsek hodin se skóre ≥ "maybe" (při shodě vyšší součet) jako (od, do) kde do je konec hodiny."""
    hourly = {}
    for p in points:
        t = _local(p)
        if t.date() == day and max(cfg.window_from, from_hour) <= t.hour <= cfg.window_to:
            hourly[t.hour] = score_fog(extract_fog_features(p, cfg), cfg)
    best: tuple[tuple[int, float], tuple[int, int]] | None = None
    run: list[int] = []
    for h in [*sorted(hourly), None]:
        if h is not None and hourly[h] >= cfg.verdict.maybe and (not run or h == run[-1] + 1):
            run.append(h)
            continue
        if run:
            key = (len(run), sum(hourly[x] for x in run))
            if best is None or key > best[0]:
                best = (key, (run[0], run[-1] + 1))
        run = [h] if h is not None and hourly[h] >= cfg.verdict.maybe else []
    return best[1] if best else None
