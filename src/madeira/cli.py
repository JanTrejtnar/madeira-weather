from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime

import typer
from rich.console import Console
from rich.table import Table

from madeira.domain.config import load_env, load_settings
from madeira.domain.models import OUTCOME_LABELS
from madeira.presenters.cli import render_comparison, render_sunrise, render_week
from madeira.services import forecast
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


@app.command()
def history(limit: int = typer.Option(30, help="Počet záznamů")) -> None:
    """Hodnocená období: predikce vs. skutečnost (podklad pro kalibraci vah)."""
    rows = Store().calibration_rows(limit)
    t = Table(
        "Datum",
        "Predikce",
        "Verdikt",
        "Inverze",
        "Skutečnost",
        "Zdroj",
        title="Predikce vs. realita",
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
def recompute() -> None:
    """Po úpravě vah/heuristiky přepočítá zpětné predikce všech hodnocených dnů."""
    results = forecast.recompute_backfilled(load_settings(), Store())
    console.print(f"Přepočítáno dnů: {len(results)}")
    history(limit=len(results) + 10)


@app.command("export-calibration")
def export_calibration() -> None:
    """Vypíše kalibrační data (predikce + features + realita) jako JSON na stdout."""
    rows = Store().calibration_rows(limit=100_000)
    for r in rows:
        for key in ("features_json", "components_json", "gates_json", "weights_json"):
            if r.get(key):
                r[key] = json.loads(r[key])
    sys.stdout.write(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")


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
