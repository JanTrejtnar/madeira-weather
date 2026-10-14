"""Export předpovědi východu slunce do statického JSONu pro web (GitHub Pages)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from madeira.analyzers.features import extract_sunrise_features
from madeira.analyzers.sunrise import score_sunrise
from madeira.domain.config import Settings
from madeira.domain.models import ForecastBundle, SunriseAnalysis
from madeira.services import forecast

TZ = ZoneInfo("Atlantic/Madeira")
ARIEIRO_ELEVATION_M = 1818


def _day_entry(analysis: SunriseAnalysis, features, departure_before_min: int) -> dict:
    sunrise = analysis.sunrise_utc.astimezone(TZ)
    deck = analysis.inversion.cloud_deck_top_m
    above = analysis.inversion.summit_above_deck_m
    return {
        "date": analysis.target_date.isoformat(),
        "sunrise": f"{sunrise:%H:%M}",
        "departure_funchal": f"{sunrise - timedelta(minutes=departure_before_min):%H:%M}",
        "verdict": analysis.verdict,
        "score_pct": round(analysis.score_pct),
        "score_range": [round(analysis.score_range[0]), round(analysis.score_range[1])],
        "confidence": analysis.confidence,
        "cloud_deck_top_m": None if deck is None else round(deck),
        "summit_above_deck_m": None if above is None else round(above),
        "inversion_pct": round(analysis.inversion.probability_pct),
        "wind_kmh": None if features.wind_kmh is None else round(features.wind_kmh),
        "gust_kmh": None if features.gust_kmh is None else round(features.gust_kmh),
        "wind_dir_deg": None if features.wind_dir_deg is None else round(features.wind_dir_deg),
        "summary_cs": analysis.summary_cs,
        # Strukturovaná data, ze kterých frontend skládá souhrn v CZ/EN
        "inversion_case": _inversion_case(deck, above),
        "problem": _problem(analysis),
        "weakest": _weakest(analysis),
        "precip_mm": round(features.precip_mm, 1),
        "alerts": _alerts(analysis, features),
    }


def _inversion_case(deck: float | None, above: float | None) -> str:
    if deck is None:
        return "none"
    if above is not None and above > 100:
        return "above"
    if above is not None and above > -200:
        return "edge"
    return "inside"


def _weakest(a: SunriseAnalysis) -> str | None:
    comp = min(a.components, key=lambda c: c.value, default=None)
    return comp.name if comp and comp.value < 0.6 else None


def _problem(a: SunriseAnalysis) -> str | None:
    if a.gates["precip"] < 1:
        return "precip"
    if a.gates["fog"] < 0.6:
        return "fog"
    return None


def _alerts(a: SunriseAnalysis, f) -> list[dict]:
    alerts = []
    if a.gates["precip"] < 1:
        alerts.append({"code": "precip", "value": round(f.precip_mm, 1)})
    if a.gates["fog"] < 0.6:
        alerts.append({"code": "fog"})
    if a.gates["gale"] < 1 and f.gust_kmh is not None:
        alerts.append({"code": "gusts", "value": round(f.gust_kmh)})
    if f.low_cloud_summit is None and f.mid_cloud is None and f.high_cloud is None:
        alerts.append({"code": "no_cloud_data"})
    return alerts


def build_payload(
    settings: Settings,
    days: int = 3,
    bundle: ForecastBundle | None = None,
    now: datetime | None = None,
    history: list[dict] | None = None,
) -> dict:
    """Spočítá zítřek a výhled na `days` dní (první den = zítřek, místní čas Madeiry)."""
    bundle = bundle or forecast.load_bundle()
    now = now or datetime.now(UTC)
    first = now.astimezone(TZ).date() + timedelta(days=1)
    available = {d.date for d in bundle.forecasts["arieiro"].daily}
    entries = []
    for offset in range(days):
        day = first + timedelta(days=offset)
        if day not in available:
            break
        features = extract_sunrise_features(bundle, day, now, settings.sunrise.window)
        analysis = score_sunrise(features, settings)
        entries.append(_day_entry(analysis, features, settings.bot.departure_before_min))
    if not entries:
        raise RuntimeError("Model nevrátil data pro zítřek.")
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "model": bundle.model,
        "location": {"name": "Pico do Arieiro", "elevation_m": ARIEIRO_ELEVATION_M},
        "tomorrow": entries[0],
        "days": entries,
        "history": history or [],
    }


def history_entries(rows: list[dict]) -> list[dict]:
    """Řádky pohledu `calibration` (predikce vs. hodnocení) ve slim podobě pro web."""
    return [
        {
            "date": r["target_date"],
            "score_pct": None if r["score"] is None else round(r["score"]),
            "verdict": r["verdict"],
            "inversion_pct": None if r["inversion_pct"] is None else round(r["inversion_pct"]),
            "outcome": r["outcome"],
            "source": "backfill" if r["model"] == "historical_forecast" else (
                "forecast" if r["model"] else None
            ),
        }
        for r in rows
    ]


def write_payload(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
