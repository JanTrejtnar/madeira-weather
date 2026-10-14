from __future__ import annotations

import os
from pathlib import Path

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
