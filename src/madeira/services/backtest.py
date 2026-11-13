"""Zpětné ověření modelu na ručně hodnocených dnech (zlatá sada).

Štítky: `ground_truth/ratings.json` (zapisuje admin přes `madeira rate`, v gitu). Vstupy modelu: archivní odpověď Open-Meteo uložená jako
`tests/fixtures/golden/<YYYY-MM-DD>.json.gz` – dny se tak počítají vždy ze stejných dat, i bez sítě."""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from pathlib import Path

from madeira.domain.config import Settings, load_settings
from madeira.fetchers.open_meteo import HISTORICAL_MODEL, parse_response
from madeira.services import ratings
from madeira.services.forecast import horizon_locations, predict_spot

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests" / "fixtures" / "golden"
ORDER = {"NO_GO": 0, "MAYBE": 1, "GO": 2}


@dataclass
class Result:
    day: date
    spot: str
    actual: str
    score: float
    predicted: str
    note: str

    @property
    def error(self) -> int:
        """Řadová chyba: + model přeceňuje, − podceňuje (NO_GO < MAYBE < GO)."""
        return ORDER[self.predicted] - ORDER[self.actual]


def fixture_path(day: date, directory: Path = FIXTURES) -> Path:
    return directory / f"{day.isoformat()}.json.gz"


def save_fixture(day: date, payload, directory: Path = FIXTURES) -> Path:
    path = fixture_path(day, directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(json.dumps(payload, separators=(",", ":")).encode(), mtime=0))
    return path


def load_bundle(day: date, directory: Path = FIXTURES):
    path = fixture_path(day, directory)
    if not path.exists():
        return None
    payload = json.loads(gzip.decompress(path.read_bytes()))
    midnight = datetime.combine(day, time(), tzinfo=UTC)
    return parse_response(payload, horizon_locations(day), HISTORICAL_MODEL, midnight)


def run(settings: Settings | None = None, directory: Path = FIXTURES, labels=None) -> tuple[list[Result], list[str]]:
    settings = settings or load_settings()
    labels = labels if labels is not None else ratings.load()
    results: list[Result] = []
    skipped: list[str] = []
    bundles: dict[date, object] = {}
    for (day, spot), entry in sorted(labels.items()):
        if not ratings.modeled(spot, entry, settings):
            skipped.append(f"{day} {spot}: model tento spot/událost neskóruje")
            continue
        if day not in bundles:
            bundles[day] = load_bundle(day, directory)
        bundle = bundles[day]
        if bundle is None:
            skipped.append(f"{day} {spot}: chybí fixture {fixture_path(day, directory).name}")
            continue
        now = datetime.combine(day, time(), tzinfo=UTC)
        a = predict_spot(day, spot, settings, None, bundle, now)
        results.append(Result(day, spot, entry["verdict"], a.score_pct, a.verdict, entry.get("note", "")))
    return results, skipped


def summary(results: list[Result]) -> dict:
    n = len(results)
    if not n:
        return {"n": 0}
    return {
        "n": n,
        "exact": sum(r.error == 0 for r in results),
        "under": sum(r.error < 0 for r in results),
        "over": sum(r.error > 0 for r in results),
        "mean_abs_error": round(sum(abs(r.error) for r in results) / n, 2),
    }

