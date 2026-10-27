"""Ground truth z botového dotazníku: ruční verdikt (GO / MAYBE / NO_GO) po dnech a spotech.

Ukládá se do `scripts/ground_truth_labels.json` jako {"labels": {"YYYY-MM-DD": {spot: {verdict, rated_at, chat_id}}}}.
Zápis je atomický (dočasný soubor + replace), aby souběžné kliknutí nepoškodilo soubor."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from madeira.domain.config import Settings, spot_event

TZ = ZoneInfo("Atlantic/Madeira")
LABELS_PATH = Path(__file__).resolve().parents[3] / "scripts" / "ground_truth_labels.json"

# Sledovaná místa v pořadí tlačítek (klíče ze `spots` v config/weights.yaml).
SURVEY_SPOTS = ("arieiro", "bica_da_cana", "ponta_do_pargo", "ponta_do_rosto", "cristo_rei")
VERDICT_CODES = {"G": "GO", "M": "MAYBE", "N": "NO_GO"}
VERDICT_ICONS = {"GO": "🟢", "MAYBE": "🟡", "NO_GO": "🔴"}
_DOC = (
    "Ruční hodnocení z botového dotazníku (10:00 místního času). Východy se hodnotí za dnešek, západy za včerejšek. "
    "verdict = GO / MAYBE / NO_GO podle toho, jak to ve skutečnosti dopadlo."
)


def survey_days(today: date, settings: Settings) -> dict[str, date]:
    """Který den se u spotu hodnotí: východ = dnes, západ = včera (v 10:00 dnešní západ ještě nenastal)."""
    return {
        key: today - timedelta(days=1) if spot_event(settings.spots[key]) == "sunset" else today
        for key in SURVEY_SPOTS
        if key in settings.spots
    }


def load(path: Path = LABELS_PATH) -> dict:
    if not path.exists():
        return {"_doc": _DOC, "labels": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("labels", {})
    return data


def get(day: date, spot: str, path: Path = LABELS_PATH) -> str | None:
    entry = load(path)["labels"].get(day.isoformat(), {}).get(spot)
    return entry["verdict"] if entry else None


def record(day: date, spot: str, verdict: str, chat_id: int | None, path: Path = LABELS_PATH) -> None:
    if verdict not in VERDICT_ICONS:
        raise ValueError(f"Neznámý verdikt: {verdict}")
    data = load(path)
    data["_doc"] = _DOC
    data["labels"].setdefault(day.isoformat(), {})[spot] = {
        "verdict": verdict,
        "rated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "chat_id": chat_id,
    }
    data["labels"] = dict(sorted(data["labels"].items()))
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
