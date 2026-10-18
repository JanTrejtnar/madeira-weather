from __future__ import annotations

import csv
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from madeira.domain.config import load_env, load_settings
from madeira.domain.models import OUTCOME_LABELS
from madeira.presenters.cli import render_comparison, render_sunrise, render_week
from madeira.services import evaluation, forecast, story_image, webexport
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


@app.command()
def rate(
    day: str = typer.Argument(..., help="today | tomorrow | YYYY-MM-DD"),
    outcome: str = typer.Argument(..., help="/".join(OUTCOME_LABELS)),
) -> None:
    """Zapíše, jak východ dopadl. Chybí-li k dni predikce, dopočítá ji z historických dat."""
    if outcome not in OUTCOME_LABELS:
        raise typer.BadParameter(f"Možnosti: {', '.join(OUTCOME_LABELS)}")
    try:
        result = forecast.record_outcome(_day(day), outcome, load_settings(), Store())
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None
    if result.backfilled:
        console.print(
            f"Predikce chyběla, dopočítána z historických dat: {result.analysis.score_pct:.0f} % "
            f"({result.analysis.verdict})"
        )
    if result.warning:
        console.print(f"[yellow]{result.warning}[/]")
    console.print(f"Zapsáno: {OUTCOME_LABELS[outcome]}")


LOCATION_HELP = "pico_arieiro | pico_ruivo | bica_da_cana | ponta_do_rosto | ponta_do_pargo | cristo_rei"


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


def _normalize_result(value: str) -> str:
    """GO / MAYBE / NO_GO (i NO-GO, malými písmeny) nebo klíč z OUTCOME_LABELS."""
    v = value.strip()
    up = v.upper().replace("-", "_").replace(" ", "_")
    if up in ("GO", "MAYBE", "NO_GO"):
        return up
    if v.lower() in OUTCOME_LABELS:
        return v.lower()
    raise ValueError(
        f"Neplatný výsledek '{value}'. Možnosti: GO, MAYBE, NO_GO, {', '.join(OUTCOME_LABELS)}"
    )


@app.command()
def history(
    limit: int = typer.Option(30, help="Počet záznamů"),
    location: str = typer.Option("pico_arieiro", "--location", "-l", help=LOCATION_HELP),
) -> None:
    """Hodnocená období: predikce vs. skutečnost (podklad pro kalibraci vah)."""
    key = _one_location(location)
    rows = Store().calibration_rows(limit, location=key)
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
    settings, store = load_settings(), Store()
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


@app.command()
def label(
    location: str = typer.Option(..., "--location", "-l", help=LOCATION_HELP),
    day: str = typer.Option(..., "--date", "-d", help="YYYY-MM-DD (nebo today)"),
    result: str = typer.Option(
        ..., "--result", "-r", help="GO | MAYBE | NO_GO (nebo " + "/".join(OUTCOME_LABELS) + ")"
    ),
    notes: str = typer.Option("", "--notes", "-n", help="Poznámka"),
) -> None:
    """Rychlé zadání ground truth pro lokalitu a den. Chybí-li predikce, dopočítá se z historie."""
    key = _one_location(location)
    try:
        outcome = _normalize_result(result)
        res = forecast.record_outcome(
            _day(day), outcome, load_settings(), Store(), location=key, notes=notes or None
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from None
    if res.backfilled:
        console.print(
            f"Predikce chyběla, dopočítána z historických dat: {res.analysis.score_pct:.0f} % "
            f"({res.analysis.verdict})"
        )
    if res.warning:
        console.print(f"[yellow]{res.warning}[/]")
    console.print(f"Zapsáno: {key} {day} → {outcome}")


@app.command("import-labels")
def import_labels(
    file: Path = typer.Argument(
        ..., exists=True, dir_okay=False, help="CSV: date,location,actual_verdict,notes"
    ),
) -> None:
    """Hromadný import ground truth z CSV (hlavička: date,location,actual_verdict,notes)."""
    store, ok, errors = Store(), 0, []
    with file.open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        missing = {"date", "location", "actual_verdict"} - set(reader.fieldnames or [])
        if missing:
            raise typer.BadParameter(f"V CSV chybí sloupce: {', '.join(sorted(missing))}")
        for n, row in enumerate(reader, start=2):
            try:
                key = _one_location((row["location"] or "").strip())
                day = forecast.parse_day((row["date"] or "").strip())
                if day > forecast.today_local():
                    raise ValueError("datum v budoucnosti")
                store.set_outcome(
                    day,
                    _normalize_result(row["actual_verdict"] or ""),
                    location=key,
                    notes=(row.get("notes") or "").strip() or None,
                )
                ok += 1
            except (ValueError, typer.BadParameter) as exc:
                errors.append(f"řádek {n}: {exc}")
    console.print(f"Importováno: {ok}")
    for e in errors:
        console.print(f"[yellow]{e}[/]")
    if ok:
        console.print("Chybějící predikce doplní `madeira fetch-history`.")
    if errors:
        raise typer.Exit(1)


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
    rows = Store().calibration_rows(100_000, location=key)
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
    rows = Store().calibration_rows(limit=100_000)
    for r in rows:
        for key in ("features_json", "components_json", "gates_json", "weights_json"):
            if r.get(key):
                r[key] = json.loads(r[key])
    sys.stdout.write(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")


@app.command("export-web")
def export_web(
    out: Path = typer.Option(Path("docs/data.json"), "--out", "-o", help="Cílový JSON"),
    days: int = typer.Option(3, min=1, max=7, help="Počet dní výhledu (od zítřka)"),
    history: int = typer.Option(60, min=0, help="Kolik hodnocených dní historie přibalit"),
) -> None:
    """Uloží zítřek a krátký výhled do statického JSONu pro GitHub Pages."""
    store = Store()
    settings = load_settings()
    spot_history = webexport.collect_spot_history(settings, store, forecast.today_local(), previous=out)
    payload = webexport.build_payload(settings, days, store=store, spot_history=spot_history)
    # Historie z lokální DB; bez ní (např. GitHub Actions) zůstane z předchozího exportu.
    rows = store.history_rows(forecast.today_local(), limit=history)
    payload["history"] = webexport.history_entries(rows, 1818) if rows else webexport.read_history(out)
    payload["history"] = webexport.with_snapshot_links(payload["history"], datetime.now(UTC).date())
    webexport.write_payload(payload, out)
    t = payload["tomorrow"]
    console.print(f"Zapsáno {out}: {t['date']} {t['verdict']} {t['score_pct']} %")
    story = story_image.write_story(payload, out.parent / "story-latest.png")
    og = out.parent / "og-preview.png"
    story_image.write_og(og)
    console.print(f"Obrázky: {story}, {og}")
    spots = payload["tomorrow_spots"]
    for part, label in (("morning", "východ"), ("evening", "západ")):
        for s in spots[part]:
            console.print(f"  {label} {s['name']}: {s['verdict']} {s['score_pct']} %")


@app.command()
def bot() -> None:
    """Spustí Telegram bota (long polling + denní digest)."""
    from madeira.bot.app import build_application

    load_env()
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        console.print("[red]Chybí TELEGRAM_BOT_TOKEN (proměnná prostředí nebo .env).[/]")
        raise typer.Exit(1)
    if not os.environ.get("MADEIRA_ALLOWED_CHATS"):
        console.print(
            "[yellow]MADEIRA_ALLOWED_CHATS není nastaveno: bot odpoví komukoli, kdo ho najde.[/]"
        )
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s", level=logging.INFO
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)  # URL s tokenem nepatří do logu
    application = build_application(token, Store(), load_settings())
    console.print(f"Bot běží ({datetime.now(UTC):%H:%M} UTC), Ctrl+C ukončí.")
    application.run_polling()


if __name__ == "__main__":
    app()
