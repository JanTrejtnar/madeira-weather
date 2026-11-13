from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from madeira.domain.config import load_settings
from madeira.domain.models import OUTCOME_LABELS
from madeira.fetchers.open_meteo import OpenMeteoFetcher
from madeira.presenters.cli import render_comparison, render_sunrise, render_week
from madeira.services import (
    backtest,
    evaluation,
    forecast,
    forecast_log,
    mapdata,
    ratings,
    story_image,
    story_renderer,
    webexport,
)
from madeira.storage.db import Store

app = typer.Typer(help="Madeira Microclimate & Sunrise Predictor", no_args_is_help=True)
for _stream in (sys.stdout, sys.stderr):  # Windows konzole bývá cp1252
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8")
console = Console(legacy_windows=False)


def _day(value: str):
    try:
        return forecast.parse_day(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None


@app.command()
def sunrise(
    day: str = typer.Option("tomorrow", "--date", "-d", help="today | tomorrow | YYYY-MM-DD"),
    log: bool = typer.Option(True, help="Zapsat predikci do SQLite"),
) -> None:
    """Skóre východu slunce na Pico do Arieiro."""
    analysis = forecast.predict(_day(day), load_settings(), Store() if log else None)
    render_sunrise(analysis, console)


@app.command()
def week(log: bool = typer.Option(True, help="Zapsat predikce do SQLite")) -> None:
    """Skóre východu slunce na dalších 7 dní."""
    render_week(forecast.predict_week(load_settings(), Store() if log else None), console)


@app.command()
def compare(
    day: str = typer.Option("tomorrow", "--date", "-d"),
    from_: str = typer.Option("13:00", "--from"),
    to: str = typer.Option("18:00", "--to"),
) -> None:
    """Srovnání mikroklimat lokalit pro časové okno (místní čas)."""
    render_comparison(forecast.compare(_day(day), from_, to), console)


LOCATION_HELP = "pico_arieiro | pico_ruivo | bica_da_cana | ponta_do_rosto | ponta_do_pargo | cristo_rei | arieiro_sunset | pico_ruivo_sunset"


def _locations(value: str) -> list[str]:
    try:
        return forecast.resolve_locations(value)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None


def _one_location(value: str) -> str:
    keys = _locations(value)
    if len(keys) != 1:
        raise typer.BadParameter("Zadej jednu lokalitu: " + LOCATION_HELP)
    return keys[0]


@app.command()
def rate(
    spot: str = typer.Argument(..., help=LOCATION_HELP),
    day: str = typer.Argument(..., help="today | yesterday | YYYY-MM-DD"),
    verdict: str = typer.Argument(..., help="GO | MAYBE | NO_GO (stálo to za cestu?)"),
    note: str = typer.Option("", "--note", "-n", help="Poznámka (co bylo vidět)"),
    event: str = typer.Option(
        "", "--event", "-e", help="sunrise | sunset; jen u spotů, které model nezná"
    ),
    fetch: bool = typer.Option(True, help="Uložit archivní data dne do zlaté sady (tests/fixtures/golden)"),
) -> None:
    """Admin: zapíše skutečný výsledek do ground_truth/ratings.json (a přidá den do zlaté sady).

    Soubor pak commitni; export webu z něj plní historii („Skutečnost“) a backtest z něj měří model."""
    settings = load_settings()
    d = _day(day)
    try:
        try:
            key = _one_location(spot)
        except typer.BadParameter:  # spot bez modelu (např. bica_da_cana_sunset): jen záznam, vyžaduje --event
            key = spot
        entry = ratings.rate(
            d, key, verdict, note, event or None, settings, today=forecast.today_local()
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None
    console.print(f"Zapsáno: {key} {d} → {entry['verdict']} ({entry['event']})")
    if fetch and ratings.modeled(key, entry, settings) and not backtest.fixture_path(d).exists():
        try:
            fetcher = OpenMeteoFetcher()
            backtest.save_fixture(
                d, fetcher.fetch_historical_payload(forecast.horizon_locations(d), d, d)
            )
            console.print(f"Archivní data dne uložena: {backtest.fixture_path(d)}")
        except RuntimeError as exc:
            console.print(f"[yellow]Archivní data se nepodařilo stáhnout ({exc}); doplň je `python3 scripts/backtest.py --fetch`.[/]")
    console.print("Commitni ground_truth/ratings.json (a nový soubor v tests/fixtures/golden/).")


@app.command()
def history(
    limit: int = typer.Option(30, help="Počet záznamů"),
    location: str = typer.Option("pico_arieiro", "--location", "-l", help=LOCATION_HELP),
) -> None:
    """Hodnocená období: predikce vs. skutečnost (podklad pro kalibraci vah)."""
    key = _one_location(location)
    rows = _rated_store().calibration_rows(limit, location=key)
    t = Table(
        "Datum",
        "Predikce",
        "Verdikt",
        "Inverze",
        "Skutečnost",
        "Zdroj",
        title=f"Predikce vs. realita – {key}",
    )
    for r in rows:
        t.add_row(
            r["target_date"],
            "-" if r["score"] is None else f"{r['score']:.0f} %",
            r["verdict"] or "-",
            "-" if r["inversion_pct"] is None else f"{r['inversion_pct']:.0f} %",
            OUTCOME_LABELS.get(r["outcome"], r["outcome"]),
            "zpětně"
            if r["model"] == "historical_forecast"
            else ("předpověď" if r["model"] else "-"),
        )
    console.print(t)


@app.command()
def recompute(
    location: str = typer.Option("all", "--location", "-l", help=LOCATION_HELP + " | all"),
) -> None:
    """Po úpravě vah/heuristiky přepočítá zpětné predikce všech hodnocených dnů."""
    settings, store = load_settings(), _rated_store()
    for key in _locations(location):
        results = forecast.recompute_backfilled(settings, store, key)
        console.print(f"{key}: přepočítáno dnů: {len(results)}")
        if results:
            history(limit=len(results) + 10, location=key)


@app.command("fetch-history")
def fetch_history(
    location: str = typer.Option("all", "--location", "-l", help=LOCATION_HELP + " | all"),
    days: int = typer.Option(90, min=1, max=365, help="Kolik posledních dní (doporučeno 60–90)"),
) -> None:
    """Stáhne archivované předpovědi (Open-Meteo Historical Forecast) a uloží zpětné predikce."""
    keys = _locations(location)
    with console.status("Stahuji historická data z Open-Meteo…"):
        try:
            counts = forecast.fetch_history(load_settings(), Store(), keys, days)
        except RuntimeError as exc:
            console.print(f"[red]{exc}[/]")
            raise typer.Exit(1) from None
    for key, n in counts.items():
        console.print(f"{key}: uloženo {n} dní")


def _rated_store() -> Store:
    """Cache predikcí s hodnoceními z ground_truth/ratings.json (jediný zdroj pravdy o realitě)."""
    store = Store()
    ratings.sync_to_store(store)
    return store


def _pct(v: float | None) -> str:
    return "–" if v is None else f"{v * 100:.0f} %"


@app.command()
def evaluate(
    location: str = typer.Option("pico_arieiro", "--location", "-l", help=LOCATION_HELP),
) -> None:
    """Přesnost modelu vs. ground truth: precision/recall, falešná GO/NO-GO a doporučení prahů."""
    key = _one_location(location)
    settings = load_settings()
    kind = "sunset" if key in forecast.SUNSET_SPOTS else "sunrise"
    go_thr = (settings.sunset if kind == "sunset" else settings.sunrise).verdict.go
    rows = _rated_store().calibration_rows(100_000, location=key)
    ev = evaluation.evaluate(rows, key, kind, go_thr)
    console.print(
        f"[bold]{key}[/] ({'západ' if kind == 'sunset' else 'východ'}): "
        f"{len(ev.cases)} vyhodnocených dnů z {ev.total_rated} hodnocených"
    )
    if ev.skipped_no_prediction:
        console.print(
            f"[yellow]{ev.skipped_no_prediction} hodnocení bez predikce – spusť "
            f"`madeira fetch-history --location {location}`.[/]"
        )
    if not ev.cases:
        for r in ev.recommendations:
            console.print(r)
        return
    t = Table("", "TP", "FP", "FN", "Precision", "Recall", "F1", title="Přesnost")
    for name, m in (("GO", ev.go), ("NO-GO", ev.no_go)):
        t.add_row(
            name, str(m["tp"]), str(m["fp"]), str(m["fn"]),
            _pct(m["precision"]), _pct(m["recall"]), _pct(m["f1"]),
        )
    console.print(t)
    cm = Table("skutečnost / predikce", *evaluation.VERDICTS, title="Matice záměn")
    for actual in evaluation.VERDICTS:
        cm.add_row(actual, *(str(ev.confusion[actual][p]) for p in evaluation.VERDICTS))
    console.print(cm)

    def listing(title: str, cases: list) -> None:
        if not cases:
            return
        lt = Table("Datum", "Skóre", "Predikce", "Skutečnost", "Poznámka", title=title)
        for c in cases:
            lt.add_row(c.date, f"{c.score:.0f} %", c.predicted, c.actual, c.notes or "")
        console.print(lt)

    listing("Falešná GO (model hlásil GO, realita horší)", ev.false_positives)
    listing("Zmeškaná GO (model nehlásil GO, realita super)", ev.false_negatives)
    console.print("[bold]Doporučení[/]")
    for r in ev.recommendations:
        console.print(f"• {r}")


@app.command("export-calibration")
def export_calibration() -> None:
    """Vypíše kalibrační data (predikce + features + realita) jako JSON na stdout."""
    rows = _rated_store().calibration_rows(limit=100_000)
    for r in rows:
        for key in ("features_json", "components_json", "gates_json", "weights_json"):
            if r.get(key):
                r[key] = json.loads(r[key])
    sys.stdout.write(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")


@app.command("export-web")
def export_web(
    out: Path = typer.Option(  # noqa: B008
        Path("docs/data.json"), "--out", "-o", help="Cílový JSON"),
    days: int = typer.Option(3, min=1, max=7, help="Počet dní výhledu (od zítřka)"),
    history: int = typer.Option(60, min=0, help="Kolik hodnocených dní historie přibalit"),
    log_forecast: bool = typer.Option(
        False, "--log-forecast", help="Zapsat předpovědi do ground_truth/forecast_log.jsonl (jen pipeline v Actions)"
    ),
) -> None:
    """Uloží zítřek a krátký výhled do statického JSONu pro GitHub Pages."""
    settings = load_settings()
    store = _rated_store()  # hodnocení z ground_truth/ratings.json (v Actions je DB prázdná)
    spot_history = webexport.collect_spot_history(settings, store, forecast.today_local(), previous=out)
    payload = webexport.build_payload(settings, days, store=store, spot_history=spot_history)
    # Historie z lokální DB; bez ní (např. GitHub Actions) zůstane z předchozího exportu.
    today = forecast.today_local()
    rows = store.history_rows(today - timedelta(days=1), limit=history)  # historie končí včerejškem
    payload["history"] = webexport.history_entries(rows, 1818) if rows else webexport.read_history(out)
    payload["history"] = webexport.with_snapshot_links(payload["history"], today)
    webexport.write_payload(payload, out)
    if log_forecast:
        console.print(f"Log předpovědí: {forecast_log.record(payload)} řádků")
    t = payload["tomorrow"]
    console.print(f"Zapsáno {out}: {t['date']} {t['verdict']} {t['score_pct']} %")
    story = story_renderer.write_story(payload, out.parent / "story-latest.png")
    og = out.parent / "og-preview.png"
    story_image.write_og(og)
    console.print(f"Obrázky: {story}, {og}")
    spots = payload["tomorrow_spots"]
    for part, label in (("morning", "východ"), ("evening", "západ")):
        for s in spots[part]:
            console.print(f"  {label} {s['name']}: {s['verdict']} {s['score_pct']} %")


@app.command("export-map")
def export_map(
    out: Path = typer.Option(  # noqa: B008
        Path("docs/map_data.json"), "--out", "-o", help="Cílový JSON"),
) -> None:
    """Hodinová předpověď (48 h) pro body mapy docs/map.html."""
    payload = mapdata.build_map_data()
    mapdata.write_map_data(payload, out)
    console.print(f"Zapsáno {out}: {len(payload['spots'])} bodů × {len(payload['timestamps'])} hodin")


if __name__ == "__main__":
    app()
