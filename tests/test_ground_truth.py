"""Ground truth štítky ukládají vedle ručního verdiktu i tehdejší predikci modelu."""

from datetime import date

from madeira.services import ground_truth


def test_record_stores_prediction_next_to_verdict(tmp_path):
    path = tmp_path / "labels.json"
    pred = {"score": 83.46, "verdict": "GO"}
    ground_truth.record(date(2026, 10, 27), "arieiro", "MAYBE", 42, path, pred)
    entry = ground_truth.load(path)["labels"]["2026-10-27"]["arieiro"]
    assert entry["verdict"] == "MAYBE" and entry["predicted_score"] == 83.5 and entry["predicted_verdict"] == "GO"
    assert entry["rated_at"] and entry["chat_id"] == 42


def test_record_without_prediction_keeps_nulls(tmp_path):
    path = tmp_path / "labels.json"
    ground_truth.record(date(2026, 10, 27), "cristo_rei", "NO_GO", None, path)
    entry = ground_truth.load(path)["labels"]["2026-10-27"]["cristo_rei"]
    assert entry["predicted_score"] is None and entry["predicted_verdict"] is None
