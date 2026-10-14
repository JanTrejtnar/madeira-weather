from __future__ import annotations

from zoneinfo import ZoneInfo

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from madeira.domain.models import MicroclimateComparison, SunriseAnalysis

TZ = ZoneInfo("Atlantic/Madeira")
VERDICT_STYLE = {"GO": "bold green", "MAYBE": "bold yellow", "NO_GO": "bold red"}
VERDICT_CS = {"GO": "GO", "MAYBE": "MOŽNÁ", "NO_GO": "NO-GO"}
DAYS_CS = ["Po", "Út", "St", "Čt", "Pá", "So", "Ne"]
CONF_CS = {"LOW": "nízká", "MEDIUM": "střední", "HIGH": "vysoká"}
COMP_CS = {
    "sky": "Obloha",
    "color": "Barvy",
    "visibility": "Dohlednost",
    "wind": "Vítr",
    "cloudsea": "Moře mraků",
}


def render_sunrise(a: SunriseAnalysis, console: Console) -> None:
    sunrise = a.sunrise_utc.astimezone(TZ)
    style = VERDICT_STYLE[a.verdict]
    title = f"Východ slunce {a.target_date:%d.%m.%Y} v {sunrise:%H:%M} (Pico do Arieiro)"
    lo, hi = a.score_range
    body = (
        f"[{style}]{VERDICT_CS[a.verdict]}[/]  skóre [bold]{a.score_pct:.0f} %[/] "
        f"(rozptyl {lo:.0f}–{hi:.0f} %, důvěra {CONF_CS[a.confidence]})\n"
        f"Inverze / moře mraků: {a.inversion.probability_pct:.0f} %\n\n{a.summary_cs}"
    )
    console.print(Panel(body, title=title, expand=False))
    t = Table("Komponenta", "Hodnota", "Váha", "Body", "Detail", show_edge=False)
    for c in a.components:
        t.add_row(
            COMP_CS.get(c.name, c.name),
            f"{c.value * 100:.0f} %",
            f"{c.weight:.2f}",
            f"{c.contribution:.1f}",
            c.explanation,
        )
    console.print(t)
    gates = ", ".join(f"{k} ×{v}" for k, v in a.gates.items() if v < 1) or "žádné"
    console.print(f"Omezení (gates): {gates}")
    for w in a.warnings:
        console.print(f"[yellow]! {w}[/]")


def render_week(analyses: list[SunriseAnalysis], console: Console) -> None:
    t = Table("Datum", "Východ", "Skóre", "Verdikt", "Inverze", "Důvěra", title="7denní výhled")
    for a in analyses:
        s = VERDICT_STYLE[a.verdict]
        t.add_row(
            f"{DAYS_CS[a.target_date.weekday()]} {a.target_date:%d.%m.}",
            f"{a.sunrise_utc.astimezone(TZ):%H:%M}",
            f"{a.score_pct:.0f} %",
            f"[{s}]{VERDICT_CS[a.verdict]}[/]",
            f"{a.inversion.probability_pct:.0f} %",
            CONF_CS[a.confidence],
        )
    console.print(t)


def render_comparison(c: MicroclimateComparison, console: Console) -> None:
    s, e = c.window_start_utc.astimezone(TZ), c.window_end_utc.astimezone(TZ)
    t = Table(
        "#",
        "Lokalita",
        "Mraky n/s/v",
        "Celk.",
        "Srážky",
        "Teplota",
        "Nárazy",
        "Sky",
        title=f"Mikroklima {s:%d.%m. %H:%M}–{e:%H:%M}",
    )
    notes = False
    for r in c.rows:
        mark = "*" if r.note else ""
        notes = notes or bool(r.note)
        t.add_row(
            str(r.rank),
            r.name + mark,
            f"{r.cloud_low_pct}/{r.cloud_mid_pct}/{r.cloud_high_pct} %",
            f"{r.cloud_total_pct} %",
            f"{r.precip_max_mm} mm",
            f"{r.temp_avg_c:.0f} °C",
            f"{r.gust_max_kmh} km/h",
            f"[bold]{r.sky_score_pct:.0f} %[/]",
        )
    console.print(t)
    if notes:
        console.print("* oblačnost může být pod vámi (moře mraků), skóre je tam konzervativní")
    console.print(c.narrative_cs)
