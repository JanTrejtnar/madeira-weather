from __future__ import annotations

from datetime import datetime
from statistics import mean

from madeira.analyzers.features import window_values
from madeira.analyzers.scoring_utils import pw
from madeira.domain.models import ForecastBundle, MicroclimateComparison, MicroclimateRow

PRECIP_MM = [(0.0, 1.0), (0.5, 0.6), (2.0, 0.0)]
WIND_GUST = [(30, 1.0), (55, 0.5), (80, 0.0)]
TEMP_COMFORT = [(8, 0.0), (18, 1.0), (26, 1.0), (33, 0.3)]


def _avg(pts, getter, start, end) -> float:
    vals = window_values(pts, getter, start, end, step_min=60)
    return mean(vals) if vals else 0.0


def compare_microclimates(
    bundle: ForecastBundle, start: datetime, end: datetime
) -> MicroclimateComparison:
    rows: list[MicroclimateRow] = []
    for key, fc in bundle.forecasts.items():
        if fc.location.role.startswith("reference_") or fc.location.role.startswith("spot_"):
            continue
        pts = fc.hourly
        total = _avg(pts, lambda p: p.cloud_cover_total_pct, start, end)
        precip = window_values(pts, lambda p: p.precipitation_mm, start, end, step_min=60)
        gusts = window_values(pts, lambda p: p.wind_gusts_kmh, start, end, step_min=60)
        temp = _avg(pts, lambda p: p.apparent_temperature_c, start, end)
        precip_max = max(precip) if precip else 0.0
        gust_max = max(gusts) if gusts else 0.0
        sky = 100 * (
            0.45 * (1 - total / 100)
            + 0.25 * pw(precip_max, PRECIP_MM)
            + 0.15 * pw(gust_max, WIND_GUST)
            + 0.15 * pw(temp, TEMP_COMFORT)
        )
        note = ""
        if fc.location.role in ("summit", "plateau"):
            note = "oblačnost může být pod vámi"
        rows.append(
            MicroclimateRow(
                location_key=key,
                name=fc.location.name,
                role=fc.location.role,
                cloud_low_pct=round(_avg(pts, lambda p: p.cloud_cover_low_pct, start, end)),
                cloud_mid_pct=round(_avg(pts, lambda p: p.cloud_cover_mid_pct, start, end)),
                cloud_high_pct=round(_avg(pts, lambda p: p.cloud_cover_high_pct, start, end)),
                cloud_total_pct=round(total),
                precip_max_mm=round(precip_max, 1),
                temp_avg_c=round(_avg(pts, lambda p: p.temperature_c, start, end), 1),
                gust_max_kmh=round(gust_max),
                sky_score_pct=round(sky, 1),
                note=note,
            )
        )
    rows.sort(key=lambda r: r.sky_score_pct, reverse=True)
    for i, r in enumerate(rows, 1):
        r.rank = i
    return MicroclimateComparison(
        window_start_utc=start,
        window_end_utc=end,
        rows=rows,
        best_location=rows[0].location_key,
        narrative_cs=_narrative(rows),
    )


def _narrative(rows: list[MicroclimateRow]) -> str:
    by = {r.location_key: r for r in rows}
    best, worst = rows[0], rows[-1]
    text = (
        f"Nejlépe vychází {best.name} (Sky-Score {best.sky_score_pct:.0f} %), "
        f"nejhůř {worst.name} ({worst.sky_score_pct:.0f} %)."
    )
    north, south = by.get("sao_vicente"), by.get("funchal")
    if north and south and north.cloud_total_pct - south.cloud_total_pct >= 30:
        text += " Typický pasátový obraz: sever je zataženější než jih."
    elif north and south and south.cloud_total_pct - north.cloud_total_pct >= 30:
        text += " Neobvyklé: jih je zataženější než sever."
    return text
