"""Log toho, co model skutečně předpověděl (ne zpětný výpočet z archivu).

Web v Actions plní historii zpětně z archivních dat, takže se jinak ztrácí informace, co model hlásil den
předem. Běh exportu proto zapíše do `ground_truth/forecast_log.jsonl` poslední předpověď každého spotu pro
každý den a vzdálenost dne (`lead` = 0 dnes, 1 zítra). Klíč (spot, den, lead) se přepisuje, takže platí
poslední běh před událostí a soubor roste jen o ~24 řádků denně."""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

FORECAST_LOG_PATH = Path(__file__).resolve().parents[3] / "ground_truth" / "forecast_log.jsonl"
MAX_LEAD = 1
FIELDS = (
    "wind_kmh", "gust_kmh", "precip_mm", "canvas_cloud_pct", "horizon_low_cloud_pct", "visibility_km",
    "cloud_deck_top_m", "summit_above_deck_m", "inversion_case", "problem", "weakest",
)


def _key(rec: dict) -> tuple[str, str, int]:
    return rec["spot"], rec["date"], rec["lead"]


def load(path: Path | None = None) -> dict[tuple[str, str, int], dict]:
    path = path or FORECAST_LOG_PATH
    if not path.exists():
        return {}
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rec = json.loads(line)
            out[_key(rec)] = rec
    return out


def records_from_payload(payload: dict) -> list[dict]:
    """Řádky logu z `spot_days` exportu webu (jen dnes a zítra)."""
    today = date.fromisoformat(payload["today"])
    run = payload["generated_at"]
    rows = []
    for spot, days in payload.get("spot_days", {}).items():
        for entry in days:
            lead = (date.fromisoformat(entry["date"]) - today).days
            if not 0 <= lead <= MAX_LEAD:
                continue
            rows.append({
                "spot": spot, "date": entry["date"], "lead": lead, "run": run,
                "score": entry["score_pct"], "verdict": entry["verdict"],
                "range": entry.get("score_range"),
                **{k: entry[k] for k in FIELDS if entry.get(k) is not None},
            })
    return rows


def record(payload: dict, path: Path | None = None) -> int:
    """Připíše/přepíše předpovědi z exportu; vrací počet zapsaných řádků. Seřazeno pro čitelný diff."""
    path = path or FORECAST_LOG_PATH
    log = load(path)
    new = records_from_payload(payload)
    for rec in new:
        log[_key(rec)] = rec
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    lines = [json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in sorted(log.values(), key=_key)]
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return len(new)
