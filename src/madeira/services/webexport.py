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
        "summary": analysis.summary_cs,
        "warnings": analysis.warnings,
    }


def build_payload(
    settings: Settings,
    days: int = 3,
    bundle: ForecastBundle | None = None,
    now: datetime | None = None,
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
    }


def write_payload(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
