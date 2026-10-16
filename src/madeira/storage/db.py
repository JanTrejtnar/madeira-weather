from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from dataclasses import asdict
from datetime import UTC, date, datetime
from pathlib import Path

from madeira.analyzers.features import SunriseFeatures
from madeira.domain.models import OUTCOME_LABELS, SunriseAnalysis

SCHEMA = """
CREATE TABLE IF NOT EXISTS predictions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    forecast_fetched_at TEXT NOT NULL,
    target_date TEXT NOT NULL,
    sunrise_utc TEXT NOT NULL,
    days_ahead INTEGER NOT NULL,
    model TEXT NOT NULL,
    score REAL NOT NULL,
    verdict TEXT NOT NULL,
    confidence TEXT NOT NULL,
    inversion_pct REAL NOT NULL,
    deck_top_m REAL,
    features_json TEXT NOT NULL,
    components_json TEXT NOT NULL,
    gates_json TEXT NOT NULL,
    weights_json TEXT NOT NULL,
    UNIQUE (target_date, forecast_fetched_at)
);
CREATE INDEX IF NOT EXISTS idx_predictions_date ON predictions (target_date);

CREATE TABLE IF NOT EXISTS outcomes (
    target_date TEXT PRIMARY KEY,
    outcome TEXT NOT NULL,
    rated_at TEXT NOT NULL,
    chat_id INTEGER
);

CREATE TABLE IF NOT EXISTS subscribers (
    chat_id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL
);

-- Kalibrační pohled: ke každému hodnocení poslední predikce vytvořená PŘED východem slunce;
-- není-li, použije se zpětně dopočítaná (model = 'historical_forecast').
DROP VIEW IF EXISTS calibration;
CREATE VIEW calibration AS
SELECT o.target_date, o.outcome, o.rated_at,
       p.id AS prediction_id, p.model, p.score, p.verdict, p.confidence, p.inversion_pct,
       p.days_ahead, p.features_json, p.components_json, p.gates_json, p.weights_json
FROM outcomes o
LEFT JOIN predictions p ON p.id = (
    SELECT id FROM predictions
    WHERE target_date = o.target_date
      AND (created_at < sunrise_utc OR model = 'historical_forecast')
    ORDER BY (model = 'historical_forecast') ASC, created_at DESC LIMIT 1
);
"""


def default_db_path() -> Path:
    env = os.environ.get("MADEIRA_DB")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "data" / "madeira.db"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class Store:
    """Tenké SQLite úložiště. Připojení se otevírá per volání (bezpečné pro asyncio.to_thread)."""

    def __init__(self, path: Path | None = None):
        self.path = path or default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    # --- predikce ---------------------------------------------------------
    def log_prediction(
        self,
        analysis: SunriseAnalysis,
        features: SunriseFeatures,
        forecast_fetched_at: datetime,
        model: str,
        weights: dict,
    ) -> bool:
        """Zapíše predikci; stejná data předpovědi pro stejný den se neukládají dvakrát."""
        with closing(self._conn()) as conn, conn:
            cur = conn.execute(
                """INSERT OR IGNORE INTO predictions
                (created_at, forecast_fetched_at, target_date, sunrise_utc, days_ahead, model,
                 score, verdict, confidence, inversion_pct, deck_top_m,
                 features_json, components_json, gates_json, weights_json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    _now(),
                    forecast_fetched_at.isoformat(timespec="seconds"),
                    analysis.target_date.isoformat(),
                    analysis.sunrise_utc.isoformat(timespec="seconds"),
                    features.days_ahead,
                    model,
                    analysis.score_pct,
                    analysis.verdict,
                    analysis.confidence,
                    analysis.inversion.probability_pct,
                    analysis.inversion.cloud_deck_top_m,
                    json.dumps(asdict(features), default=str),
                    json.dumps([c.model_dump() for c in analysis.components]),
                    json.dumps(analysis.gates),
                    json.dumps(weights),
                ),
            )
            return cur.rowcount == 1

    def latest_prediction(self, target_date: date) -> dict | None:
        with closing(self._conn()) as conn:
            row = conn.execute(
                "SELECT * FROM predictions WHERE target_date = ? ORDER BY created_at DESC, id DESC LIMIT 1",
                (target_date.isoformat(),),
            ).fetchone()
        return dict(row) if row else None

    def delete_backfilled(self, target_date: date) -> None:
        """Smaže zpětně dopočítané predikce dne (skutečné předpovědi z doby před východem zůstávají)."""
        with closing(self._conn()) as conn, conn:
            conn.execute(
                "DELETE FROM predictions WHERE target_date = ? AND model = 'historical_forecast'",
                (target_date.isoformat(),),
            )

    def rated_dates(self) -> list[date]:
        with closing(self._conn()) as conn:
            rows = conn.execute("SELECT target_date FROM outcomes ORDER BY target_date").fetchall()
        return [date.fromisoformat(r["target_date"]) for r in rows]

    # --- zpětná vazba -----------------------------------------------------
    def set_outcome(self, target_date: date, outcome: str, chat_id: int | None = None) -> None:
        if outcome not in OUTCOME_LABELS:
            raise ValueError(f"Neznámý výsledek: {outcome}")
        with closing(self._conn()) as conn, conn:
            conn.execute(
                """INSERT INTO outcomes (target_date, outcome, rated_at, chat_id) VALUES (?,?,?,?)
                ON CONFLICT(target_date) DO UPDATE SET
                    outcome = excluded.outcome, rated_at = excluded.rated_at,
                    chat_id = excluded.chat_id""",
                (target_date.isoformat(), outcome, _now(), chat_id),
            )

    def get_outcome(self, target_date: date) -> str | None:
        with closing(self._conn()) as conn:
            row = conn.execute(
                "SELECT outcome FROM outcomes WHERE target_date = ?", (target_date.isoformat(),)
            ).fetchone()
        return row["outcome"] if row else None

    def calibration_rows(self, limit: int = 50) -> list[dict]:
        with closing(self._conn()) as conn:
            rows = conn.execute(
                "SELECT * FROM calibration ORDER BY target_date DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def history_rows(self, up_to: date, limit: int = 60) -> list[dict]:
        """Všechny dny s predikcí nebo hodnocením do `up_to` včetně; hodnocení může chybět."""
        with closing(self._conn()) as conn:
            rows = conn.execute(
                """WITH dates AS (
                       SELECT target_date FROM predictions UNION SELECT target_date FROM outcomes)
                   SELECT d.target_date, o.outcome, p.model, p.score, p.verdict, p.inversion_pct
                   FROM dates d
                   LEFT JOIN outcomes o ON o.target_date = d.target_date
                   LEFT JOIN predictions p ON p.id = (
                       SELECT id FROM predictions
                       WHERE target_date = d.target_date
                         AND (created_at < sunrise_utc OR model = 'historical_forecast')
                       ORDER BY (model = 'historical_forecast') ASC, created_at DESC LIMIT 1)
                   WHERE d.target_date <= ? AND (p.id IS NOT NULL OR o.outcome IS NOT NULL)
                   ORDER BY d.target_date DESC LIMIT ?""",
                (up_to.isoformat(), limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # --- odběratelé -------------------------------------------------------
    def subscribe(self, chat_id: int) -> None:
        with closing(self._conn()) as conn, conn:
            conn.execute(
                "INSERT OR IGNORE INTO subscribers (chat_id, created_at) VALUES (?,?)",
                (chat_id, _now()),
            )

    def unsubscribe(self, chat_id: int) -> None:
        with closing(self._conn()) as conn, conn:
            conn.execute("DELETE FROM subscribers WHERE chat_id = ?", (chat_id,))

    def subscribers(self) -> list[int]:
        with closing(self._conn()) as conn:
            return [r["chat_id"] for r in conn.execute("SELECT chat_id FROM subscribers")]
