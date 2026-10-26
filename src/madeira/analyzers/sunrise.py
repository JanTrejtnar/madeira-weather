from __future__ import annotations

from datetime import date, datetime

from madeira.analyzers.features import SunriseFeatures, extract_sunrise_features
from madeira.analyzers.inversion import (
    analyze_inversion,
    deck_margin,
    rh_aloft,
    summit_clear_factor,
    summit_rh,
)
from madeira.analyzers.mountain import (
    cloud_cover,
    high_deck_gate,
    spillover_cap,
    spillover_hard,
    wind_driven_fog,
)
from madeira.analyzers.scoring_utils import pw
from madeira.domain.config import FogConfig, MountainCloudConfig, Settings, SpotTuning
from madeira.domain.models import ForecastBundle, ScoreComponent, SunriseAnalysis

CLEAR_SKY = [(0, 1.0), (10, 1.0), (60, 0.2), (100, 0.0)]
COLOR = [(0, 0.6), (20, 1.0), (60, 1.0), (85, 0.5), (100, 0.2)]
VISIBILITY_KM = [(1, 0.0), (5, 0.4), (10, 0.8), (20, 1.0)]
DEWPOINT_DEP = [(0.5, 0.0), (3, 1.0)]
WIND_GUST = [(25, 1.0), (50, 0.5), (70, 0.1), (90, 0.0)]
PRECIP_MM = [(0.0, 1.0), (0.2, 0.6), (0.5, 0.0)]
PRECIP_PROB = [(20, 1.0), (60, 0.6), (90, 0.3)]
GALE = [(70, 1.0), (90, 0.3)]
FOG_DEP = [(0.5, 1.0), (3, 0.0)]
NEUTRAL = 0.5  # hodnota komponenty při chybějících datech


def surface_trust(f: SunriseFeatures, cfg: FogConfig) -> float:
    """0..1: nakolik věřit přízemní vlhkosti/dohlednosti modelu u Arieira.

    Model má pro Arieiro vyhlazený terén a přízemní hodnoty bere z výšky ~1200 m, tedy často
    z vnitřku moře mraků. Pokud je vzduch nad vrcholem (800 hPa) suchý, jde o inverzi a přízemní
    vlhkost/dohlednost nic neříká o vrcholu → nedůvěra (0). Je-li i nad vrcholem vlhko, platí (1).
    """
    aloft = rh_aloft(f)
    if aloft is None:
        return 1.0
    return pw(aloft, [(cfg.aloft_dry_rh, 0.0), (cfg.aloft_moist_rh, 1.0)])


def fog_upper(
    f: SunriseFeatures, cfg: FogConfig, mc: MountainCloudConfig | None = None
) -> float | None:
    """Pravděpodobnost mlhy na vrcholu čistě z vlhkosti ve výšce vrcholu (profil atmosféry).

    S `mc` se přidává mlha hnaná větrem: RH v 800 hPa s prahem klesajícím při silných nárazech."""
    rh = summit_rh(f)
    if rh is None:
        return None
    p = pw(rh, [(cfg.summit_rh_clear, 0.0), (cfg.summit_rh_fog, 1.0)])
    if mc is not None:
        cover = cloud_cover(f.low_cloud_summit, f.mid_cloud)
        p = max(p, wind_driven_fog(rh_aloft(f), f.gust_kmh, cover, mc))
    return p


def fog_likelihood(
    f: SunriseFeatures, cfg: FogConfig, mc: MountainCloudConfig | None = None
) -> float | None:
    """Pravděpodobnost, že je vrchol přímo v mraku/mlze (ne mraky pod ním)."""
    upper = fog_upper(f, cfg, mc)
    surface = None
    if f.dewpoint_depression_c is not None:
        surface = surface_trust(f, cfg) * pw(f.dewpoint_depression_c, FOG_DEP)
    parts = [p for p in (upper, surface) if p is not None]
    return max(parts) if parts else None


def _confidence(days_ahead: int) -> tuple[str, float]:
    if days_ahead <= 1:
        return "HIGH", 5.0
    if days_ahead <= 3:
        return "MEDIUM", 10.0
    return "LOW", 18.0


def score_sunrise(
    f: SunriseFeatures, settings: Settings, tuning: SpotTuning | None = None
) -> SunriseAnalysis:
    """Skóre východu; `tuning` upravuje prahy pro jiný vrchol než Arieiro (např. Bica da Cana)."""
    cfg = settings.sunrise
    tuning = tuning or SpotTuning()
    inv = analyze_inversion(f, settings.inversion, tuning.cloud_top_max_m)
    warnings: list[str] = []
    gust_curve = WIND_GUST
    if tuning.max_gusts_kmh is not None:
        shift = tuning.max_gusts_kmh - WIND_GUST[0][0]
        gust_curve = [(x + shift, y) for x, y in WIND_GUST]

    # Mraky POD vrcholem (moře mraků) pohled neblokují: nízkou oblačnost u vrcholu
    # proto tlumíme, pokud je vrchol nad horní hranicí vrstvy.
    fog_cfg = cfg.fog
    if tuning.peak_rh_max is not None:
        fog_cfg = fog_cfg.model_copy(update={"summit_rh_clear": tuning.peak_rh_max})
    clear = summit_clear_factor(f, deck_margin(f, tuning.cloud_top_max_m))
    # Přelévání mraků přes hřeben (stejná orografie ráno i večer jako u západu): hrozí-li, žádný
    # inverzní bonus – mraky pod vrcholem pohled neblokují jen tehdy, když nepřetékají. Rezerva je
    # relativní (vrchol − horní hranice mraků), takže platí pro Arieiro i Bica da Cana.
    mcfg = settings.mountain_clouds
    spill, spill_deck = None, inv.cloud_deck_top_m
    # vlhký sloupec: 850 i 800 hPa vlhké (≥ práh) = žádný suchý strop nad vrcholem (nejde o
    # inverzi), i když RH nepřekročí 85 % a horní hranice mraků se nenajde → mrak sahá výš
    if (
        f.rh.get(850) is not None
        and f.rh[850] >= mcfg.spill_hard_rh
        and (rh_aloft(f) or 0) >= mcfg.spill_hard_rh
        and f.z.get(f.aloft_hpa) is not None
    ):
        spill_deck = max(spill_deck or 0, f.z[f.aloft_hpa])
    # jen tvrdé pravidlo (RH 800 hPa ≥ práh nebo silné nárazy): plynulá rampa laděná na západy
    # by ořízla ověřenou inverzi z 10. 10. (deck 1 673 m, RH 800 hPa 45 %, nárazy 28 km/h)
    if spill_deck is not None and spillover_hard(rh_aloft(f), f.gust_kmh, mcfg):
        spill = spillover_cap(spill_deck, f.summit_m, rh_aloft(f), mcfg, f.gust_kmh)
        if spill is not None:
            clear = 0.0
    eff_low = None if f.low_cloud_summit is None else f.low_cloud_summit * (1 - clear)
    # Stejně nízká oblačnost nad mořem na východě: pohled nad ni neblokuje, jen střední vrstva.
    east_block = None
    if f.east_low_cloud is not None and f.east_mid_cloud is not None:
        east_block = max(f.east_low_cloud * (1 - clear), 0.6 * f.east_mid_cloud)
    fog = fog_likelihood(f, fog_cfg, mcfg)
    trust = surface_trust(f, fog_cfg)

    comps: list[tuple[str, float, str]] = []

    # --- sky ---
    sky_parts = [pw(v, CLEAR_SKY) for v in (eff_low, east_block) if v is not None]
    if sky_parts:
        sky = min(sky_parts)
        txt = []
        if eff_low is not None:
            txt.append(f"nízká oblačnost u vrcholu {eff_low:.0f} %")
            if clear > 0.5 and f.low_cloud_summit and f.low_cloud_summit > 30:
                txt.append(
                    f"(model hlásí {f.low_cloud_summit:.0f} % nízké oblačnosti, ale pod vrcholem)"
                )
        if east_block is not None:
            txt.append(f"východní obzor zakrytý z {east_block:.0f} %")
        comps.append(("sky", sky, ", ".join(txt)))
    else:
        comps.append(("sky", NEUTRAL, "chybí data o oblačnosti"))
        warnings.append("Chybí data o oblačnosti.")

    # --- color ---
    mc = [v for v in (f.mid_cloud, f.high_cloud) if v is not None]
    if mc:
        cc = sum(mc) / len(mc)
        comps.append(("color", pw(cc, COLOR), f"střední/vysoká oblačnost {cc:.0f} %"))
    else:
        comps.append(("color", NEUTRAL, "chybí data"))

    # --- visibility ---
    # Přízemní dohlednost a rosný bod platí jen do té míry, nakolik jim věříme (viz surface_trust);
    # jinak se dohlednost odvozuje z vlhkosti ve výšce vrcholu.
    vparts = []
    vtxt = []
    if f.visibility_km is not None:
        vparts.append(pw(f.visibility_km, VISIBILITY_KM))
        vtxt.append(f"dohlednost {f.visibility_km:.0f} km")
    if f.dewpoint_depression_c is not None:
        vparts.append(pw(f.dewpoint_depression_c, DEWPOINT_DEP))
        vtxt.append(f"rozdíl T − rosný bod {f.dewpoint_depression_c:.1f} °C")
    upper = fog_upper(f, fog_cfg, mcfg)
    if vparts and upper is not None:
        value = trust * (sum(vparts) / len(vparts)) + (1 - trust) * (1 - upper)
        if trust < 0.5:
            vtxt = [
                f"přízemní data ignorována (sucho nad vrcholem, RH 800 hPa {rh_aloft(f):.0f} %)"
            ]
    elif vparts:
        value = sum(vparts) / len(vparts)
    elif upper is not None:
        value = 1 - upper
    else:
        value = NEUTRAL
    rh_top = summit_rh(f)
    if rh_top is not None:
        vtxt.append(f"RH ve výšce vrcholu {rh_top:.0f} %")
    comps.append(("visibility", value, ", ".join(vtxt) or "chybí data"))

    # --- wind ---
    if f.gust_kmh is not None:
        comps.append(("wind", pw(f.gust_kmh, gust_curve), f"nárazy {f.gust_kmh:.0f} km/h"))
    else:
        comps.append(("wind", NEUTRAL, "chybí data"))

    # --- cloudsea (volitelný, výchozí váha 0) ---
    comps.append(
        (
            "cloudsea",
            inv.probability_pct / 100,
            f"pravděpodobnost inverze {inv.probability_pct:.0f} %",
        )
    )

    weights = cfg.weights.model_dump()
    total_w = sum(weights[name] for name, _, _ in comps)
    components = [
        ScoreComponent(
            name=name,
            value=round(value, 3),
            weight=round(weights[name] / total_w, 3),
            contribution=round(100 * value * weights[name] / total_w, 1),
            explanation=expl,
        )
        for name, value, expl in comps
        if weights[name] > 0
    ]
    raw = sum(c.contribution for c in components)

    # --- gating ---
    g_mm = pw(f.precip_mm, PRECIP_MM)
    g_prob = pw(f.precip_prob, PRECIP_PROB) if f.precip_prob is not None else 1.0
    gates = {
        "precip": round(min(g_mm, g_prob), 2),
        "fog": round(1 - fog_cfg.gate_strength * (fog or 0.0), 2),
        "gale": round(pw(f.gust_kmh, GALE) if f.gust_kmh is not None else 1.0, 2),
    }
    # zatažení střední oblačností: slunce nebude vidět, ať je vrchol nad nízkými mraky nebo ne
    if f.mid_cloud is not None:
        overcast = pw(f.mid_cloud, [(cfg.overcast_from, 1.0), (100, cfg.overcast_gate_min)])
        if overcast < 1:
            gates["overcast"] = round(overcast, 2)
            warnings.append(f"Zataženo střední oblačností ({f.mid_cloud:.0f} %) – slunce nebude vidět.")

    # základna mraků nad vrcholem + zakrytá obloha: vrchol je pod mraky, ne nad mořem mraků
    # (potvrzená inverze – vrchol jasně nad horní hranicí mraků – se nepenalizuje)
    above = inv.summit_above_deck_m
    confirmed = above is not None and above >= deck_margin(f, tuning.cloud_top_max_m)
    cover = cloud_cover(f.low_cloud_summit, f.mid_cloud)
    deck_gate = high_deck_gate(f.summit_m, f.coast_dd_c, cover, 0.0 if confirmed else trust, mcfg)
    if deck_gate < 1:
        gates["high_deck"] = round(deck_gate, 2)
        warnings.append("Základna mraků je nad vrcholem – vrchol je pod mraky, ne nad nimi.")
    # brány mlhy a mraků nad vrcholem popisují stejný jev → platí přísnější, netrestá se dvakrát
    score = raw * gates["precip"] * min(gates["fog"], deck_gate) * gates["gale"] * gates.get("overcast", 1.0)

    if spill is not None and score > spill:
        score = spill
        gates["spillover"] = round(spill / 100, 2)
        warnings.append(
            f"Mraky sahají k ~{spill_deck:.0f} m, tedy těsně pod hřeben – hrozí přelévání."
        )

    if gates["precip"] < 1:
        warnings.append(f"Srážky kolem východu: {f.precip_mm:.1f} mm (gate ×{gates['precip']}).")
    if gates["fog"] < 0.6:
        warnings.append("Vrchol bude pravděpodobně v mraku/mlze.")
    if gates["gale"] < 1:
        warnings.append(f"Silné nárazy větru ({f.gust_kmh:.0f} km/h).")

    confidence, margin = _confidence(f.days_ahead)
    verdict = (
        "GO" if score >= cfg.verdict.go else "MAYBE" if score >= cfg.verdict.maybe else "NO_GO"
    )
    summary = _summary_cs(score, verdict, inv.description_cs, components, gates)
    return SunriseAnalysis(
        target_date=f.target_date,
        sunrise_utc=f.sunrise_utc,
        score_pct=round(score, 1),
        score_range=(round(max(0, score - margin), 1), round(min(100, score + margin), 1)),
        verdict=verdict,
        confidence=confidence,
        inversion=inv,
        components=components,
        gates=gates,
        summary_cs=summary,
        warnings=warnings,
    )


_NAMES_CS = {
    "sky": "oblačnost",
    "color": "barvy oblohy",
    "visibility": "dohlednost",
    "wind": "vítr",
    "cloudsea": "moře mraků",
}


def _summary_cs(score, verdict, inv_text, components, gates) -> str:
    head = {
        "GO": "Vypadá to dobře.",
        "MAYBE": "Hazard, výsledek je nejistý.",
        "NO_GO": "Spíš zůstaň v posteli.",
    }[verdict]
    weakest = min(components, key=lambda c: c.value * 100 - 0)
    parts = [f"{head} Skóre {score:.0f} %."]
    if gates["precip"] < 1:
        parts.append("Největší problém jsou srážky.")
    elif gates["fog"] < 0.6 or gates.get("high_deck", 1.0) < 0.6:
        parts.append("Největší problém je mlha/mrak na vrcholu.")
    elif gates.get("overcast", 1.0) < 0.6:
        parts.append("Největší problém je zatažení střední oblačností.")
    elif weakest.value < 0.6:
        parts.append(f"Nejslabší stránka: {_NAMES_CS[weakest.name]} ({weakest.explanation}).")
    parts.append(inv_text)
    return " ".join(parts)


def analyze_sunrise(
    bundle: ForecastBundle,
    target_date: date,
    settings: Settings,
    now_utc: datetime,
    spot: str = "arieiro",
) -> SunriseAnalysis:
    """Východ slunce na `spot` (arieiro | bica_da_cana)."""
    f, tuning = extract_spot_features(bundle, target_date, settings, now_utc, spot)
    return score_sunrise(f, settings, tuning)


def extract_spot_features(
    bundle: ForecastBundle, target_date: date, settings: Settings, now_utc: datetime, spot: str
) -> tuple[SunriseFeatures, SpotTuning | None]:
    """Features východu a ladění heuristiky pro dané místo."""
    tuning = settings.bica_da_cana.tuning() if spot == "bica_da_cana" else None
    f = extract_sunrise_features(
        bundle,
        target_date,
        now_utc,
        settings.sunrise.window,
        summit_key=spot,
        aloft_hpa=tuning.dry_layer_hpa if tuning else 800,
    )
    return f, tuning
