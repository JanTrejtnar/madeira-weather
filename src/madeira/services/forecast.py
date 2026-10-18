"""Orchestrace use-case funkcí sdílená CLI a botem (fetch → analýza → log)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from madeira.analyzers.coastal_sunrise import score_coastal_sunrise
from madeira.analyzers.features import extract_sunrise_features
from madeira.analyzers.microclimate import compare_microclimates
from madeira.analyzers.sunrise import extract_spot_features, score_sunrise
from madeira.analyzers.sunset import extract_sunset_features, score_sunset
from madeira.domain.config import Settings, load_locations
from madeira.domain.models import (
    ForecastBundle,
    MicroclimateComparison,
    SunriseAnalysis,
    SunsetAnalysis,
)
from madeira.fetchers.open_meteo import HISTORICAL_MODEL, OpenMeteoFetcher
from madeira.storage.db import Store

TZ = ZoneInfo("Atlantic/Madeira")
CACHE_DIR = Path(__file__).resolve().parents[3] / ".cache"


ALL_LOCATIONS = (
    "arieiro", "pico_ruivo", "bica_da_cana", "ponta_do_rosto",
    "ponta_do_pargo", "cristo_rei", "arieiro_sunset", "pico_ruivo_sunset",
)
LOCATION_ALIASES = {"pico_arieiro": "arieiro", "pico_do_arieiro": "arieiro"}
SUNSET_SPOTS = ("ponta_do_pargo", "cristo_rei", "arieiro_sunset", "pico_ruivo_sunset")
COASTAL_SUNRISE_SPOTS = ("ponta_do_rosto",)


def resolve_locations(value: str) -> list[str]:
    """`all` nebo klíč lokality (i alias `pico_arieiro`) -> seznam interních klíčů."""
    if value == "all":
        return list(ALL_LOCATIONS)
    key = LOCATION_ALIASES.get(value, value)
    if key not in ALL_LOCATIONS:
        options = ", ".join(["pico_arieiro", *ALL_LOCATIONS[1:], "all"])
        raise ValueError(f"Neznámá lokalita '{value}'. Možnosti: {options}")
    return [key]


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


def predict_spot(
    day: date,
    key: str,
    settings: Settings,
    store: Store | None,
    bundle: ForecastBundle,
    now: datetime,
) -> SunriseAnalysis | SunsetAnalysis:
    """Skóre libovolného spotu (východ na horách / západ na pobřeží) + zalogování predikce."""
    if key in SUNSET_SPOTS:
        spot = settings.spots[key]
        features = extract_sunset_features(
            bundle, day, now, settings, spot_key=spot.location or key,
            mountain=spot.type == "mountain_sunset",
        )
        analysis = score_sunset(features, settings)
        weights = settings.sunset.weights.model_dump()
    elif key in COASTAL_SUNRISE_SPOTS:
        features, _ = extract_spot_features(bundle, day, settings, now, key)
        analysis = score_coastal_sunrise(features, settings)
        weights = settings.coastal_sunrise.weights
    else:
        features, tuning = extract_spot_features(bundle, day, settings, now, key)
        analysis = score_sunrise(features, settings, tuning)
        weights = settings.sunrise.weights.model_dump()
    if store is not None:
        store.log_prediction(
            analysis, features, bundle.fetched_at_utc, bundle.model, weights, location=key
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


def backfill_prediction(
    day: date, settings: Settings, store: Store, location: str = "arieiro"
) -> SunriseAnalysis | SunsetAnalysis:
    """Stáhne archivované předpovědi modelů pro uplynulý den, spočítá skóre a zaloguje ho."""
    midnight = datetime.combine(day, time(), tzinfo=UTC)  # days_ahead = 0
    try:
        bundle = OpenMeteoFetcher(cache_dir=CACHE_DIR).fetch_historical(load_locations(), day, day)
    except RuntimeError:
        if day != today_local():
            raise
        # dnešek v historickém API ještě nemusí být → použij aktuální data jako "pozorování"
        bundle = load_bundle().model_copy(update={"model": HISTORICAL_MODEL})
    return predict_spot(day, location, settings, store, bundle, midnight)


def record_outcome(
    day: date,
    outcome: str,
    settings: Settings,
    store: Store,
    chat_id: int | None = None,
    location: str = "arieiro",
    notes: str | None = None,
) -> RateResult:
    """Zapíše hodnocení; pokud k danému dni chybí predikce, nejdřív ji dopočítá a uloží."""
    if day > today_local():
        raise ValueError("Východ slunce v budoucnosti ještě nejde hodnotit.")
    result = RateResult()
    if store.latest_prediction(day, location) is None:
        try:
            extra = {} if location == "arieiro" else {"location": location}
            result.analysis = backfill_prediction(day, settings, store, **extra)
            result.backfilled = True
        except Exception as exc:  # hodnocení se uloží i bez predikce, ať o něj nepřijdeš
            result.warning = f"Predikci se nepodařilo dopočítat ({exc}); hodnocení uloženo bez ní."
    store.set_outcome(day, outcome, chat_id, location=location, notes=notes)
    return result


def recompute_backfilled(
    settings: Settings, store: Store, location: str = "arieiro"
) -> list[SunriseAnalysis | SunsetAnalysis]:
    """Po změně heuristiky přepočítá zpětné predikce všech hodnocených dnů (nové features i skóre)."""
    results = []
    for day in store.rated_dates(location):
        store.delete_backfilled(day, location)
        if store.latest_prediction(day, location) is None:  # skutečná předpověď má přednost
            extra = {} if location == "arieiro" else {"location": location}
            results.append(backfill_prediction(day, settings, store, **extra))
    return results


CHUNK_DAYS = 30


def fetch_history(
    settings: Settings,
    store: Store,
    locations: list[str],
    days: int = 90,
    end: date | None = None,
    fetcher: OpenMeteoFetcher | None = None,
) -> dict[str, int]:
    """Stáhne archivované předpovědi (Historical Forecast API) za posledních `days` dní
    a uloží pro každou lokalitu zpětné predikce (model = historical_forecast).

    Vrací počet uložených dní podle lokality. Dny, které model nepokrývá, se přeskočí."""
    end = end or today_local() - timedelta(days=1)  # dnešek v archivu často ještě není
    start = end - timedelta(days=days - 1)
    fetcher = fetcher or OpenMeteoFetcher(cache_dir=CACHE_DIR)
    locs = load_locations()
    counts = dict.fromkeys(locations, 0)
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(chunk_start + timedelta(days=CHUNK_DAYS - 1), end)
        bundle = fetcher.fetch_historical(locs, chunk_start, chunk_end)
        day = chunk_start
        while day <= chunk_end:
            midnight = datetime.combine(day, time(), tzinfo=UTC)
            for key in locations:
                store.delete_backfilled(day, key)
                try:
                    predict_spot(day, key, settings, store, bundle, midnight)
                except ValueError:  # den není v odpovědi modelu
                    continue
                counts[key] += 1
            day += timedelta(days=1)
        chunk_start = chunk_end + timedelta(days=1)
    return counts
