from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class Location(BaseModel):
    key: str
    name: str
    lat: float
    lon: float
    elevation_m: float
    role: Literal[
        "coast_south",
        "coast_north",
        "summit",
        "plateau",
        "reference_east",
        "reference_west",
        "spot_sunrise",
        "spot_sunset",
        "coast_escape",
    ]


class PressureLevel(BaseModel):
    temperature_c: float | None = None
    relative_humidity_pct: float | None = None
    geopotential_height_m: float | None = None


class HourlyPoint(BaseModel):
    time_utc: datetime
    temperature_c: float | None = None
    apparent_temperature_c: float | None = None
    dew_point_c: float | None = None
    relative_humidity_pct: float | None = None
    cloud_cover_total_pct: float | None = None
    cloud_cover_low_pct: float | None = None
    cloud_cover_mid_pct: float | None = None
    cloud_cover_high_pct: float | None = None
    precipitation_mm: float | None = None
    precipitation_probability_pct: float | None = None
    wind_speed_kmh: float | None = None
    wind_gusts_kmh: float | None = None
    wind_direction_deg: float | None = None
    visibility_m: float | None = None
    pressure_msl_hpa: float | None = None
    direct_radiation_wm2: float | None = None
    sunshine_duration_s: float | None = None  # sekundy slunce v dané hodině (0–3600)
    levels: dict[int, PressureLevel] = Field(default_factory=dict)


class DailyInfo(BaseModel):
    date: date
    sunrise_utc: datetime
    sunset_utc: datetime


class LocationForecast(BaseModel):
    location: Location
    hourly: list[HourlyPoint]
    daily: list[DailyInfo]


class ForecastBundle(BaseModel):
    model: str
    fetched_at_utc: datetime
    forecasts: dict[str, LocationForecast]


class ScoreComponent(BaseModel):
    name: str
    value: float  # 0..1
    weight: float  # normalizovaná váha
    contribution: float  # body do skóre (0..100)
    explanation: str


class InversionAnalysis(BaseModel):
    probability_pct: float
    cloud_deck_top_m: float | None
    summit_above_deck_m: float | None
    evidence: dict[str, float]
    description_cs: str


class SunriseAnalysis(BaseModel):
    target_date: date
    sunrise_utc: datetime
    score_pct: float
    score_range: tuple[float, float]
    verdict: Literal["GO", "MAYBE", "NO_GO"]
    confidence: Literal["LOW", "MEDIUM", "HIGH"]
    inversion: InversionAnalysis
    components: list[ScoreComponent]
    gates: dict[str, float]
    summary_cs: str
    warnings: list[str]


class SunsetAnalysis(BaseModel):
    target_date: date
    sunset_utc: datetime
    score_pct: float
    score_range: tuple[float, float]
    verdict: Literal["GO", "MAYBE", "NO_GO"]
    confidence: Literal["LOW", "MEDIUM", "HIGH"]
    components: list[ScoreComponent]
    gates: dict[str, float]
    summary_cs: str
    warnings: list[str]


class MicroclimateRow(BaseModel):
    location_key: str
    name: str
    role: str
    cloud_low_pct: int
    cloud_mid_pct: int
    cloud_high_pct: int
    cloud_total_pct: int
    precip_max_mm: float
    temp_avg_c: float
    gust_max_kmh: int
    sky_score_pct: float
    rank: int = 0
    note: str = ""


class MicroclimateComparison(BaseModel):
    window_start_utc: datetime
    window_end_utc: datetime
    rows: list[MicroclimateRow]
    best_location: str
    narrative_cs: str


# Zpětná vazba z chatu: klíč → popisek tlačítka (kalibrační "ground truth")
OUTCOME_LABELS = {
    "cloud_sea": "🌊 Nádherná inverze",
    "clear": "☀️ Jasno bez mraků",
    "mixed": "⛅ Částečně zataženo",
    "fail": "🌫 V mlze / nepovedlo se",
}

# Ground truth lze zadat i rovnou jako verdikt (hlavně pro západy slunce, kde "inverze" nedává smysl).
VERDICT_OUTCOMES = ("GO", "MAYBE", "NO_GO")
VALID_OUTCOMES = (*OUTCOME_LABELS, *VERDICT_OUTCOMES)
# Jak se hodnocení převádí na "skutečný verdikt" při vyhodnocení přesnosti modelu.
OUTCOME_TO_VERDICT = {
    "cloud_sea": "GO", "clear": "MAYBE", "mixed": "MAYBE", "fail": "NO_GO",
    "GO": "GO", "MAYBE": "MAYBE", "NO_GO": "NO_GO",
}
