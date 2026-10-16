from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from madeira.domain.models import Location


class SunriseWeights(BaseModel):
    sky: float = 0.45
    color: float = 0.15
    visibility: float = 0.25
    wind: float = 0.15
    cloudsea: float = 0.0


class VerdictThresholds(BaseModel):
    go: float = 70
    maybe: float = 45


class SunriseWindow(BaseModel):
    before_min: int = 60
    after_min: int = 30


class FogConfig(BaseModel):
    """Detekce "vrchol je v mraku". Rozhoduje vlhkost ve výšce vrcholu (z hladin 850/800 hPa);
    přízemní vlhkost/dohlednost modelu se bere v potaz jen tehdy, když vzduch nad vrcholem není suchý."""

    summit_rh_clear: float = 70  # RH ve výšce vrcholu pod tímto = bez mlhy
    summit_rh_fog: float = 90  # RH nad tímto = vrchol v mraku
    aloft_dry_rh: float = (
        40  # RH v 800 hPa pod tímto = suchý vzduch nad vrstvou (přízemí se ignoruje)
    )
    aloft_moist_rh: float = 65  # RH v 800 hPa nad tímto = přízemní vlhkost je důvěryhodná
    gate_strength: float = 0.85  # o kolik brána sráží skóre při jistotě mlhy


class SunriseConfig(BaseModel):
    weights: SunriseWeights = Field(default_factory=SunriseWeights)
    verdict: VerdictThresholds = Field(default_factory=VerdictThresholds)
    window: SunriseWindow = Field(default_factory=SunriseWindow)
    fog: FogConfig = Field(default_factory=FogConfig)


class InversionWeights(BaseModel):
    stability: float = 0.30
    moisture: float = 0.25
    above_deck: float = 0.30
    lowland: float = 0.15


class InversionConfig(BaseModel):
    weights: InversionWeights = Field(default_factory=InversionWeights)


class SpotConfig(BaseModel):
    """Vyhlídkové místo: klíč odpovídá lokalitě v locations.yaml (souřadnice jsou tam)."""

    name: str
    type: Literal["mountain_inversion", "coastal_sunset"]
    departure_from_funchal_min: int  # odjezd z Funchalu před východem/západem (jízda + chůze)


def _default_spots() -> dict[str, SpotConfig]:
    return {
        "arieiro": SpotConfig(
            name="Pico do Arieiro", type="mountain_inversion", departure_from_funchal_min=80
        ),
        "bica_da_cana": SpotConfig(
            name="Miradouro da Bica da Cana",
            type="mountain_inversion",
            departure_from_funchal_min=70,
        ),
        "ponta_do_pargo": SpotConfig(
            name="Farol da Ponta do Pargo",
            type="coastal_sunset",
            departure_from_funchal_min=65,
        ),
    }


class SpotTuning(BaseModel):
    """Úpravy heuristiky východu pro jiný vrchol než Arieiro; None = výchozí chování."""

    cloud_top_max_m: float | None = None  # mraky musí končit pod touto výškou
    peak_rh_max: float | None = None  # RH ve výšce vrcholu pod tímto = bez mlhy
    dry_layer_hpa: int = 800  # hladina, jejíž sucho odděluje vrchol od vlhké vrstvy
    max_gusts_kmh: float | None = None  # nárazy do této hodnoty nesrážejí skóre


class BicaInversion(BaseModel):
    target_cloud_top_max: float = 1400
    peak_rh_max: float = 70
    dry_layer_hpa: int = 850


class BicaWind(BaseModel):
    max_gusts_kmh: float = 30


class BicaConfig(BaseModel):
    """Bica da Cana (1560 m ≈ hladina 850 hPa): mraky musí zůstat pod plošinou Paul da Serra."""

    inversion: BicaInversion = Field(default_factory=BicaInversion)
    wind: BicaWind = Field(default_factory=BicaWind)

    def tuning(self) -> SpotTuning:
        return SpotTuning(
            cloud_top_max_m=self.inversion.target_cloud_top_max,
            peak_rh_max=self.inversion.peak_rh_max,
            dry_layer_hpa=self.inversion.dry_layer_hpa,
            max_gusts_kmh=self.wind.max_gusts_kmh,
        )


class SunsetGates(BaseModel):
    max_precipitation_mm: float = 0.1  # více = NO-GO
    max_horizon_low_clouds: float = 30  # nad tímto nízké mraky na západě zakrývají slunce
    min_visibility_m: float = 15000


class SunsetWeights(BaseModel):
    horizon_clarity: float = 0.40
    cloud_canvas: float = 0.35
    low_cloud_penalty: float = 0.15
    wind_comfort: float = 0.10


Curve = list[tuple[float, float]]  # (vstup, skóre 0..100)


class SunsetCurves(BaseModel):
    canvas_clouds: Curve = [(0, 50), (15, 80), (35, 100), (55, 90), (70, 40), (85, 0)]
    horizon_low_clouds: Curve = [(0, 100), (15, 90), (25, 60), (40, 20), (60, 0)]


class SunsetWindow(BaseModel):
    before_min: int = 45
    after_min: int = 15  # barvy oblohy vrcholí až po západu


class SunsetConfig(BaseModel):
    gates: SunsetGates = Field(default_factory=SunsetGates)
    weights: SunsetWeights = Field(default_factory=SunsetWeights)
    piecewise: SunsetCurves = Field(default_factory=SunsetCurves)
    window: SunsetWindow = Field(default_factory=SunsetWindow)
    verdict: VerdictThresholds = Field(default_factory=VerdictThresholds)


class BotConfig(BaseModel):
    digest_time: str = "20:00"  # místní čas Madeiry
    feedback_time: str = "10:00"
    departure_before_min: int = 80  # odjezd z Funchalu před východem (jízda + chůze + rezerva)
    compare_from: str = "13:00"
    compare_to: str = "18:00"


class Settings(BaseModel):
    sunrise: SunriseConfig = Field(default_factory=SunriseConfig)
    inversion: InversionConfig = Field(default_factory=InversionConfig)
    bot: BotConfig = Field(default_factory=BotConfig)
    spots: dict[str, SpotConfig] = Field(default_factory=_default_spots)
    bica_da_cana: BicaConfig = Field(default_factory=BicaConfig)
    sunset: SunsetConfig = Field(default_factory=SunsetConfig)


def config_dir() -> Path:
    env = os.environ.get("MADEIRA_CONFIG_DIR")
    if env:
        return Path(env)
    return Path(__file__).resolve().parents[3] / "config"


def load_settings() -> Settings:
    path = config_dir() / "weights.yaml"
    if not path.exists():
        return Settings()
    return Settings.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


def load_locations() -> list[Location]:
    path = config_dir() / "locations.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [Location.model_validate(item) for item in data["locations"]]


def load_env() -> None:
    """Načte KEY=VALUE z .env v kořeni repa (existující proměnné prostředí nepřepisuje)."""
    path = config_dir().parent / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
