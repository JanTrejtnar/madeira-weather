"""Orchestrace use-case funkcí sdílená CLI a botem (fetch → analýza → log)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from madeira.analyzers.features import extract_sunrise_features
from madeira.analyzers.microclimate import compare_microclimates
from madeira.analyzers.sunrise import score_sunrise
from madeira.domain.config import Settings, load_locations
from madeira.domain.models import ForecastBundle, MicroclimateComparison, SunriseAnalysis
from madeira.fetchers.open_meteo import HISTORICAL_MODEL, OpenMeteoFetcher
from madeira.storage.db import Store

TZ = ZoneInfo("Atlantic/Madeira")
CACHE_DIR = Path(__file__).resolve().parents[3] / ".cache"


def today_local() -> date:
    return datetime.now(TZ).date()


def parse_day(value: str) -> date:
    if value == "today":
        return today_local()
    if value == "tomorrow":
        return today_local() + timedelta(days=1)
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValueError("Použij today, tomorrow nebo YYYY-MM-DD") from None


def load_bundle() -> ForecastBundle:
    return OpenMeteoFetcher(cache_dir=CACHE_DIR).fetch(load_locations())


def predict(
    day: date,
    settings: Settings,
    store: Store | None = None,
    bundle: ForecastBundle | None = None,
    now: datetime | None = None,
) -> SunriseAnalysis:
    """Spočítá skóre východu pro den a (je-li store) zaloguje predikci do SQLite."""
    bundle = bundle or load_bundle()
    now = now or datetime.now(UTC)
    features = extract_sunrise_features(bundle, day, now, settings.sunrise.window)
    analysis = score_sunrise(features, settings)
    if store is not None:
        store.log_prediction(
            analysis,
            features,
            bundle.fetched_at_utc,
            bundle.model,
            settings.sunrise.weights.model_dump(),
        )
    return analysis


def predict_week(settings: Settings, store: Store | None = None) -> list[SunriseAnalysis]:
    bundle = load_bundle()
    now = datetime.now(UTC)
    days = [d.date for d in bundle.forecasts["arieiro"].daily if d.sunrise_utc > now]
    return [predict(d, settings, store, bundle) for d in days]


def compare(day: date, from_: str, to: str) -> MicroclimateComparison:
    start = datetime.combine(day, time.fromisoformat(from_), tzinfo=TZ).astimezone(UTC)
    end = datetime.combine(day, time.fromisoformat(to), tzinfo=TZ).astimezone(UTC)
    return compare_microclimates(load_bundle(), start, end)


@dataclass
class RateResult:
    backfilled: bool = False  # predikce byla dopočítána ze stažených historických dat
    analysis: SunriseAnalysis | None = None
    warning: str | None = None


def backfill_prediction(day: date, settings: Settings, store: Store) -> SunriseAnalysis:
    """Stáhne archivované předpovědi modelů pro uplynulý den, spočítá skóre a zaloguje ho."""
    midnight = datetime.combine(day, time(), tzinfo=UTC)  # days_ahead = 0
    try:
        bundle = OpenMeteoFetcher(cache_dir=CACHE_DIR).fetch_historical(load_locations(), day, day)
    except RuntimeError:
        if day != today_local():
            raise
        # dnešek v historickém API ještě nemusí být → použij aktuální data jako "pozorování"
        bundle = load_bundle().model_copy(update={"model": HISTORICAL_MODEL})
    return predict(day, settings, store, bundle, now=midnight)


def record_outcome(
    day: date, outcome: str, settings: Settings, store: Store, chat_id: int | None = None
) -> RateResult:
    """Zapíše hodnocení; pokud k danému dni chybí predikce, nejdřív ji dopočítá a uloží."""
    if day > today_local():
        raise ValueError("Východ slunce v budoucnosti ještě nejde hodnotit.")
    result = RateResult()
    if store.latest_prediction(day) is None:
        try:
            result.analysis = backfill_prediction(day, settings, store)
            result.backfilled = True
        except Exception as exc:  # hodnocení se uloží i bez predikce, ať o něj nepřijdeš
            result.warning = f"Predikci se nepodařilo dopočítat ({exc}); hodnocení uloženo bez ní."
    store.set_outcome(day, outcome, chat_id)
    return result


def recompute_backfilled(settings: Settings, store: Store) -> list[SunriseAnalysis]:
    """Po změně heuristiky přepočítá zpětné predikce všech hodnocených dnů (nové features i skóre)."""
    results = []
    for day in store.rated_dates():
        store.delete_backfilled(day)
        if store.latest_prediction(day) is None:  # skutečná předpověď má přednost, ta zůstala
            results.append(backfill_prediction(day, settings, store))
    return results
