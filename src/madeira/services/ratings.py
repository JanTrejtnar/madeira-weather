"""Skutečné výsledky východů a západů (ground truth) zapisuje admin ručně do `ground_truth/ratings.json`.

Soubor je v gitu a je jediným zdrojem pravdy o realitě: čte ho export webu (historie), backtest i vyhodnocení.
Databáze `data/madeira.db` je jen přestavitelná cache predikcí; hodnocení se do ní před použitím nasypou
(`sync_to_store`)."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Literal

from madeira.domain.config import Settings, load_settings, spot_event
from madeira.storage.db import Store

RATINGS_PATH = Path(__file__).resolve().parents[3] / "ground_truth" / "ratings.json"
VERDICTS = ("GO", "MAYBE", "NO_GO")
Event = Literal["sunrise", "sunset"]


def normalize_verdict(value: str) -> str:
    """GO / MAYBE / NO_GO; toleruje malá písmena a NO-GO."""
    up = value.strip().upper().replace("-", "_").replace(" ", "_")
    if up not in VERDICTS:
        raise ValueError(f"Neplatný verdikt '{value}'. Možnosti: {', '.join(VERDICTS)}")
    return up


def load_raw(path: Path | None = None) -> dict:
    path = path or RATINGS_PATH
    if not path.exists():
        return {"ratings": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("ratings", {})
    return data


def load(path: Path | None = None) -> dict[tuple[date, str], dict]:
    """{(den, spot): {verdict, event, note}}."""
    return {
        (date.fromisoformat(day), spot): entry
        for day, spots in load_raw(path).get("ratings", {}).items()
        for spot, entry in spots.items()
    }


def rate(
    day: date,
    spot: str,
    verdict: str,
    note: str = "",
    event: Event | None = None,
    settings: Settings | None = None,
    today: date | None = None,
    path: Path | None = None,
) -> dict:
    """Zapíše (nebo přepíše) hodnocení. `event` se u spotů, které model zná, odvodí z konfigurace."""
    path = path or RATINGS_PATH
    verdict = normalize_verdict(verdict)
    if today is not None and day > today:
        raise ValueError("Budoucnost ještě nejde hodnotit.")
    settings = settings or load_settings()
    cfg = settings.spots.get(spot)
    if event is None:
        if cfg is None:
            raise ValueError(f"Spot '{spot}' model nezná, zadej --event sunrise|sunset.")
        event = spot_event(cfg)
    entry = {"verdict": verdict, "event": event, "note": note.strip()}
    data = load_raw(path)
    data["ratings"].setdefault(day.isoformat(), {})[spot] = entry
    data["ratings"] = dict(sorted(data["ratings"].items()))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return entry


def modeled(spot: str, entry: dict, settings: Settings) -> bool:
    """Hodnocení jde porovnat jen se spotem, který model skóruje, a ve stejné události (východ/západ)."""
    cfg = settings.spots.get(spot)
    return cfg is not None and spot_event(cfg) == entry.get("event", spot_event(cfg))


def sync_to_store(store: Store, settings: Settings | None = None, path: Path | None = None) -> int:
    """Nasype hodnocení do cache predikcí (outcomes), ať je vidí historie, `history` a `evaluate`."""
    settings = settings or load_settings()
    n = 0
    for (day, spot), entry in load(path).items():
        if modeled(spot, entry, settings):
            store.set_outcome(day, entry["verdict"], location=spot, notes=entry.get("note") or None)
            n += 1
    return n
