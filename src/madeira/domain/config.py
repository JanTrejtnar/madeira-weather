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
    type: Literal["mountain_inversion", "coastal_sunrise", "coastal_sunset", "mountain_sunset"]
    departure_from_funchal_min: int  # odjezd z Funchalu před východem/západem (jízda + chůze)
    webcam_url: str | None = None  # živá webkamera (odkaz na webu)
    location: str | None = None  # klíč lokality v locations.yaml; None = klíč spotu (jeden vrchol, dva spoty)

def _default_spots() -> dict[str, SpotConfig]:
    return {
        "arieiro": SpotConfig(
            name="Pico do Arieiro",
            type="mountain_inversion",
            departure_from_funchal_min=80,
            webcam_url="https://www.netmadeira.com/webcams-madeira/pico-do-arieiro",
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
            webcam_url="https://www.netmadeira.com/webcams-madeira/ponta-do-pargo",
        ),
        "pico_ruivo": SpotConfig(
            name="Pico Ruivo", type="mountain_inversion", departure_from_funchal_min=120
        ),
        "ponta_do_rosto": SpotConfig(
            name="Miradouro da Ponta do Rosto", type="coastal_sunrise", departure_from_funchal_min=50
        ),
        "cristo_rei": SpotConfig(
            name="Miradouro do Cristo Rei", type="coastal_sunset", departure_from_funchal_min=25
        ),
        "arieiro_sunset": SpotConfig(
            name="Pico do Arieiro", type="mountain_sunset", departure_from_funchal_min=80,
            location="arieiro",
            webcam_url="https://www.netmadeira.com/webcams-madeira/pico-do-arieiro",
        ),
        "pico_ruivo_sunset": SpotConfig(
            name="Pico Ruivo", type="mountain_sunset", departure_from_funchal_min=120,
            location="pico_ruivo",
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


class CoastalSunriseConfig(BaseModel):
    """Východ na útesu (Ponta do Rosto): čistý východní mořský horizont a mírný vítr."""

    max_horizon_low_clouds: float = 20  # nad tímto (%) nízké mraky na východě zakrývají slunce
    max_gusts_kmh: float = 40  # nárazy nad tímto na útesu srážejí skóre
    weights: dict[str, float] = {"horizon_clarity": 0.45, "cloud_canvas": 0.25, "visibility": 0.15, "wind": 0.15}


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


class EscapePoint(BaseModel):
    """Pobřežní bod pro "Sunny Escape"; souřadnice a výška se posílají do Open-Meteo."""

    key: str
    name: str
    lat: float
    lon: float
    elevation_m: float
    name_gen: str | None = None  # 2. pád pro "jeďte do …" (česky); None = stejné jako name

    def as_location(self) -> Location:
        return Location(**self.model_dump(exclude={"name_gen"}), role="coast_escape")


def _default_escape_points() -> list[EscapePoint]:
    return [
        EscapePoint(key="ponta_do_sol", name="Ponta do Sol", lat=32.6806, lon=-17.1042, elevation_m=20),
        EscapePoint(key="calheta", name="Calheta / Jardim do Mar", name_gen="Calhety / Jardim do Mar", lat=32.7214, lon=-17.1742, elevation_m=15),
        EscapePoint(key="machico", name="Machico", name_gen="Machica", lat=32.7183, lon=-16.7644, elevation_m=10),
        EscapePoint(key="sao_vicente", name="São Vicente", lat=32.8028, lon=-17.0436, elevation_m=30),
        EscapePoint(key="funchal", name="Funchal", name_gen="Funchalu", lat=32.65, lon=-16.9086, elevation_m=50),
    ]


class EscapeWeights(BaseModel):
    sunshine: float = 0.40  # slunce (sunshine duration / přímé záření)
    clouds: float = 0.40  # nízká + střední oblačnost
    temperature: float = 0.20


class EscapeCloudMix(BaseModel):
    low: float = 0.45  # nízká a střední oblačnost zakrývá slunce nejvíc
    mid: float = 0.35
    high: float = 0.20


class EscapeConfig(BaseModel):
    """Nejslunnější pobřežní místo v okně hour_from–hour_to (místní čas, hour_to bez konce)."""

    hour_from: int = 11
    hour_to: int = 16
    points: list[EscapePoint] = Field(default_factory=_default_escape_points)
    weights: EscapeWeights = Field(default_factory=EscapeWeights)
    cloud_mix: EscapeCloudMix = Field(default_factory=EscapeCloudMix)
    max_radiation_wm2: float = 700  # přímé záření odpovídající plnému slunci (záloha bez sunshine_duration)
    # [průměrná °C v okně, skóre 0–1]
    temperature_curve: list[tuple[float, float]] = [
        (14, 0.0), (18, 0.4), (22, 0.85), (25, 1.0), (28, 1.0), (33, 0.6),
    ]
    rain_min_mm: float = 0.1  # úhrn v okně od tohoto = srážky (pod tím jen stopy)
    rain_base_factor: float = 0.5  # skóre se po prvních srážkách násobí tímto ...
    rain_per_mm: float = 0.15  # ... a dál klesá o tolik za každý mm (min. 0)


class FanalWeights(BaseModel):
    humidity: float = 0.40  # RH u povrchu a ve výšce lesa
    cloud_base: float = 0.35  # základna mraků v úrovni lesa
    visibility: float = 0.25  # nízká dohlednost = jsme uvnitř mraku


class FanalCurves(BaseModel):
    humidity: Curve = [(60, 0), (85, 1), (100, 1)]  # RH [%] -> 0..1; 85–100 % = maximum
    cloud_base: Curve = [(700, 0), (900, 1), (1300, 1), (1600, 0.3), (2000, 0)]  # m n. m.; 900–1300 = les v mraku
    visibility: Curve = [(400, 1), (1000, 0.6), (3000, 0.1), (10000, 0)]  # m; <400 ideál, <1000 dobré
    precip: Curve = [(1.5, 1), (3, 0)]  # mm/h: mrholení do 1,5 nevadí, nad 3 skóre k nule
    gusts: Curve = [(35, 1), (60, 0.2)]  # km/h: nad 35 vichr trhá mlhu


class FanalConfig(BaseModel):
    """Fanal Forest (1150 m): obrácená logika než hory, hledáme hustou mlhu bez vichru a lijáku."""

    name: str = "Fanal Forest"
    type: Literal["fog_seeker"] = "fog_seeker"
    lat: float = 32.8083
    lon: float = -17.1417
    elevation_m: float = 1150
    # NetMadeira nemá kameru přímo na Fanalu ani Paul da Serra; nejbližší je Rabaçal (stejná výška, ~8 km)
    webcam_url: str = "https://www.netmadeira.com/webcams-madeira/rabacal-madeira"
    slot_hours: list[int] = [9, 13, 17]  # denní sloty (místní čas)
    window_from: int = 8  # hledání nejlepšího okna (místní čas, hodiny from..to včetně)
    window_to: int = 18
    cloud_base_m_per_c: float = 125  # základna mraků nad zemí ≈ 125 m za každý °C rozdílu T − rosný bod
    weights: FanalWeights = Field(default_factory=FanalWeights)
    curves: FanalCurves = Field(default_factory=FanalCurves)
    verdict: VerdictThresholds = Field(default_factory=lambda: VerdictThresholds(go=70, maybe=40))

    def as_location(self) -> Location:
        return Location(key="fanal", name=self.name, lat=self.lat, lon=self.lon,
                        elevation_m=self.elevation_m, role=self.type)


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
    coastal_sunrise: CoastalSunriseConfig = Field(default_factory=CoastalSunriseConfig)
    sunny_escape: EscapeConfig = Field(default_factory=EscapeConfig)
    fanal: FanalConfig = Field(default_factory=FanalConfig)


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
