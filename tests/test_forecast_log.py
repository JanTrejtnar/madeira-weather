import json

from madeira.services import forecast_log


def _payload(today, run, score, lead_dates=("2026-11-14", "2026-11-15", "2026-11-17")):
    days = [
        {"date": d, "score_pct": score, "verdict": "GO", "score_range": [score - 5, score + 5],
         "gust_kmh": 7, "inversion_case": "none", "problem": None}
        for d in lead_dates
    ]
    return {"today": today, "generated_at": run, "spot_days": {"arieiro": days}}


def test_only_today_and_tomorrow_are_logged(tmp_path):
    path = tmp_path / "log.jsonl"
    n = forecast_log.record(_payload("2026-11-14", "2026-11-14T08:00:00+00:00", 80), path)
    assert n == 2  # 14. (lead 0) a 15. (lead 1); 17. je lead 3
    log = forecast_log.load(path)
    assert set(log) == {("arieiro", "2026-11-14", 0), ("arieiro", "2026-11-15", 1)}
    rec = log[("arieiro", "2026-11-15", 1)]
    assert rec["score"] == 80 and rec["gust_kmh"] == 7 and "problem" not in rec  # None se nezapisuje


def test_last_run_before_event_wins_and_other_days_are_kept(tmp_path):
    path = tmp_path / "log.jsonl"
    forecast_log.record(_payload("2026-11-13", "2026-11-13T20:00:00+00:00", 60, ("2026-11-14",)), path)
    forecast_log.record(_payload("2026-11-14", "2026-11-14T06:00:00+00:00", 90, ("2026-11-14",)), path)
    forecast_log.record(_payload("2026-11-13", "2026-11-13T22:00:00+00:00", 62, ("2026-11-14",)), path)
    log = forecast_log.load(path)
    assert log[("arieiro", "2026-11-14", 1)]["score"] == 62  # včera předpověď den předem (poslední běh)
    assert log[("arieiro", "2026-11-14", 0)]["score"] == 90  # ráno téhož dne
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and all(json.loads(x) for x in lines)
