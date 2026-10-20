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

    Platí jen při významné oblačnosti (vlhký vzduch bez mraků mlhu nedělá); shora omezeno na
    `wind_fog_max`, aby brána skóre stáhla k NO-GO, ne k nule."""
    if rh_aloft is None or cover_pct is None or cover_pct <= cfg.cover_min_pct:
        return 0.0
    lo = aloft_fog_threshold(gust_kmh, cfg)
    return cfg.wind_fog_max * pw(rh_aloft, [(lo, 0.0), (lo + cfg.aloft_rh_span, 1.0)])


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
