import json
from datetime import UTC, datetime

from madeira.domain.config import Settings, load_locations
from madeira.fetchers.open_meteo import parse_response
from madeira.services import webexport
from tests.test_analyzers import FIXTURE, NOW


def _bundle():
    return parse_response(json.loads(FIXTURE.read_text()), load_locations(), "best_match", NOW)


def test_payload_shape(tmp_path):
    payload = webexport.build_payload(Settings(), days=3, bundle=_bundle(), now=NOW)
    t = payload["tomorrow"]
    assert t["date"] == "2026-10-13"
    assert t["verdict"] in {"GO", "MAYBE", "NO_GO"}
    assert 0 <= t["score_pct"] <= 100
    assert t["departure_funchal"] < t["sunrise"]
    assert t["inversion_case"] in {"none", "above", "edge", "inside"}
    assert payload["history"] == []
    assert len(payload["days"]) == 3
    out = tmp_path / "docs" / "data.json"
    webexport.write_payload(payload, out)
    assert json.loads(out.read_text(encoding="utf-8"))["tomorrow"] == t
    assert datetime.fromisoformat(payload["generated_at"]).tzinfo == UTC
