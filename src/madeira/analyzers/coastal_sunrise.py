"""Východ slunce na pobřežním útesu (Miradouro da Ponta do Rosto): rozhoduje východní mořský horizont."""

from __future__ import annotations

from madeira.analyzers.features import SunriseFeatures
from madeira.analyzers.scoring_utils import pw
from madeira.analyzers.sunrise import (
    COLOR,
    DEWPOINT_DEP,
    NEUTRAL,
    PRECIP_MM,
    VISIBILITY_KM,
    _confidence,
)
from madeira.domain.config import Settings
from madeira.domain.models import InversionAnalysis, ScoreComponent, SunriseAnalysis

HORIZON_SPAN = 40  # o kolik % nad prahem skóre horizontu klesne na nulu
HORIZON_GATE_MIN = 0.4
GUST_SPAN = 30  # o kolik km/h nad prahem skóre větru klesne na nulu

_NAMES_CS = {
    "horizon_clarity": "čistý východní obzor",
    "cloud_canvas": "barvy oblohy",
    "visibility": "dohlednost",
    "wind": "vítr",
}


def score_coastal_sunrise(f: SunriseFeatures, settings: Settings) -> SunriseAnalysis:
    cfg = settings.coastal_sunrise
    warnings: list[str] = []
    comps: list[tuple[str, float, str]] = []

    # --- horizon_clarity: nízké mraky nad mořem na východě (do 20 % = čisto) ---
    if f.east_low_cloud is not None:
        value = pw(f.east_low_cloud, [(cfg.max_horizon_low_clouds, 1.0), (cfg.max_horizon_low_clouds + HORIZON_SPAN, 0.0)])
        comps.append(("horizon_clarity", value, f"nízká oblačnost na východním obzoru {f.east_low_cloud:.0f} %"))
    else:
        comps.append(("horizon_clarity", NEUTRAL, "chybí data o oblačnosti"))
        warnings.append("Chybí data o oblačnosti.")

    # --- cloud_canvas: střední + vysoká oblačnost jako plátno pro barvy ---
    mc = [v for v in (f.mid_cloud, f.high_cloud) if v is not None]
    if mc:
        cc = sum(mc) / len(mc)
        comps.append(("cloud_canvas", pw(cc, COLOR), f"střední/vysoká oblačnost {cc:.0f} %"))
    else:
        comps.append(("cloud_canvas", NEUTRAL, "chybí data"))

    # --- visibility: přímo na vyhlídce (útes je ve 135 m, mlha tam nebývá výjimečná) ---
    vparts, vtxt = [], []
    if f.visibility_km is not None:
        vparts.append(pw(f.visibility_km, VISIBILITY_KM))
        vtxt.append(f"dohlednost {f.visibility_km:.0f} km")
    if f.dewpoint_depression_c is not None:
        vparts.append(pw(f.dewpoint_depression_c, DEWPOINT_DEP))
        vtxt.append(f"rozdíl T − rosný bod {f.dewpoint_depression_c:.1f} °C")
    comps.append(("visibility", sum(vparts) / len(vparts) if vparts else NEUTRAL, ", ".join(vtxt) or "chybí data"))

    # --- wind: nárazy nad prahem na útesu nepříjemné ---
    if f.gust_kmh is not None:
        value = pw(f.gust_kmh, [(cfg.max_gusts_kmh - 15, 1.0), (cfg.max_gusts_kmh, 0.6), (cfg.max_gusts_kmh + GUST_SPAN, 0.0)])
        comps.append(("wind", value, f"nárazy {f.gust_kmh:.0f} km/h"))
    else:
        comps.append(("wind", NEUTRAL, "chybí data"))

    weights = cfg.weights
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

    # --- brány: srážky a zakrytý východní obzor ---
    horizon_gate = 1.0
    if f.east_low_cloud is not None:
        horizon_gate = pw(
            f.east_low_cloud,
            [(cfg.max_horizon_low_clouds, 1.0), (cfg.max_horizon_low_clouds + HORIZON_SPAN, HORIZON_GATE_MIN)],
        )
    gates = {"precip": round(pw(f.precip_mm, PRECIP_MM), 2), "horizon": round(horizon_gate, 2)}
    score = raw * gates["precip"] * gates["horizon"]

    if gates["precip"] < 1:
        warnings.append(f"Srážky kolem východu: {f.precip_mm:.1f} mm (gate ×{gates['precip']}).")
    if gates["horizon"] < 1:
        warnings.append(f"Nízké mraky na východním obzoru ({f.east_low_cloud:.0f} %) mohou zakrýt slunce.")
    if f.gust_kmh is not None and f.gust_kmh > cfg.max_gusts_kmh:
        warnings.append(f"Silné nárazy větru na útesu ({f.gust_kmh:.0f} km/h).")

    confidence, margin = _confidence(f.days_ahead)
    v = settings.sunrise.verdict
    verdict = "GO" if score >= v.go else "MAYBE" if score >= v.maybe else "NO_GO"
    return SunriseAnalysis(
        target_date=f.target_date,
        sunrise_utc=f.sunrise_utc,
        score_pct=round(score, 1),
        score_range=(round(max(0, score - margin), 1), round(min(100, score + margin), 1)),
        verdict=verdict,
        confidence=confidence,
        # moře mraků se na pobřeží neskóruje; pole zůstává kvůli společnému modelu
        inversion=InversionAnalysis(
            probability_pct=0.0, cloud_deck_top_m=None, summit_above_deck_m=None, evidence={},
            description_cs="Pobřežní vyhlídka: moře mraků se neskóruje.",
        ),
        components=components,
        gates=gates,
        summary_cs=_summary_cs(score, verdict, components, gates),
        warnings=warnings,
    )


def _summary_cs(score, verdict, components, gates) -> str:
    head = {
        "GO": "Východ slunce vypadá slibně.",
        "MAYBE": "Hazard, výsledek je nejistý.",
        "NO_GO": "Spíš zůstaň v posteli.",
    }[verdict]
    parts = [f"{head} Skóre {score:.0f} %."]
    weakest = min(components, key=lambda c: c.value)
    if gates["precip"] < 1:
        parts.append("Největší problém jsou srážky.")
    elif gates["horizon"] < 0.6:
        parts.append("Největší problém jsou nízké mraky na východním obzoru.")
    elif weakest.value < 0.6:
        parts.append(f"Nejslabší stránka: {_NAMES_CS[weakest.name]} ({weakest.explanation}).")
    return " ".join(parts)
