from __future__ import annotations

import os
from datetime import date
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


class MountainCloudConfig(BaseModel):
    """Horské vyhlídky (východ i západ): mlha hnaná větrem a mraky ve výšce vrcholu / nad ním."""

    aloft_rh_fog: float = 80  # RH v 800 hPa od tohoto začíná penalizace mlhy
    aloft_rh_fog_windy: float = 70  # totéž při silných nárazech (vítr žene vlhkost na masiv)
    windy_gust_kmh: float = 45  # nárazy od tohoto = použije se nižší práh
    aloft_rh_span: float = 8  # o kolik % RH nad prahem je vlhkostní rampa plná
    wind_fog_cover_from: float = 15  # mlha z větru: rampa podle nízké/střední oblačnosti (%) ...
    wind_fog_cover_full: float = 35  # ... od 0 po plnou sílu
    spill_from_m: float = 1400  # západ z vrcholu: horní hranice mraků pod tímto = plná rezerva
    spill_full_m: float = 1500  # od tohoto po hřeben hrozí přelévání mraků (strop skóre)
    spill_cap: float = 39  # strop skóre při hrozícím přelévání (NO-GO)
    inside_cap: float = 25  # strop skóre, když je horní hranice mraků nad vrcholem
    spill_clearance_m: float = 318  # min. rezerva vrcholu nad horní hranicí mraků (Arieiro 1818 → 1500 m)
    spill_hard_cap: float = 35  # tvrdý strop (NO-GO), když rezerva < spill_clearance_m a vlhko / nárazy
    spill_hard_rh: float = 60  # RH v 800 hPa od tohoto (nebo nárazy >= windy_gust_kmh) = tvrdý strop
    spill_rh_dry: float = 35  # RH v 800 hPa pod tímto = suchý vzduch nad vrstvou, přelévání nehrozí
    spill_rh_moist: float = 55  # nad tímto je riziko přelévání plné
    cover_min_pct: float = 25  # nízká/střední oblačnost nad tímto
    wind_fog_max: float = 0.85  # strop mlhy z větru (brána ~0.28, ne nula)
    high_deck_gate: float = 0.35  # koeficient skóre, když je základna mraků nad vrcholem
    low_cloud_cap: float = 35  # západ: strop (NO-GO) při velké nízké oblačnosti a vlhku bez suché pokličky
    low_cloud_from: float = 45  # nízká oblačnost (%) od tohoto začíná strop ...
    low_cloud_full: float = 55  # ... a od tohoto je plný
    lc_rh_dry: float = 50  # RH (800 hPa) pod tímto = suchá poklička; nad ní roste vlhkost ...
    lc_rh_moist: float = 65  # ... až do plné síly
    lc_summit_rh_dry: float = 60  # totéž pro RH ve výšce vrcholu
    lc_summit_rh_moist: float = 72


class SunriseConfig(BaseModel):
    weights: SunriseWeights = Field(default_factory=SunriseWeights)
    verdict: VerdictThresholds = Field(default_factory=VerdictThresholds)
    window: SunriseWindow = Field(default_factory=SunriseWindow)
    fog: FogConfig = Field(default_factory=FogConfig)
    overcast_from: float = 80  # střední oblačnost od tohoto (%) = zataženo, východ není vidět
    overcast_gate_min: float = 0.3  # při 100 % zatažení se skóre násobí tímto (→ NO-GO)


class InversionWeights(BaseModel):
    stability: float = 0.30
    moisture: float = 0.25
    above_deck: float = 0.30
    lowland: float = 0.15


class InversionConfig(BaseModel):
    weights: InversionWeights = Field(default_factory=InversionWeights)


class TerrainSector(BaseModel):
    """Terén v ose slunce: pro azimuty az_from–az_to (včetně) leží obzor `cutoff_elev_deg` nad rovinou."""

    az_from: float
    az_to: float
    cutoff_elev_deg: float


class TerrainGate(BaseModel):
    """Brána stínění: slunce za terénem vysokým `cutoff_elev_deg` mizí/vychází o desítky minut jinak.

    Lineární rampa: do `free_deg` bez trestu, od `blocked_deg` skóre ×0 (1° ≈ 5 min západu na Madeiře)."""

    free_deg: float = 1.0
    blocked_deg: float = 6.0
    edge_deg: float = 3.0  # měkký okraj sektoru: výška terénu klesá lineárně k 0° během tohoto pásma za hranou
    horizon_km: float = 55.0  # vzdálenost dynamického bodu obzoru v ose slunce (dávkový dotaz Open-Meteo)


class SpotConfig(BaseModel):
    """Vyhlídkové místo: klíč odpovídá lokalitě v locations.yaml (souřadnice jsou tam)."""

    # Obzor v ose slunce: dynamický bod `horizon_<klíč spotu>` místo pevného východního/západního bodu
    dynamic_horizon: bool = False
    terrain_cutoff: list[TerrainSector] = Field(default_factory=list)  # prázdné = otevřený obzor (0°)

    name: str
    type: Literal["mountain_inversion", "coastal_sunrise", "coastal_sunset", "mountain_sunset"]
    webcam_url: str | None = None  # živá webkamera (odkaz na webu)
    location: str | None = None  # klíč lokality v locations.yaml; None = klíč spotu (jeden vrchol, dva spoty)
    # Spot leží ve stejné buňce modelu jako jiný (Ruivo ~ Arieiro, 1,8 km): data nejsou nezávislá předpověď
    derived_from: str | None = None

def _default_spots() -> dict[str, SpotConfig]:
    return {
        "arieiro": SpotConfig(
            name="Pico do Arieiro",
            type="mountain_inversion",
            webcam_url="https://www.netmadeira.com/webcams-madeira/pico-do-arieiro",
        ),
        "bica_da_cana": SpotConfig(
            name="Miradouro da Bica da Cana",
            type="mountain_inversion",
            dynamic_horizon=True,
            terrain_cutoff=[TerrainSector(az_from=90, az_to=105, cutoff_elev_deg=1.6)],
        ),
        "ponta_do_pargo": SpotConfig(
            name="Farol da Ponta do Pargo",
            type="coastal_sunset",
            dynamic_horizon=True,
        ),
        "pico_ruivo": SpotConfig(
            name="Pico Ruivo", type="mountain_inversion", derived_from="arieiro", dynamic_horizon=True
        ),
        "ponta_do_rosto": SpotConfig(
            name="Miradouro da Ponta do Rosto", type="coastal_sunrise", dynamic_horizon=True
        ),
        "cristo_rei": SpotConfig(
            name="Miradouro do Cristo Rei", type="coastal_sunset", dynamic_horizon=True,
            terrain_cutoff=[
                TerrainSector(az_from=240, az_to=260, cutoff_elev_deg=1.5),
                TerrainSector(az_from=275, az_to=300, cutoff_elev_deg=7.0),
            ],
        ),
        "arieiro_sunset": SpotConfig(
            name="Pico do Arieiro", type="mountain_sunset",
            location="arieiro",
            webcam_url="https://www.netmadeira.com/webcams-madeira/pico-do-arieiro",
        ),
        "pico_ruivo_sunset": SpotConfig(
            name="Pico Ruivo", type="mountain_sunset",
            location="pico_ruivo", derived_from="arieiro", dynamic_horizon=True,
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
    mid_block_from: float = 60  # střední oblačnost (%) od tohoto blokuje slunce (do barev nevstupuje)
    mid_block_gate_min: float = 0.3  # při 100 % střední oblačnosti se skóre násobí tímto
    weights: dict[str, float] = {"horizon_clarity": 0.45, "cloud_canvas": 0.25, "visibility": 0.15, "wind": 0.15}


Curve = list[tuple[float, float]]  # (vstup, skóre 0..100)


class SunsetGates(BaseModel):
    max_precipitation_mm: float = 0.1  # více = NO-GO
    max_horizon_low_clouds: float = 30  # nad tímto nízké mraky na západě zakrývají slunce
    min_visibility_m: float = 15000
    overcast_from: float = 80  # nízká/střední oblačnost na západě od tohoto (%) = zataženo, slunce není vidět
    overcast_gate_min: float = 0.3  # při 100 % zatažení se skóre násobí tímto (→ pod 40 %, NO-GO)
    mid_block_from: float = 60  # střední oblačnost (%) od tohoto jen blokuje slunce; do barev nevstupuje


class MountainSunsetConfig(BaseModel):
    """Západ z vrcholu (Arieiro, Ruivo): mraky pod vrcholem nevadí, mlha na vrcholu ano."""

    fog_rh_clear: float = 85  # RH v 800 hPa pod tímto = bez mlhy (brána se nepoužije)
    fog_rh_fog: float = 90  # RH v 800 hPa od tohoto = vrchol v mraku (brána na minimum)
    summit_rh_clear: float = 85  # totéž pro RH přímo ve výšce vrcholu (850/800 hPa)
    summit_rh_fog: float = 90
    fog_gate_strength: float = 0.85  # o kolik brána sráží skóre při jisté mlze
    inversion_bonus: float = 10  # body navíc za moře mraků pod vrcholem ("zlatá inverze")
    bonus_rh_max: float = 70  # bonus jen když je RH v 800 hPa pod tímto (suchý vzduch nad vrstvou)
    bonus_deck_max_m: float = 1400  # ... a horní hranice mraků je pod touto výškou
    bonus_low_cloud: Curve = [(30, 0.0), (70, 1.0)]  # [nízká oblačnost %, podíl bonusu]


class SunsetWeights(BaseModel):
    horizon_clarity: float = 0.40
    cloud_canvas: float = 0.35
    low_cloud_penalty: float = 0.15
    wind_comfort: float = 0.10


class SunsetCurves(BaseModel):
    canvas_clouds: Curve = [(0, 50), (15, 80), (35, 100), (55, 90), (70, 45), (100, 45)]
    horizon_low_clouds: Curve = [(0, 100), (15, 90), (25, 60), (40, 20), (60, 0)]


class SunsetWindow(BaseModel):
    before_min: int = 45
    after_min: int = 15  # barvy oblohy vrcholí až po západu


class SunsetConfig(BaseModel):
    gates: SunsetGates = Field(default_factory=SunsetGates)
    mountain: MountainSunsetConfig = Field(default_factory=MountainSunsetConfig)
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


class FanalDew(BaseModel):
    """Primární metrika: vlhkost vzduchu ve výšce lesa (izobarické hladiny 900/850 hPa), ne přízemní RH a rosný bod.

    Přízemní RH a T − Td ráno kvůli nočnímu ochlazení padají k nasycení téměř každý den, i když v lese mlha není.
    """

    rh_extreme: float = 95  # [%] vlhkost ve výšce lesa od této hodnoty = plný zisk (points_extreme)
    rh_likely: float = 90  # [%] od této hodnoty částečný zisk (points_likely)
    rh_dry: float = 80  # [%] pod touto hodnotou nic; mezi dry a likely body lineárně rostou
    points_extreme: float = 50
    points_likely: float = 30
    dawn_until_hour: int = 10  # před touto místní hodinou (rozbřesk) platí pravidlo níže
    dawn_min_low_cloud_pct: float = 50  # ráno se vlhkost započítá jen při modelem potvrzené nízké oblačnosti ≥ tato hodnota


class FanalLayer(BaseModel):
    """Vrstva vzduchu ve výšce lesa: pod nasycenou vrstvou (900 hPa ≈ 1 100 m) a nad ní (850 hPa ≈ 1 550 m)."""

    mystic_rh_min: float = 80  # MYSTIC jen když RH v 900 i 850 hPa ≥ tato hodnota; v suchém 850 hPa (< 60 %) leží inverzní poklička pod lesem


class FanalWind(BaseModel):
    """Návětrný směr: pasát ze severu tlačí vlhkost po svahu na plošinu, jižní vítr (föhn) ji vysouší."""

    windward_from: float = 320  # severní až severovýchodní sektor (přes 0°) [°]
    windward_to: float = 70
    lee_from: float = 140  # jižní sektor [°]
    lee_to: float = 220
    speed_min: float = 8  # bonus jen při rychlosti větru v tomto rozsahu [km/h]
    speed_max: float = 30
    bonus: float = 25  # jen při vlhkém vzduchu (vlhkost ve výšce lesa ≥ dew.rh_dry)
    lee_penalty: float = 30
    calm_from: float = 290  # slabý vítr pod speed_min z NW/N (290°–020°) dostane bonus taky, pokud je 900 hPa nasycená
    calm_to: float = 20
    calm_rh_min: float = 95  # [%] RH v 900 hPa pro „plně nasycenou“ vrstvu při bezvětří


class FanalCurves(BaseModel):
    visibility: Curve = [(400, 1), (1000, 0.6), (3000, 0.1), (10000, 0)]  # m -> 0..1 násobek bodů za dohlednost
    precip: Curve = [(1.5, 1), (3, 0)]  # mm/h: mrholení do 1,5 nevadí, nad 3 skóre k nule
    gusts: Curve = [(50, 1), (80, 0.3)]  # km/h: model hlásí náraz 10 m i při reálné mlze v 39–72 km/h


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
    window_hours: int = 3  # délka doporučeného okna od začátku nejlepšího slotu
    cloud_base_m_per_c: float = 125  # jen informativní odhad na webu, do skóre nevstupuje: základna mraků nad zemí ≈ 125 m za každý °C rozdílu T − rosný bod
    dew: FanalDew = Field(default_factory=FanalDew)
    wind: FanalWind = Field(default_factory=FanalWind)
    layer: FanalLayer = Field(default_factory=FanalLayer)
    visibility_points: float = 25  # bonus za nízkou dohlednost (model ji často nevidí, proto jen bonus)
    curves: FanalCurves = Field(default_factory=FanalCurves)
    verdict: VerdictThresholds = Field(default_factory=lambda: VerdictThresholds(go=70, maybe=40))

    def as_location(self) -> Location:
        return Location(key="fanal", name=self.name, lat=self.lat, lon=self.lon,
                        elevation_m=self.elevation_m, role=self.type)


class BotConfig(BaseModel):
    digest_time: str = "20:00"  # místní čas Madeiry
    feedback_time: str = "10:00"
    compare_from: str = "13:00"
    compare_to: str = "18:00"


class Settings(BaseModel):
    sunrise: SunriseConfig = Field(default_factory=SunriseConfig)
    inversion: InversionConfig = Field(default_factory=InversionConfig)
    bot: BotConfig = Field(default_factory=BotConfig)
    spots: dict[str, SpotConfig] = Field(default_factory=_default_spots)
    terrain: TerrainGate = Field(default_factory=TerrainGate)
    bica_da_cana: BicaConfig = Field(default_factory=BicaConfig)
    sunset: SunsetConfig = Field(default_factory=SunsetConfig)
    mountain_clouds: MountainCloudConfig = Field(default_factory=MountainCloudConfig)
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


SUNSET_SPOT_TYPES = ("coastal_sunset", "mountain_sunset")


def spot_event(spot: SpotConfig) -> Literal["sunrise", "sunset"]:
    return "sunset" if spot.type in SUNSET_SPOT_TYPES else "sunrise"


def horizon_key(spot_key: str) -> str:
    """Klíč dynamického bodu obzoru spotu v bundlu Open-Meteo."""
    return f"horizon_{spot_key}"


def terrain_cutoff_deg(spot: SpotConfig, azimuth_deg: float, edge_deg: float = 3.0) -> float:
    """Výška terénu v ose slunce (°) pro daný azimut; mimo popsané sektory 0° (otevřený obzor).

    Uvnitř sektoru plná hodnota, za hranou lineárně klesá k 0° během `edge_deg` (bez skoku mezi sousedními dny)."""
    from madeira.domain.solar import azimuth_gap

    def at(s: TerrainSector) -> float:
        gap = azimuth_gap(azimuth_deg, s.az_from, s.az_to)
        return s.cutoff_elev_deg * max(0.0, 1.0 - gap / edge_deg) if edge_deg > 0 else float(gap == 0) * s.cutoff_elev_deg

    return max((at(s) for s in spot.terrain_cutoff), default=0.0)


def terrain_gate(cutoff_deg: float, cfg: TerrainGate) -> float:
    """0..1: násobek skóre podle výšky terénu v ose slunce (lineárně od free_deg po blocked_deg)."""
    if cutoff_deg <= cfg.free_deg:
        return 1.0
    return max(0.0, 1.0 - (cutoff_deg - cfg.free_deg) / (cfg.blocked_deg - cfg.free_deg))


def with_sun_horizons(
    locations: list[Location], settings: Settings, day: date
) -> list[Location]:
    """K lokalitám přidá pro spoty s `dynamic_horizon` bod 55 km v ose slunce (azimut východu/západu dne `day`).

    Azimut se mění o ~0,2° za den, takže jeden bod platí pro celé týdenní okno předpovědi (posun ≪ rozlišení
    modelu). Body jdou do téhož dávkového dotazu Open-Meteo jako ostatní lokality."""
    from madeira.domain.solar import destination, sun_azimuth_deg

    by_key = {loc.key: loc for loc in locations}
    extra = []
    for key, spot in settings.spots.items():
        if not spot.dynamic_horizon:
            continue
        origin = by_key.get(spot.location or key)
        if origin is None:
            continue
        event = spot_event(spot)
        az = sun_azimuth_deg(origin.lat, day, event)
        lat, lon = destination(origin.lat, origin.lon, az, settings.terrain.horizon_km)
        extra.append(Location(
            key=horizon_key(key), name=f"Obzor {spot.name} ({az:.0f}°)", lat=round(lat, 4), lon=round(lon, 4),
            elevation_m=0, role="reference_east" if event == "sunrise" else "reference_west",
        ))
    return [*locations, *extra]


class Webcam(BaseModel):
    """Webkamera NetMadeira; `spot_id` ji páruje s předpovědí (klíč spotu nebo `fanal`)."""

    id: str
    name: str
    altitude_m: int
    category: Literal["mountain", "forest", "sunset", "coast"]
    snapshot_url: str  # šablona s {day} a {slot}
    live_url: str
    lat: float
    lon: float
    spot_id: str | None = None


def load_webcams() -> list[Webcam]:
    path = config_dir() / "webcams.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [Webcam.model_validate(item) for item in data["webcams"]]


class MapSpot(BaseModel):
    """Bod mapy Sunny Escape (docs/map.html); `webcam` je id nejbližší kamery z webcams.yaml."""

    id: str
    name: str
    lat: float
    lon: float
    altitude_m: int
    region: Literal["peaks", "north", "south", "west", "east"]
    webcam: str | None = None


def load_map_spots() -> list[MapSpot]:
    path = config_dir() / "map_spots.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return [MapSpot.model_validate(item) for item in data["spots"]]


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
