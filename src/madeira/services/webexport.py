"""Export předpovědi východu slunce do statického JSONu pro web (GitHub Pages)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from madeira.analyzers.daily_weather import day_weather
from madeira.analyzers.features import extract_sunrise_features
from madeira.analyzers.sunrise import extract_spot_features, score_sunrise
from madeira.analyzers.sunset import extract_sunset_features, score_sunset
from madeira.domain.config import Settings
from madeira.domain.models import ForecastBundle, SunriseAnalysis, SunsetAnalysis
from madeira.services import forecast
from madeira.storage.db import Store

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


def _sunset_entry(analysis: SunsetAnalysis, features, departure_min: int) -> dict:
    sunset = analysis.sunset_utc.astimezone(TZ)
    return {
        "date": analysis.target_date.isoformat(),
        "sunset": f"{sunset:%H:%M}",
        "departure_funchal": f"{sunset - timedelta(minutes=departure_min):%H:%M}",
        "verdict": analysis.verdict,
        "score_pct": round(analysis.score_pct),
        "score_range": [round(analysis.score_range[0]), round(analysis.score_range[1])],
        "confidence": analysis.confidence,
        "horizon_low_cloud_pct": _round(features.horizon_low_cloud),
        "canvas_cloud_pct": _round(features.canvas_cloud),
        "wind_kmh": _round(features.wind_kmh),
        "gust_kmh": _round(features.gust_kmh),
        "wind_dir_deg": _round(features.wind_dir_deg),
        "visibility_km": None if features.visibility_m is None else round(features.visibility_m / 1000),
        "precip_mm": round(features.precip_mm, 1),
        "summary_cs": analysis.summary_cs,
        "problem": "precip" if analysis.gates["precip"] < 1 else (
            "horizon" if analysis.gates["horizon"] < 0.6 else None
        ),
        "weakest": _weakest(analysis),
        "warnings": analysis.warnings,
    }


def _round(value: float | None) -> int | None:
    return None if value is None else round(value)


def _spot_meta(settings: Settings, bundle: ForecastBundle, key: str) -> dict:
    loc = bundle.forecasts[key].location
    return {
        "key": key,
        "name": settings.spots[key].name,
        "type": settings.spots[key].type,
        "lat": loc.lat,
        "lon": loc.lon,
        "elevation_m": round(loc.elevation_m),
    }


def tomorrow_spots(settings: Settings, bundle: ForecastBundle, day, now: datetime) -> dict:
    """Zítřejší ranní (Arieiro, Bica da Cana) a večerní (Ponta do Pargo) vyhlídky."""
    morning = []
    for key in ("arieiro", "bica_da_cana"):
        features, tuning = extract_spot_features(bundle, day, settings, now, key)
        analysis = score_sunrise(features, settings, tuning)
        departure = settings.spots[key].departure_from_funchal_min
        morning.append(_spot_meta(settings, bundle, key) | _day_entry(analysis, features, departure))
    key = "ponta_do_pargo"
    features = extract_sunset_features(bundle, day, now, settings)
    entry = _sunset_entry(
        score_sunset(features, settings), features, settings.spots[key].departure_from_funchal_min
    )
    return {"morning": morning, "evening": [_spot_meta(settings, bundle, key) | entry]}


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
    store: Store | None = None,
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
        if store is not None:  # predikce se zapíše, ať se den objeví v historii
            store.log_prediction(
                analysis, features, bundle.fetched_at_utc, bundle.model,
                settings.sunrise.weights.model_dump(),
            )
        entry = _day_entry(analysis, features, settings.bot.departure_before_min)
        entry["weather"] = day_weather(bundle, day)
        entries.append(entry)
    if not entries:
        raise RuntimeError("Model nevrátil data pro zítřek.")
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "model": bundle.model,
        "location": {"name": "Pico do Arieiro", "elevation_m": ARIEIRO_ELEVATION_M},
        "tomorrow": entries[0],
        "tomorrow_spots": tomorrow_spots(settings, bundle, first, now),
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


def read_history(path: Path) -> list[dict]:
    """Historie z dříve exportovaného JSONu (prázdná, pokud soubor chybí nebo je rozbitý)."""
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("history", [])
    except (OSError, ValueError, AttributeError):
        return []


def write_payload(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
