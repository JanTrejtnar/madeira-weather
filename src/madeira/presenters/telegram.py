"""Formátování zpráv pro Telegram (HTML parse mode)."""

from __future__ import annotations

from datetime import timedelta
from html import escape

from madeira.domain.models import MicroclimateComparison, SunriseAnalysis
from madeira.presenters.cli import COMP_CS, CONF_CS, DAYS_CS, TZ, VERDICT_CS

VERDICT_ICON = {"GO": "✅", "MAYBE": "🤔", "NO_GO": "❌"}


def _day(a: SunriseAnalysis) -> str:
    return f"{DAYS_CS[a.target_date.weekday()]} {a.target_date:%d.%m.}"


def format_sunrise(a: SunriseAnalysis) -> str:
    sunrise = a.sunrise_utc.astimezone(TZ)
    lo, hi = a.score_range
    lines = [
        f"🌅 <b>Východ slunce {_day(a)} v {sunrise:%H:%M}</b> (Pico do Arieiro)",
        f"{VERDICT_ICON[a.verdict]} <b>{VERDICT_CS[a.verdict]}</b> · skóre <b>{a.score_pct:.0f} %</b> "
        f"({lo:.0f}–{hi:.0f} %, důvěra {CONF_CS[a.confidence]})",
        f"Inverze / moře mraků: {a.inversion.probability_pct:.0f} %",
        "",
        escape(a.summary_cs),
        "",
    ]
    lines += [
        f"• {COMP_CS.get(c.name, c.name)}: {c.value * 100:.0f} % – {escape(c.explanation)}"
        for c in a.components
    ]
    lines += [f"⚠️ {escape(w)}" for w in a.warnings]
    return "\n".join(lines)


def format_week(analyses: list[SunriseAnalysis]) -> str:
    rows = [
        f"{_day(a)} {a.sunrise_utc.astimezone(TZ):%H:%M} {VERDICT_ICON[a.verdict]} "
        f"{a.score_pct:>3.0f} %  inv {a.inversion.probability_pct:>3.0f} %  {CONF_CS[a.confidence]}"
        for a in analyses
    ]
    return "📅 <b>Výhled východů slunce</b>\n<pre>" + escape("\n".join(rows)) + "</pre>"


def format_comparison(c: MicroclimateComparison) -> str:
    s, e = c.window_start_utc.astimezone(TZ), c.window_end_utc.astimezone(TZ)
    rows = [
        f"{r.rank}. {r.name + ('*' if r.note else ''):<15} {r.sky_score_pct:>3.0f} %  "
        f"mraky {r.cloud_total_pct:>3} %  {r.temp_avg_c:>3.0f} °C  vítr {r.gust_max_kmh:>3}"
        for r in c.rows
    ]
    note = "\n* oblačnost může být pod vámi (moře mraků)" if any(r.note for r in c.rows) else ""
    return (
        f"🗺 <b>Mikroklima {s:%d.%m. %H:%M}–{e:%H:%M}</b>\n<pre>"
        + escape("\n".join(rows))
        + "</pre>"
        + escape(note)
        + f"\n{escape(c.narrative_cs)}"
    )


def format_digest(
    tomorrow: SunriseAnalysis, week: list[SunriseAnalysis], departure_before_min: int
) -> str:
    sunrise = tomorrow.sunrise_utc.astimezone(TZ)
    if tomorrow.verdict == "NO_GO":
        advice = "😴 Zítra klidně spi."
    else:
        leave = sunrise - timedelta(minutes=departure_before_min)
        advice = f"⏰ Odjezd z Funchalu cca <b>{leave:%H:%M}</b> (východ {sunrise:%H:%M})."
    later = [a for a in week if a.target_date != tomorrow.target_date]
    outlook = ""
    if later:
        outlook = (
            "\n\n<b>Další dny:</b>\n<pre>"
            + escape(
                "\n".join(
                    f"{_day(a)} {VERDICT_ICON[a.verdict]} {a.score_pct:>3.0f} %" for a in later
                )
            )
            + "</pre>"
        )
    return f"🌙 <b>Večerní digest</b>\n{advice}\n\n{format_sunrise(tomorrow)}{outlook}"
