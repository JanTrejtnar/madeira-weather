from __future__ import annotations

from madeira.analyzers.features import SunriseFeatures
from madeira.analyzers.scoring_utils import clamp, pw
from madeira.domain.config import InversionConfig
from madeira.domain.models import InversionAnalysis

MOIST_RH = 85.0  # nad touto RH považujeme hladinu za "v oblačné vrstvě"
LEVELS = (925, 850, 800, 700)


def _profile(f: SunriseFeatures) -> list[tuple[float, float]]:
    """(výška m, RH %) hladin, ke kterým máme obě hodnoty; seřazeno zdola nahoru."""
    return sorted(
        (f.z[lv], f.rh[lv]) for lv in LEVELS if f.z.get(lv) is not None and f.rh.get(lv) is not None
    )


def estimate_deck_top(f: SunriseFeatures) -> float | None:
    """Horní hranice vlhké (oblačné) vrstvy z hladin 925/850/800/700 hPa, jinak None."""
    levels = _profile(f)
    top_idx = None
    for i, (_, rh) in enumerate(levels):
        if rh >= MOIST_RH:
            top_idx = i
    if top_idx is None:
        return None
    z, rh = levels[top_idx]
    if top_idx == len(levels) - 1:
        return z
    z2, rh2 = levels[top_idx + 1]
    return z + (rh - MOIST_RH) / (rh - rh2) * (z2 - z)


def rh_aloft(f: SunriseFeatures) -> float | None:
    """RH ve vzduchu nad vrcholem: f.aloft_hpa (Arieiro 800 hPa ≈ 2050 m), jinak 700 hPa."""
    rh = f.rh.get(f.aloft_hpa)
    return rh if rh is not None else f.rh.get(700)


def summit_rh(f: SunriseFeatures) -> float | None:
    """RH ve výšce vrcholu (f.summit_m), interpolovaná mezi hladinami 850 a 800 hPa (jinak 850/700)."""
    for upper in (800, 700):
        z0, z1, r0, r1 = f.z.get(850), f.z.get(upper), f.rh.get(850), f.rh.get(upper)
        if None not in (z0, z1, r0, r1) and z1 != z0:
            return r0 + (r1 - r0) * clamp((f.summit_m - z0) / (z1 - z0))
    return None


DEFAULT_DECK_MARGIN_M = 100.0  # o kolik musí vrchol převyšovat vrstvu mraků pro plné skóre


def deck_margin(f: SunriseFeatures, cloud_top_max_m: float | None) -> float:
    """Požadované převýšení vrcholu nad vrstvou; cloud_top_max_m = nejvyšší přípustná horní hranice mraků."""
    return DEFAULT_DECK_MARGIN_M if cloud_top_max_m is None else f.summit_m - cloud_top_max_m


def above_deck_factor(
    summit_above_deck_m: float | None, margin_m: float = DEFAULT_DECK_MARGIN_M
) -> float:
    if summit_above_deck_m is None:
        return 0.0
    return pw(summit_above_deck_m, [(margin_m - 300, 0.0), (margin_m, 1.0)])


def summit_clear_factor(f: SunriseFeatures, margin_m: float = DEFAULT_DECK_MARGIN_M) -> float:
    """0..1: nakolik je vrchol nad oblačností pod ním (1 = mraky pohled neblokují).

    Žádná vlhká vrstva ve 925 hPa a výš znamená, že případné mraky jsou níž než ~850 m.
    """
    if not _profile(f):
        return 0.0
    deck_top = estimate_deck_top(f)
    if deck_top is None:
        return 1.0
    return above_deck_factor(f.summit_m - deck_top, margin_m)


def analyze_inversion(
    f: SunriseFeatures,
    cfg: InversionConfig | None = None,
    cloud_top_max_m: float | None = None,
) -> InversionAnalysis:
    cfg = cfg or InversionConfig()
    margin = deck_margin(f, cloud_top_max_m)
    w = cfg.weights
    deck_top = estimate_deck_top(f)
    above = None if deck_top is None else f.summit_m - deck_top

    # Stabilita nad vlhkou vrstvou: T(800) − T(850) ≥ 0 = zadržující inverze nad vrcholem.
    # Bez 800 hPa: starší odhad T(850) − T(925) (normální gradient je asi −5 °C).
    if f.t.get(800) is not None and f.t.get(850) is not None:
        e_stab = pw(f.t[800] - f.t[850], [(-2, 0.0), (0, 1.0)])
    elif f.t.get(850) is not None and f.t.get(925) is not None:
        e_stab = pw(f.t[850] - f.t[925], [(-4, 0.0), (0, 1.0)])
    else:
        e_stab = 0.0

    # Vlhko dole + sucho nahoře je POZITIVNÍ znak inverze (ne mlhy na vrcholu).
    e_moist = 0.0
    aloft = rh_aloft(f)
    if f.rh.get(925) is not None and aloft is not None:
        e_moist = 0.5 * pw(f.rh[925], [(60, 0.0), (90, 1.0)]) + 0.5 * pw(
            aloft, [(40, 1.0), (80, 0.0)]
        )

    e_deck = above_deck_factor(above, margin)

    e_low = 0.0
    if f.lowland_low_cloud is not None and deck_top is not None:
        # hodně nízké oblačnosti na pobřeží a vrchol nad její vrstvou
        e_low = pw(f.lowland_low_cloud, [(20, 0.0), (70, 1.0)]) * e_deck

    trade = 1.0
    if f.wind_dir_deg is not None and f.wind_kmh is not None:
        ok_dir = 10 <= f.wind_dir_deg <= 70
        ok_speed = 15 <= f.wind_kmh <= 45
        trade = 1.0 if (ok_dir and ok_speed) else 0.7

    total_w = w.stability + w.moisture + w.above_deck + w.lowland
    raw = (
        w.stability * e_stab + w.moisture * e_moist + w.above_deck * e_deck + w.lowland * e_low
    ) / total_w
    prob = clamp(raw * trade) * 100

    if deck_top is None:
        desc = "Žádná výrazná vlhká vrstva pod vrcholem — moře mraků nepravděpodobné."
    elif above is not None and above > margin:
        desc = f"Vrstva mraků sahá asi do {deck_top:.0f} m, vrchol ({f.summit_m:.0f} m) je nad ní o ~{above:.0f} m."
    elif above is not None and above > margin - 300:
        desc = f"Vrstva mraků sahá asi do {deck_top:.0f} m — vrchol je na její hranici."
    else:
        desc = f"Vlhká vrstva sahá až k ~{deck_top:.0f} m — vrchol bude pravděpodobně v mraku."

    return InversionAnalysis(
        probability_pct=round(prob, 1),
        cloud_deck_top_m=None if deck_top is None else round(deck_top),
        summit_above_deck_m=None if above is None else round(above),
        evidence={
            "stability": round(e_stab, 2),
            "moisture": round(e_moist, 2),
            "above_deck": round(e_deck, 2),
            "lowland": round(e_low, 2),
            "trade_wind_modifier": trade,
        },
        description_cs=desc,
    )
