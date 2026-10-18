"""Západ slunce na pobřežní vyhlídce (Farol da Ponta do Pargo, západní cíp ostrova)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from madeira.analyzers.features import _avg, _max, window_values
from madeira.analyzers.inversion import summit_clear_factor
from madeira.analyzers.scoring_utils import pw
from madeira.analyzers.sunrise import DEWPOINT_DEP, NEUTRAL, _confidence
from madeira.domain.config import Settings
from madeira.domain.models import ForecastBundle, ScoreComponent, SunsetAnalysis

WIND_GUST = [(25, 1.0), (45, 0.5), (70, 0.0)]  # na útesu nad mořem je nad 45 km/h nepříjemně
LOCAL_LOW = [(10, 1.0), (30, 0.7), (70, 0.0)]  # mlha/mrak přímo na vyhlídce (312 m)
HORIZON_GATE_SPAN = 30  # o kolik % nad prahem brána klesne na minimum
HORIZON_GATE_MIN = 0.4
VISIBILITY_GATE_MIN = 0.3

_NAMES_CS = {
    "horizon_clarity": "čistý obzor",
    "cloud_canvas": "barvy oblohy",
    "low_cloud_penalty": "mlha na vyhlídce",
    "wind_comfort": "vítr",
}


@dataclass
class SunsetFeatures:
    target_date: date
    sunset_utc: datetime
    days_ahead: int
    horizon_low_cloud: float | None  # nízká oblačnost nad mořem na západě (blokuje slunce)
    canvas_cloud: float | None  # průměr střední + vysoké oblačnosti na západě (barvy)
    local_low_cloud: float | None  # nízká oblačnost přímo na vyhlídce
    precip_mm: float
    gust_kmh: float | None
    wind_kmh: float | None
    wind_dir_deg: float | None
    visibility_m: float | None
    dewpoint_depression_c: float | None
    temp_c: float | None
    summit_clear: float | None = None  # jen vrcholy: 0..1, nakolik je vrchol nad mraky pod ním


def extract_sunset_features(
    bundle: ForecastBundle,
    target_date: date,
    now_utc: datetime,
    settings: Settings,
    spot_key: str = "ponta_do_pargo",
    west_key: str = "west_horizon",
    mountain: bool = False,
) -> SunsetFeatures:
    """`spot_key` je klíč lokality v bundlu; `mountain` zapne model mraků pod vrcholem."""
    spot = bundle.forecasts[spot_key]
    day = next((d for d in spot.daily if d.date == target_date), None)
    if day is None:
        raise ValueError(f"Pro {target_date} nemám data (předpověď pokrývá jen několik dní).")
    sunset = day.sunset_utc
    win = settings.sunset.window
    start = sunset - timedelta(minutes=win.before_min)
    end = sunset + timedelta(minutes=win.after_min)
    pts = spot.hourly

    def avg(getter) -> float | None:
        return _avg(window_values(pts, getter, start, end))

    # Horizont a barevné plátno se čtou z bodu na moři západně od ostrova; bez něj z vyhlídky.
    west = bundle.forecasts[west_key].hourly if west_key in bundle.forecasts else pts
    west_low = _avg(window_values(west, lambda p: p.cloud_cover_low_pct, start, end))
    west_mid = _avg(window_values(west, lambda p: p.cloud_cover_mid_pct, start, end))
    west_high = _avg(window_values(west, lambda p: p.cloud_cover_high_pct, start, end))
    canvas = [v for v in (west_mid, west_high) if v is not None]

    precip = [
        p.precipitation_mm or 0.0
        for p in pts
        if sunset - timedelta(minutes=90) <= p.time_utc <= sunset + timedelta(minutes=90)
    ]
    temp = avg(lambda p: p.temperature_c)
    dew = avg(lambda p: p.dew_point_c)
    clear = None
    if mountain:  # profil atmosféry v okně západu: je vrchol nad vrstvou mraků?
        def lvl(level: int, attr: str) -> float | None:
            return avg(lambda p: getattr(p.levels[level], attr) if level in p.levels else None)

        levels = (925, 850, 800, 700)
        profile = SimpleNamespace(
            z={lv: lvl(lv, "geopotential_height_m") for lv in levels},
            rh={lv: lvl(lv, "relative_humidity_pct") for lv in levels},
            summit_m=spot.location.elevation_m,
        )
        clear = summit_clear_factor(profile)
    return SunsetFeatures(
        target_date=target_date,
        sunset_utc=sunset,
        days_ahead=(target_date - now_utc.date()).days,
        horizon_low_cloud=west_low,
        canvas_cloud=_avg(canvas),
        local_low_cloud=_max(window_values(pts, lambda p: p.cloud_cover_low_pct, start, end)),
        precip_mm=sum(precip),
        gust_kmh=_max(window_values(pts, lambda p: p.wind_gusts_kmh, start, end)),
        wind_kmh=avg(lambda p: p.wind_speed_kmh),
        wind_dir_deg=avg(lambda p: p.wind_direction_deg),
        visibility_m=avg(lambda p: p.visibility_m),
        dewpoint_depression_c=None if temp is None or dew is None else temp - dew,
        temp_c=temp,
        summit_clear=clear,
    )


def _above_deck(f: SunsetFeatures) -> SunsetFeatures:
    """Vrchol nad mraky: mraky pod ním západ neblokují, takže se tlumí (viz východ na horách)."""
    if f.summit_clear is None:
        return f
    keep = 1 - f.summit_clear

    def scale(v: float | None) -> float | None:
        return None if v is None else v * keep

    dd = f.dewpoint_depression_c
    return replace(
        f,
        horizon_low_cloud=scale(f.horizon_low_cloud),
        local_low_cloud=scale(f.local_low_cloud),
        dewpoint_depression_c=None if dd is None else dd + f.summit_clear * max(0.0, 3.0 - dd),
    )


def score_sunset(f: SunsetFeatures, settings: Settings) -> SunsetAnalysis:
    cfg = settings.sunset
    f = _above_deck(f)
    curves = cfg.piecewise
    warnings: list[str] = []
    comps: list[tuple[str, float, str]] = []

    # --- horizon_clarity: nízké mraky na západním obzoru ---
    if f.horizon_low_cloud is not None:
        value = pw(f.horizon_low_cloud, curves.horizon_low_clouds) / 100
        comps.append(
            ("horizon_clarity", value, f"nízká oblačnost na západním obzoru {f.horizon_low_cloud:.0f} %")
        )
    else:
        comps.append(("horizon_clarity", NEUTRAL, "chybí data o oblačnosti"))
        warnings.append("Chybí data o oblačnosti.")

    # --- cloud_canvas: střední + vysoká oblačnost jako plátno pro barvy ---
    if f.canvas_cloud is not None:
        value = pw(f.canvas_cloud, curves.canvas_clouds) / 100
        comps.append(("cloud_canvas", value, f"střední/vysoká oblačnost {f.canvas_cloud:.0f} %"))
    else:
        comps.append(("cloud_canvas", NEUTRAL, "chybí data"))

    # --- low_cloud_penalty: mlha/mrak přímo na vyhlídce ---
    fog_parts, fog_txt = [], []
    if f.local_low_cloud is not None:
        fog_parts.append(pw(f.local_low_cloud, LOCAL_LOW))
        fog_txt.append(f"nízká oblačnost na vyhlídce {f.local_low_cloud:.0f} %")
    if f.dewpoint_depression_c is not None:
        fog_parts.append(pw(f.dewpoint_depression_c, DEWPOINT_DEP))
        fog_txt.append(f"rozdíl T − rosný bod {f.dewpoint_depression_c:.1f} °C")
    comps.append(
        ("low_cloud_penalty", min(fog_parts, default=NEUTRAL), ", ".join(fog_txt) or "chybí data")
    )

    # --- wind_comfort ---
    if f.gust_kmh is not None:
        comps.append(("wind_comfort", pw(f.gust_kmh, WIND_GUST), f"nárazy {f.gust_kmh:.0f} km/h"))
    else:
        comps.append(("wind_comfort", NEUTRAL, "chybí data"))

    weights = cfg.weights.model_dump()
    total_w = sum(weights[name] for name, _, _ in comps)
    components = [
        ScoreComponent(
            name=name,
            value=round(value, 3),
            weight=round(weights[name] / total_w, 3),
            contribution=round(100 * value * weights[name] / total_w, 1),
            explanation=expl,
        )
        for name, value, expl in comps
        if weights[name] > 0
    ]
    raw = sum(c.contribution for c in components)

    # --- brány: srážky = okamžité NO-GO, zakrytý obzor a špatná dohlednost skóre srazí ---
    g = cfg.gates
    horizon_gate = 1.0
    if f.horizon_low_cloud is not None:
        horizon_gate = pw(
            f.horizon_low_cloud,
            [
                (g.max_horizon_low_clouds, 1.0),
                (g.max_horizon_low_clouds + HORIZON_GATE_SPAN, HORIZON_GATE_MIN),
            ],
        )
    vis_gate = 1.0
    if f.visibility_m is not None:
        vis_gate = pw(
            f.visibility_m,
            [(g.min_visibility_m / 3, VISIBILITY_GATE_MIN), (g.min_visibility_m, 1.0)],
        )
    gates = {
        "precip": 1.0 if f.precip_mm <= g.max_precipitation_mm else 0.0,
        "horizon": round(horizon_gate, 2),
        "visibility": round(vis_gate, 2),
    }
    score = raw * gates["precip"] * gates["horizon"] * gates["visibility"]

    if gates["precip"] < 1:
        warnings.append(f"Srážky kolem západu: {f.precip_mm:.1f} mm (NO-GO).")
    if gates["horizon"] < 1:
        warnings.append(
            f"Nízké mraky na západním obzoru ({f.horizon_low_cloud:.0f} %) mohou zakrýt slunce."
        )
    if gates["visibility"] < 1:
        warnings.append(f"Snížená dohlednost ({f.visibility_m / 1000:.0f} km).")

    confidence, margin = _confidence(f.days_ahead)
    verdict = (
        "GO" if score >= cfg.verdict.go else "MAYBE" if score >= cfg.verdict.maybe else "NO_GO"
    )
    return SunsetAnalysis(
        target_date=f.target_date,
        sunset_utc=f.sunset_utc,
        score_pct=round(score, 1),
        score_range=(round(max(0, score - margin), 1), round(min(100, score + margin), 1)),
        verdict=verdict,
        confidence=confidence,
        components=components,
        gates=gates,
        summary_cs=_summary_cs(score, verdict, components, gates),
        warnings=warnings,
    )


def _summary_cs(score, verdict, components, gates) -> str:
    head = {
        "GO": "Západ slunce vypadá slibně.",
        "MAYBE": "Hazard, výsledek je nejistý.",
        "NO_GO": "Dnes to nevypadá na západ, který by stál za cestu.",
    }[verdict]
    parts = [f"{head} Skóre {score:.0f} %."]
    weakest = min(components, key=lambda c: c.value)
    if gates["precip"] < 1:
        parts.append("Největší problém jsou srážky.")
    elif gates["horizon"] < 0.6:
        parts.append("Největší problém jsou nízké mraky na obzoru.")
    elif weakest.value < 0.6:
        parts.append(f"Nejslabší stránka: {_NAMES_CS[weakest.name]} ({weakest.explanation}).")
    return " ".join(parts)


def analyze_sunset(
    bundle: ForecastBundle, target_date: date, settings: Settings, now_utc: datetime
) -> SunsetAnalysis:
    f = extract_sunset_features(bundle, target_date, now_utc, settings)
    return score_sunset(f, settings)
