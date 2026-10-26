"""Společná logika horských vyhlídek (Arieiro, Ruivo) pro východ i západ: mlha řízená větrem
a vrstva mraků ve výšce vrcholu nebo nad ním (vrchol NENÍ nad mořem mraků)."""

from __future__ import annotations

from madeira.analyzers.scoring_utils import pw
from madeira.domain.config import MountainCloudConfig

CLOUD_BASE_M_PER_C = 125  # základna mraků ≈ 125 m nad mořem za každý °C rozdílu T − rosný bod (LCL)


def cloud_base_m(coast_dewpoint_depression_c: float | None) -> float | None:
    """Odhad základny mraků od hladiny moře z T − rosný bod na pobřeží (ne od vrcholu!)."""
    if coast_dewpoint_depression_c is None:
        return None
    return CLOUD_BASE_M_PER_C * max(coast_dewpoint_depression_c, 0.0)


def cloud_cover(*values: float | None) -> float | None:
    return max((v for v in values if v is not None), default=None)


def aloft_fog_threshold(gust_kmh: float | None, cfg: MountainCloudConfig) -> float:
    """Práh RH (800 hPa) pro mlhu: při silných nárazech nižší (orografická kondenzace na masivu)."""
    windy = gust_kmh is not None and gust_kmh >= cfg.windy_gust_kmh
    return cfg.aloft_rh_fog_windy if windy else cfg.aloft_rh_fog


def wind_driven_fog(
    rh_aloft: float | None,
    gust_kmh: float | None,
    cover_pct: float | None,
    cfg: MountainCloudConfig,
) -> float:
    """0..1: mlha na vrcholu z vlhkosti v 800 hPa s prahem závislým na nárazech větru.

    Vše je lineární rampa (žádný ostrý skok): vlhkost nad prahem × podíl oblačnosti. Vlhký vzduch
    bez mraků mlhu nedělá, proto se násobí rampou pokrytí. Shora omezeno na `wind_fog_max`, aby
    brána skóre stáhla k NO-GO, ne k nule."""
    if rh_aloft is None or cover_pct is None:
        return 0.0
    lo = aloft_fog_threshold(gust_kmh, cfg)
    humidity = pw(rh_aloft, [(lo, 0.0), (lo + cfg.aloft_rh_span, 1.0)])
    cover = pw(cover_pct, [(cfg.wind_fog_cover_from, 0.0), (cfg.wind_fog_cover_full, 1.0)])
    return cfg.wind_fog_max * humidity * cover


def spillover_cap(
    deck_top_m: float | None,
    summit_m: float,
    rh_aloft: float | None,
    cfg: MountainCloudConfig,
    gust_kmh: float | None = None,
) -> float | None:
    """Strop skóre (0..100) pro východ i západ, když horní hranice mraků leží příliš blízko hřebenu.

    Pod `spill_from_m` je rezerva dost; od `spill_full_m` do hřebenu hrozí přelévání mraků přes
    hřeben (strop `spill_cap`), nad hřebenem je vrchol uvnitř mraků (`inside_cap`). Přelévání se
    bere vážně jen při vlhku nad vrcholem – nad suchým vzduchem (800 hPa) inverze drží.

    Tvrdé pravidlo (relativní, škáluje s výškou vrcholu): rezerva `summit_m − horní hranice` menší
    než `spill_clearance_m` a ZÁROVEŇ RH(800 hPa) ≥ `spill_hard_rh` nebo
    nárazy ≥ `windy_gust_kmh` → strop `spill_hard_cap` (NO-GO) bez ohledu na rampu vlhkosti."""
    if deck_top_m is None:
        return None
    if summit_m - deck_top_m < cfg.spill_clearance_m and spillover_hard(rh_aloft, gust_kmh, cfg):
        floor = cfg.inside_cap if deck_top_m >= summit_m else cfg.spill_hard_cap
        return min(floor, cfg.spill_hard_cap)
    risk = pw(deck_top_m, [(cfg.spill_from_m, 0.0), (cfg.spill_full_m, 1.0)])
    if rh_aloft is not None:
        risk *= pw(rh_aloft, [(cfg.spill_rh_dry, 0.0), (cfg.spill_rh_moist, 1.0)])
    if risk <= 0:
        return None
    floor = cfg.inside_cap if deck_top_m >= summit_m else cfg.spill_cap
    return 100.0 - risk * (100.0 - floor)


def spillover_hard(
    rh_aloft: float | None, gust_kmh: float | None, cfg: MountainCloudConfig
) -> bool:
    """Mraky u hřebene se přelévají: vlhko nad vrcholem nebo silné nárazy větru."""
    return (rh_aloft is not None and rh_aloft >= cfg.spill_hard_rh) or (
        gust_kmh is not None and gust_kmh >= cfg.windy_gust_kmh
    )


def high_deck_gate(
    summit_m: float,
    coast_dewpoint_depression_c: float | None,
    cover_pct: float | None,
    trust: float,
    cfg: MountainCloudConfig,
) -> float:
    """Brána 0..1: základna mraků (od hladiny moře) leží nad vrcholem a obloha je z >25 % zakrytá.

    Vrchol pak není nad mořem mraků, ale pod mraky. Mraky nižší než vrchol (inverze) bránu
    nespustí. `trust` (0..1) je důvěra, že je i nad vrcholem vlhko; při suchu (inverze) je 0."""
    base = cloud_base_m(coast_dewpoint_depression_c)
    if base is None or cover_pct is None or trust <= 0:
        return 1.0
    if base <= summit_m or cover_pct <= cfg.cover_min_pct:
        return 1.0
    return 1.0 - trust * (1.0 - cfg.high_deck_gate)
