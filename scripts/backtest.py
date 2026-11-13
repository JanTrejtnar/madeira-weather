"""Backtest modelu na ručně hodnocených dnech.

    python3 scripts/backtest.py                  # skóre ze uložených fixtur
    python3 scripts/backtest.py --fetch          # chybějící dny stáhne z Open-Meteo a uloží jako fixture
Nic nepřepisuje v docs/ ani v databázi."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from madeira.fetchers.open_meteo import OpenMeteoFetcher
from madeira.services import backtest, ratings
from madeira.services.forecast import horizon_locations


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true", help="stáhnout chybějící fixtury z Open-Meteo")
    args = ap.parse_args()
    labels = ratings.load()
    if args.fetch:
        fetcher = OpenMeteoFetcher()
        for day in sorted({d for d, _ in labels}):
            if not backtest.fixture_path(day).exists():
                print("stahuji", day)
                payload = fetcher.fetch_historical_payload(horizon_locations(day), day, day)
                backtest.save_fixture(day, payload)
    results, skipped = backtest.run(labels=labels)
    print(f"{'den':<11}{'spot':<20}{'skutečnost':<12}{'model':<8}{'skóre':>6}  chyba")
    for r in results:
        print(f"{r.day!s:<11}{r.spot:<20}{r.actual:<12}{r.predicted:<8}{r.score:>6.1f}  {r.error:+d}")
    print("\n", backtest.summary(results))
    for s in skipped:
        print("přeskočeno:", s)


if __name__ == "__main__":
    main()
