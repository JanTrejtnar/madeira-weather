"""Vyhodnocení přesnosti modelu: skóre/verdikt vs. ground truth (ručně zadané výsledky)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from statistics import mean

from madeira.domain.models import OUTCOME_TO_VERDICT

VERDICTS = ("GO", "MAYBE", "NO_GO")
MIN_SAMPLES = 10  # pod tímto počtem jsou doporučení jen orientační

# Feature -> kde se to v config/weights.yaml ladí (pro doporučení)
FEATURE_HINTS = {
    "sunset": {
        "horizon_low_cloud": "sunset.piecewise.horizon_low_clouds / sunset.gates.max_horizon_low_clouds",
        "canvas_cloud": "sunset.piecewise.canvas_clouds",
        "local_low_cloud": "sunset.weights.low_cloud_penalty",
        "gust_kmh": "sunset.weights.wind_comfort",
        "visibility_m": "sunset.gates.min_visibility_m",
        "precip_mm": "sunset.gates.max_precipitation_mm",
    },
    "sunrise": {
        "low_cloud_summit": "sunrise.fog / spots.<spot>.max_gusts_kmh (mlha na vrcholu)",
        "east_low_cloud": "sunrise.weights.sky (východní obzor)",
        "east_mid_cloud": "sunrise.weights.sky",
        "gust_kmh": "spots.<spot>.max_gusts_kmh",
        "precip_mm": "sunrise gate na srážky",
    },
}
VERDICT_PATH = {"sunset": "sunset.verdict.go", "sunrise": "sunrise.verdict.go"}


@dataclass
class Case:
    date: str
    score: float
    predicted: str
    actual: str
    outcome: str
    notes: str | None
    features: dict
    components: dict[str, float]


@dataclass
class Evaluation:
    location: str
    kind: str
    total_rated: int
    cases: list[Case]
    skipped_no_prediction: int
    confusion: dict[str, dict[str, int]]
    go: dict
    no_go: dict
    false_positives: list[Case] = field(default_factory=list)
    false_negatives: list[Case] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)


def _load(raw) -> dict | list:
    return json.loads(raw) if isinstance(raw, str) and raw else {}


def _prf(tp: int, fp: int, fn: int) -> dict:
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    f1 = 2 * precision * recall / (precision + recall) if precision and recall else None
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}


def _best_threshold(cases: list[Case]) -> tuple[float, float] | None:
    """Práh skóre pro GO s nejvyšším F1 vůči skutečnému GO (prohledává 30–95)."""
    best: tuple[float, float] | None = None
    for thr in range(30, 96, 5):
        tp = sum(c.score >= thr and c.actual == "GO" for c in cases)
        fp = sum(c.score >= thr and c.actual != "GO" for c in cases)
        fn = sum(c.score < thr and c.actual == "GO" for c in cases)
        f1 = _prf(tp, fp, fn)["f1"] or 0.0
        if best is None or f1 > best[1] + 1e-9:
            best = (float(thr), f1)
    return best


def _feature_means(cases: list[Case]) -> dict[str, float]:
    keys = {k for c in cases for k, v in c.features.items() if isinstance(v, int | float)}
    out = {}
    for k in keys:
        vals = [c.features[k] for c in cases if isinstance(c.features.get(k), int | float)]
        if vals:
            out[k] = mean(vals)
    return out


def _recommend(ev: Evaluation, go_threshold: float) -> list[str]:
    recs: list[str] = []
    n = len(ev.cases)
    if n == 0:
        return ["Žádná hodnocená data – zadej ground truth (`madeira label` / `import-labels`)."]
    if n < MIN_SAMPLES:
        recs.append(
            f"Jen {n} hodnocených dnů (doporučeno ≥ {MIN_SAMPLES}) – závěry jsou orientační."
        )
    tp = [c for c in ev.cases if c.predicted == "GO" and c.actual == "GO"]
    fps, fns = ev.false_positives, ev.false_negatives
    if not fps and not fns:
        recs.append("Model sedí – žádné falešné GO ani zmeškané GO, prahy neměň.")
        return recs

    best = _best_threshold(ev.cases)
    path = VERDICT_PATH[ev.kind]
    if best and abs(best[0] - go_threshold) >= 5:
        recs.append(
            f"Práh GO: nyní {go_threshold:.0f}, nejlepší shoda s realitou při ≈ {best[0]:.0f} "
            f"(F1 {best[1]:.2f}) → zvaž `{path}: {best[0]:.0f}`."
        )
    elif len(fps) > len(fns):
        recs.append("Převažují falešná GO – model je příliš optimistický; zpřísni křivky níže.")
    elif len(fns) > len(fps):
        recs.append("Převažují zmeškaná GO – model je příliš přísný; zmírni křivky níže.")

    hints = FEATURE_HINTS[ev.kind]
    ref = tp or [c for c in ev.cases if c.actual == "GO"]
    for group, label, sign in ((fps, "falešných GO", 1), (fns, "zmeškaných GO", -1)):
        if not group or not ref:
            continue
        g, r = _feature_means(group), _feature_means(ref)
        scored = []
        for k, hint in hints.items():
            if k in g and k in r and max(abs(g[k]), abs(r[k])) > 0:
                diff = (g[k] - r[k]) / max(abs(g[k]), abs(r[k]))
                scored.append((abs(diff), k, hint, g[k], r[k]))
        for strength, k, hint, gv, rv in sorted(scored, reverse=True)[:2]:
            if strength < 0.25:
                continue
            action = "zpřísni" if sign > 0 else "zmírni"
            recs.append(
                f"{k}: u {label} průměr {gv:.0f}, u správných GO {rv:.0f} → {action} `{hint}`."
            )
    # komponenta, která falešná GO nerozlišuje
    if fps and tp:
        names = {n for c in fps for n in c.components}
        gaps = []
        for name in names:
            fp_v = mean(c.components[name] for c in fps if name in c.components)
            tp_v = mean(c.components[name] for c in tp if name in c.components)
            gaps.append((tp_v - fp_v, name, fp_v, tp_v))
        if gaps:
            gap, name, fp_v, tp_v = min(gaps)
            if fp_v >= 0.6 and gap < 0.1:
                recs.append(
                    f"Komponenta `{name}` je u falešných GO vysoká ({fp_v:.2f} vs. {tp_v:.2f} "
                    "u správných) – nerozlišuje, zvaž snížení její váhy nebo přísnější křivku."
                )
    return recs


def evaluate(
    rows: list[dict], location: str, kind: str, go_threshold: float
) -> Evaluation:
    """`rows` = řádky pohledu `calibration` (jedna lokalita)."""
    cases: list[Case] = []
    skipped = 0
    for r in rows:
        actual = OUTCOME_TO_VERDICT.get(r["outcome"])
        if r["score"] is None or r["verdict"] is None or actual is None:
            skipped += 1
            continue
        comps = {c["name"]: c["value"] for c in _load(r.get("components_json")) or []}
        cases.append(
            Case(
                date=r["target_date"],
                score=float(r["score"]),
                predicted=r["verdict"],
                actual=actual,
                outcome=r["outcome"],
                notes=r.get("notes"),
                features=_load(r.get("features_json")) or {},
                components=comps,
            )
        )
    cases.sort(key=lambda c: c.date)
    confusion = {a: dict.fromkeys(VERDICTS, 0) for a in VERDICTS}  # [skutečnost][predikce]
    for c in cases:
        confusion[c.actual][c.predicted] += 1
    go = _prf(
        sum(c.predicted == "GO" and c.actual == "GO" for c in cases),
        sum(c.predicted == "GO" and c.actual != "GO" for c in cases),
        sum(c.predicted != "GO" and c.actual == "GO" for c in cases),
    )
    no_go = _prf(
        sum(c.predicted == "NO_GO" and c.actual == "NO_GO" for c in cases),
        sum(c.predicted == "NO_GO" and c.actual != "NO_GO" for c in cases),
        sum(c.predicted != "NO_GO" and c.actual == "NO_GO" for c in cases),
    )
    ev = Evaluation(
        location=location,
        kind=kind,
        total_rated=len(rows),
        cases=cases,
        skipped_no_prediction=skipped,
        confusion=confusion,
        go=go,
        no_go=no_go,
        false_positives=[c for c in cases if c.predicted == "GO" and c.actual != "GO"],
        false_negatives=[c for c in cases if c.predicted != "GO" and c.actual == "GO"],
    )
    ev.recommendations = _recommend(ev, go_threshold)
    return ev
