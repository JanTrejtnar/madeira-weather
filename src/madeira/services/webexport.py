"""Export předpovědi východu slunce do statického JSONu pro web (GitHub Pages)."""

from __future__ import annotations

import json
import math
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from madeira.analyzers import coastal_sunrise as _coastal
from madeira.analyzers import fanal
from madeira.analyzers import sunrise as _sunrise
from madeira.analyzers import sunset as _sunset
from madeira.analyzers.coastal_sunrise import score_coastal_sunrise
from madeira.analyzers.daily_weather import day_weather
from madeira.analyzers.features import extract_sunrise_features
from madeira.analyzers.inversion import summit_rh
from madeira.analyzers.sunny_escape import rank_locations, recommendation_cs
from madeira.analyzers.sunrise import extract_spot_features, score_sunrise
from madeira.analyzers.sunset import extract_sunset_features, score_sunset
from madeira.domain.config import EscapeConfig, FanalConfig, Settings, load_locations
from madeira.domain.models import (
    OUTCOME_TO_VERDICT,
    ForecastBundle,
    SunriseAnalysis,
    SunsetAnalysis,
)
from madeira.fetchers.open_meteo import OpenMeteoFetcher
from madeira.services import forecast
from madeira.storage.db import Store

TZ = ZoneInfo("Atlantic/Madeira")
ARIEIRO_ELEVATION_M = 1818


NETMADEIRA_SNAPSHOT = (
    "https://www.netmadeira.com/assets/images/services/webcams/timelapses/netmadeira/"
    "pico-do-arieiro/{day:%Y-%m-%d}/{slot}.jpg"
)
MORNING_SNAPSHOT_SLOTS = ["08_00", "08_30"]  # ranní sloty timelapsu (půlhodinové, místní čas Madeiry)
MORNING_SNAPSHOT_SLOT = MORNING_SNAPSHOT_SLOTS[0]
SNAPSHOT_RETENTION_DAYS = 7  # NetMadeira drží snímky jen týden zpět
ARIEIRO_LAT, ARIEIRO_LON = 32.7358, -16.9291


def sunset_local(day: date, lat: float = ARIEIRO_LAT, lon: float = ARIEIRO_LON) -> datetime:
    """Přibližný západ slunce (±2 min, NOAA) v místním čase Madeiry; pro výběr slotu stačí."""
    n = day.timetuple().tm_yday
    g = 2 * math.pi / 365 * (n - 1)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    lat_r = math.radians(lat)
    cos_ha = math.cos(math.radians(90.833)) / (math.cos(lat_r) * math.cos(decl)) - math.tan(lat_r) * math.tan(decl)
    ha = math.degrees(math.acos(max(-1.0, min(1.0, cos_ha))))
    minutes_utc = 720 - 4 * (lon - ha) - eqtime
    return (datetime(day.year, day.month, day.day, tzinfo=UTC) + timedelta(minutes=minutes_utc)).astimezone(TZ)


def _slot(minutes: int) -> str:
    return f"{minutes // 60:02d}_{minutes % 60:02d}"


def sunset_snapshot_slots(day: date) -> list[str]:
    """Slot nejblíž západu slunce a ten následující (po západu, barvy oblohy), např. `19_30`, `20_00`."""
    t = sunset_local(day)
    half = round((t.hour * 60 + t.minute) / 30) * 30
    return [_slot(half), _slot(half + 30)]


def sunset_snapshot_slot(day: date) -> str:
    return sunset_snapshot_slots(day)[0]


def morning_webcam_urls(day) -> list[str]:
    """URL ranních snímků (08:00 a o půl hodiny později) timelapsu NetMadeira z Arieira (nic se nestahuje)."""
    return [NETMADEIRA_SNAPSHOT.format(day=day, slot=slot) for slot in MORNING_SNAPSHOT_SLOTS]


def morning_webcam_url(day) -> str:
    return morning_webcam_urls(day)[0]


def sunset_webcam_urls(day) -> list[str]:
    """URL večerních snímků (západ a o půl hodiny později) z Pico do Arieiro pro daný den."""
    return [NETMADEIRA_SNAPSHOT.format(day=day, slot=slot) for slot in sunset_snapshot_slots(day)]


def sunset_webcam_url(day) -> str:
    return sunset_webcam_urls(day)[0]


def _slot_time(url_slot: str) -> str:
    return url_slot.replace("_", ":")


def with_snapshot_links(history: list[dict], today, evening: bool = False) -> list[dict]:
    """Dny historie, pro které NetMadeira ještě snímek drží, dostanou `webcam_url` a `webcam_time`."""
    oldest = today - timedelta(days=SNAPSHOT_RETENTION_DAYS)
    out = []
    for row in history:
        row = {k: v for k, v in row.items() if not k.startswith("webcam_")}
        day = date.fromisoformat(row["date"])
        if oldest <= day <= today:
            urls = sunset_webcam_urls(day) if evening else morning_webcam_urls(day)
            slots = sunset_snapshot_slots(day) if evening else MORNING_SNAPSHOT_SLOTS
            row["webcam_url"], row["webcam_time"] = urls[0], _slot_time(slots[0])
            row["webcam_urls"], row["webcam_times"] = urls, [_slot_time(x) for x in slots]
        out.append(row)
    return out


def _day_entry(analysis: SunriseAnalysis, features, departure_before_min: int) -> dict:
    sunrise = analysis.sunrise_utc.astimezone(TZ)
    deck = analysis.inversion.cloud_deck_top_m
    above = analysis.inversion.summit_above_deck_m
    return {
        "date": analysis.target_date.isoformat(),
        "sunrise": f"{sunrise:%H:%M}",
        "departure_funchal": f"{sunrise - timedelta(minutes=departure_before_min):%H:%M}",
        "verdict": analysis.verdict,
        "score_pct": round(analysis.score_pct),
        "score_range": [round(analysis.score_range[0]), round(analysis.score_range[1])],
        "confidence": analysis.confidence,
        "cloud_deck_top_m": None if deck is None else round(deck),
        "summit_above_deck_m": None if above is None else round(above),
        "inversion_pct": round(analysis.inversion.probability_pct),
        "wind_kmh": None if features.wind_kmh is None else round(features.wind_kmh),
        "gust_kmh": None if features.gust_kmh is None else round(features.gust_kmh),
        "wind_dir_deg": None if features.wind_dir_deg is None else round(features.wind_dir_deg),
        "summary_cs": analysis.summary_cs,
        # Strukturovaná data, ze kterých frontend skládá souhrn v CZ/EN
        "inversion_case": _inversion_case(deck, above),
        "problem": _problem(analysis),
        "weakest": _weakest(analysis),
        "precip_mm": round(features.precip_mm, 1),
        "alerts": _alerts(analysis, features),
        "canvas_cloud_pct": _canvas_pct(features),
    }


def _coastal_sunrise_entry(analysis: SunriseAnalysis, features, departure_before_min: int) -> dict:
    """Východ na útesu: ranní klíče jako u hor (sunrise, departure), ale bez inverze; tvar horizontu jako u západu."""
    sunrise = analysis.sunrise_utc.astimezone(TZ)
    return {
        "date": analysis.target_date.isoformat(),
        "sunrise": f"{sunrise:%H:%M}",
        "departure_funchal": f"{sunrise - timedelta(minutes=departure_before_min):%H:%M}",
        "verdict": analysis.verdict,
        "score_pct": round(analysis.score_pct),
        "score_range": [round(analysis.score_range[0]), round(analysis.score_range[1])],
        "confidence": analysis.confidence,
        "horizon_low_cloud_pct": _round(features.east_low_cloud),
        "canvas_cloud_pct": _canvas_pct(features),
        "wind_kmh": _round(features.wind_kmh),
        "gust_kmh": _round(features.gust_kmh),
        "wind_dir_deg": _round(features.wind_dir_deg),
        "visibility_km": None if features.visibility_km is None else round(features.visibility_km),
        "precip_mm": round(features.precip_mm, 1),
        "summary_cs": analysis.summary_cs,
        "problem": "precip" if analysis.gates["precip"] < 1 else (
            "horizon" if analysis.gates["horizon"] < 0.6 else None
        ),
        "weakest": _weakest(analysis),
        "warnings": analysis.warnings,
        "alerts": _coastal_sunrise_alerts(analysis, features),
    }


def _coastal_sunrise_alerts(a: SunriseAnalysis, f) -> list[dict]:
    alerts = []
    if a.gates["precip"] < 1:
        alerts.append({"code": "precip", "value": round(f.precip_mm, 1)})
    if a.gates["horizon"] < 1 and f.east_low_cloud is not None:
        alerts.append({"code": "horizon_east", "value": round(f.east_low_cloud)})
    if f.gust_kmh is not None and f.gust_kmh > 40:
        alerts.append({"code": "gusts", "value": round(f.gust_kmh)})
    if f.east_low_cloud is None and f.mid_cloud is None and f.high_cloud is None:
        alerts.append({"code": "no_cloud_data"})
    return alerts


def _canvas_pct(features) -> int | None:
    """Střední + vysoká oblačnost (průměr) jako "plátno" pro barvy, stejně jako ve skóre."""
    vals = [v for v in (features.mid_cloud, features.high_cloud) if v is not None]
    return round(sum(vals) / len(vals)) if vals else None


def _sunset_entry(analysis: SunsetAnalysis, features, departure_min: int) -> dict:
    sunset = analysis.sunset_utc.astimezone(TZ)
    return {
        "date": analysis.target_date.isoformat(),
        "sunset": f"{sunset:%H:%M}",
        "departure_funchal": f"{sunset - timedelta(minutes=departure_min):%H:%M}",
        "verdict": analysis.verdict,
        "score_pct": round(analysis.score_pct),
        "score_range": [round(analysis.score_range[0]), round(analysis.score_range[1])],
        "confidence": analysis.confidence,
        "horizon_low_cloud_pct": _round(features.horizon_low_cloud),
        "canvas_cloud_pct": _round(features.canvas_cloud),
        "wind_kmh": _round(features.wind_kmh),
        "gust_kmh": _round(features.gust_kmh),
        "wind_dir_deg": _round(features.wind_dir_deg),
        "visibility_km": None if features.visibility_m is None else round(features.visibility_m / 1000),
        "precip_mm": round(features.precip_mm, 1),
        "summary_cs": analysis.summary_cs,
        "problem": "precip" if analysis.gates["precip"] < 1 else (
            "horizon" if analysis.gates["horizon"] < 0.6 else None
        ),
        "weakest": _weakest(analysis),
        "warnings": analysis.warnings,
        "alerts": _sunset_alerts(analysis, features),
    }


def _sunset_alerts(a: SunsetAnalysis, f) -> list[dict]:
    alerts = []
    if a.gates["precip"] < 1:
        alerts.append({"code": "precip", "value": round(f.precip_mm, 1)})
    if a.gates["horizon"] < 1 and f.horizon_low_cloud is not None:
        alerts.append({"code": "horizon", "value": round(f.horizon_low_cloud)})
    if a.gates["visibility"] < 1 and f.visibility_m is not None:
        alerts.append({"code": "visibility", "value": round(f.visibility_m / 1000)})
    if f.horizon_low_cloud is None and f.canvas_cloud is None:
        alerts.append({"code": "no_cloud_data"})
    return alerts


def _spot_summary(spot: dict, kind: str, history: list[dict] | None = None) -> dict:
    """Jednotný tvar pro kořenové klíče `sunrise` a `sunset` v data.json."""
    cloud_sea = None
    if kind == "sunrise" and spot["type"] == "mountain_inversion":  # moře mraků jen na horské vyhlídce
        cloud_sea = {
            "probability_pct": spot["inversion_pct"],
            "deck_top_m": spot["cloud_deck_top_m"],
            "above_deck_m": spot["summit_above_deck_m"],
            "case": spot["inversion_case"],
        }
    return {
        "location": {
            k: spot[k] for k in ("key", "name", "type", "lat", "lon", "elevation_m")
        },
        "webcam_url": spot["webcam_url"],
        "date": spot["date"],
        "time": spot[kind],
        "departure": spot["departure_funchal"],
        "verdict": spot["verdict"],
        "score": spot["score_pct"],
        "score_range": spot["score_range"],
        "confidence": spot["confidence"],
        "cloud_sea": cloud_sea,
        "wind": {
            "kmh": spot["wind_kmh"],
            "gust_kmh": spot["gust_kmh"],
            "dir_deg": spot["wind_dir_deg"],
        },
        "clouds_canvas": {"pct": spot["canvas_cloud_pct"]},
        "horizon_low_cloud_pct": spot.get("horizon_low_cloud_pct"),
        "visibility_km": spot.get("visibility_km"),
        "summary": spot["summary_cs"],
        "problem": spot["problem"],
        "weakest": spot["weakest"],
        "alerts": spot["alerts"],
        "history": history or [],
    }


def _round(value: float | None) -> int | None:
    return None if value is None else round(value)


def _spot_meta(settings: Settings, bundle: ForecastBundle, key: str) -> dict:
    loc = bundle.forecasts[settings.spots[key].location or key].location
    return {
        "key": key,
        "name": settings.spots[key].name,
        "type": settings.spots[key].type,
        "lat": loc.lat,
        "lon": loc.lon,
        "elevation_m": round(loc.elevation_m),
        "webcam_url": settings.spots[key].webcam_url,
    }


MORNING_SPOTS = ("arieiro", "pico_ruivo", "bica_da_cana", "ponta_do_rosto")
EVENING_SPOTS = ("ponta_do_pargo", "cristo_rei", "arieiro_sunset", "pico_ruivo_sunset")


def spot_entry(settings: Settings, bundle: ForecastBundle, key: str, day, now: datetime) -> dict:
    """Předpověď jednoho spotu na jeden den (ranní nebo večerní tvar podle spotu)."""
    departure = settings.spots[key].departure_from_funchal_min
    if key in EVENING_SPOTS:
        spot = settings.spots[key]
        features = extract_sunset_features(
            bundle, day, now, settings, spot_key=spot.location or key,
            mountain=spot.type == "mountain_sunset",
        )
        return _sunset_entry(score_sunset(features, settings), features, departure)
    features, tuning = extract_spot_features(bundle, day, settings, now, key)
    if settings.spots[key].type == "coastal_sunrise":
        return _coastal_sunrise_entry(score_coastal_sunrise(features, settings), features, departure)
    return _day_entry(score_sunrise(features, settings, tuning), features, departure)


def tomorrow_spots(settings: Settings, bundle: ForecastBundle, day, now: datetime) -> dict:
    """Zítřejší ranní (Arieiro, Pico Ruivo, Bica da Cana, Ponta do Rosto) a večerní vyhlídky."""
    def one(key):
        return _spot_meta(settings, bundle, key) | spot_entry(settings, bundle, key, day, now)

    return {"morning": [one(k) for k in MORNING_SPOTS], "evening": [one(k) for k in EVENING_SPOTS]}


def spot_days(settings: Settings, bundle: ForecastBundle, first, days: int, now: datetime) -> dict:
    """Výhled na `days` dní pro každý spot + počasí během dne (Funchal a samotný spot)."""
    out: dict[str, list[dict]] = {}
    for key in (*MORNING_SPOTS, *EVENING_SPOTS):
        loc = settings.spots[key].location or key
        available = {d.date for d in bundle.forecasts[loc].daily}
        rows = []
        for offset in range(days):
            day = first + timedelta(days=offset)
            if day not in available:
                break
            entry = spot_entry(settings, bundle, key, day, now)
            entry["weather"] = day_weather(bundle, day, ("funchal", loc))
            rows.append(entry)
        out[key] = rows
    return out



def _inversion_case(deck: float | None, above: float | None) -> str:
    if deck is None:
        return "none"
    if above is not None and above > 100:
        return "above"
    if above is not None and above > -200:
        return "edge"
    return "inside"


def _weakest(a: SunriseAnalysis) -> str | None:
    comp = min(a.components, key=lambda c: c.value, default=None)
    return comp.name if comp and comp.value < 0.6 else None


def _problem(a: SunriseAnalysis) -> str | None:
    if a.gates["precip"] < 1:
        return "precip"
    if a.gates["fog"] < 0.6:
        return "fog"
    return None


def _alerts(a: SunriseAnalysis, f) -> list[dict]:
    alerts = []
    if a.gates["precip"] < 1:
        alerts.append({"code": "precip", "value": round(f.precip_mm, 1)})
    if a.gates["fog"] < 0.6:
        alerts.append({"code": "fog"})
    if a.gates["gale"] < 1 and f.gust_kmh is not None:
        alerts.append({"code": "gusts", "value": round(f.gust_kmh)})
    if f.low_cloud_summit is None and f.mid_cloud is None and f.high_cloud is None:
        alerts.append({"code": "no_cloud_data"})
    return alerts


def sunny_escape_section(cfg: EscapeConfig, bundle: ForecastBundle, now: datetime) -> dict | None:
    """Nejslunnější pobřeží pro dnešek a zítřek; `winner`/`locations` platí pro výchozí den."""
    local = now.astimezone(TZ)
    days = {"today": local.date(), "tomorrow": local.date() + timedelta(days=1)}
    out: dict = {}
    for label, day in days.items():
        rows = rank_locations(bundle, day, cfg)
        if rows:
            best = rows[0]
            winner = {
                "key": best["key"], "name": best["name"], "name_gen": best["name_gen"], "sun_score": best["sun_score"],
                "temp_c": best["temp_c"], "precip_mm": best["precip_mm"], "sky": best["sky"],
                "recommendation": recommendation_cs(label, best),
            }
            out[label] = {"date": day.isoformat(), "winner": winner, "locations": rows}
    if not out:
        return None
    # po konci okna (16:00) už dnešek nemá cenu doporučovat
    default = "today" if "today" in out and local.hour < cfg.hour_to else "tomorrow"
    default = default if default in out else next(iter(out))
    return {"window": [cfg.hour_from, cfg.hour_to], "day": default, **out[default], **out}


def fetch_escape_bundle(cfg: EscapeConfig) -> ForecastBundle | None:
    try:
        return OpenMeteoFetcher(cache_dir=forecast.CACHE_DIR).fetch([p.as_location() for p in cfg.points], days=2)
    except RuntimeError:
        return None  # výpadek API nesmí shodit celý export


def _fanal_summary(verdict: str, f: dict, cfg: FanalConfig) -> str:
    c = cfg.curves
    if verdict == "MYSTIC":
        wind = "slabý vítr" if f["wind_gusts"] <= 20 else "mírný vítr"
        return f"Hustá mlha sedí přímo mezi stromy, {wind} – vezměte foťák!"
    if verdict == "PARTIAL":
        return "Převalující se cáry mlhy, mezi nimi občasné protrhání."
    if f["rain"] >= c.precip[-1][0]:
        return "Silný déšť sráží mlhu i náladu – spíš jen mokro než mystika."
    if f["wind_gusts"] > c.gusts[0][0]:
        return "Silný vítr mlhu rozfoukává, atmosféra se nekoná."
    return "Jasno nebo sucho – obyčejný zelený park bez atmosféry."


def _fanal_day(cfg: FanalConfig, points, day, from_hour: int = 0) -> dict | None:
    """Souhrn jednoho dne ze slotů (9/13/17 h): skóre = průměr slotů, nárazy a srážky = maximum."""
    slots = fanal.slot_rows(points, day, cfg, from_hour)
    if not slots:
        return None
    feats = [r["f"] for r in slots]

    def mean(vals):
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals)) if vals else None

    score = round(sum(fanal.score_fog(f, cfg) for f in feats) / len(feats))
    out = {
        "date": day.isoformat(),
        "verdict": fanal.verdict(score, cfg),
        "score": score,
        "humidity": mean(f.humidity_pct for f in feats),
        "cloud_coverage_local": mean(f.cloud_cover_pct for f in feats),
        "cloud_base_m": mean(f.cloud_base_m for f in feats),
        "visibility_m": mean(f.visibility_m for f in feats),
        "wind_gusts": round(max((f.gust_kmh for f in feats if f.gust_kmh is not None), default=0)),
        "rain": round(max(f.precip_mm_h for f in feats), 1),
    }
    window = fanal.best_window(points, day, cfg, from_hour)
    out["best_window_hours"] = list(window) if window else None
    out["best_window"] = _fanal_window_cs(window)
    out["summary"] = _fanal_summary(out["verdict"], out, cfg)
    out["slots"] = [{"hour": r["hour"], "score": r["score"], "verdict": r["verdict"]} for r in slots]
    return out


def _fanal_window_cs(window: tuple[int, int] | None) -> str | None:
    if window is None:
        return None
    start, end = window
    part = "Dopoledne" if start < 12 else "Odpoledne" if start < 17 else "Večer"
    return f"{part} {start:02d}:00–{end:02d}:00"


def fanal_section(cfg: FanalConfig, bundle: ForecastBundle, now: datetime) -> dict | None:
    """Fanal Fog Index pro dnešek (zbývající sloty) a zítřek; kořen platí pro výchozí den."""
    fc = bundle.forecasts.get("fanal")
    if fc is None:
        return None
    local = now.astimezone(TZ)
    today = _fanal_day(cfg, fc.hourly, local.date(), local.hour)
    tomorrow = _fanal_day(cfg, fc.hourly, local.date() + timedelta(days=1))
    days = {k: v for k, v in (("today", today), ("tomorrow", tomorrow)) if v}
    if not days:
        return None
    default = "today" if today else "tomorrow"  # dnešek bez zbývajících slotů se nedoporučuje
    return {
        "location": {"key": "fanal", "name": cfg.name, "type": cfg.type, "lat": cfg.lat, "lon": cfg.lon,
                     "elevation_m": round(cfg.elevation_m)},
        "webcam_url": cfg.webcam_url,
        "day": default,
        **days[default],
        **days,
    }


def fetch_fanal_bundle(cfg: FanalConfig) -> ForecastBundle | None:
    try:
        loc = cfg.as_location()
        return OpenMeteoFetcher(cache_dir=forecast.CACHE_DIR).fetch([loc], days=2)
    except RuntimeError:
        return None  # výpadek API nesmí shodit celý export


def build_payload(
    settings: Settings,
    days: int = 3,
    bundle: ForecastBundle | None = None,
    now: datetime | None = None,
    history: list[dict] | None = None,
    store: Store | None = None,
    escape_bundle: ForecastBundle | None = None,
    fanal_bundle: ForecastBundle | None = None,
    spot_history: dict[str, list[dict]] | None = None,
) -> dict:
    """Spočítá zítřek a výhled na `days` dní (první den = zítřek, místní čas Madeiry)."""
    live = bundle is None
    bundle = bundle or forecast.load_bundle()
    now = now or datetime.now(UTC)
    if escape_bundle is None and live:  # s předaným bundlem (testy) se nic nestahuje
        escape_bundle = fetch_escape_bundle(settings.sunny_escape)
    if fanal_bundle is None and live:
        fanal_bundle = fetch_fanal_bundle(settings.fanal)
    first = now.astimezone(TZ).date() + timedelta(days=1)
    available = {d.date for d in bundle.forecasts["arieiro"].daily}
    entries = []
    for offset in range(days):
        day = first + timedelta(days=offset)
        if day not in available:
            break
        features = extract_sunrise_features(bundle, day, now, settings.sunrise.window)
        analysis = score_sunrise(features, settings)
        if store is not None:  # predikce se zapíše, ať se den objeví v historii
            store.log_prediction(
                analysis, features, bundle.fetched_at_utc, bundle.model,
                settings.sunrise.weights.model_dump(),
            )
        entry = _day_entry(analysis, features, settings.bot.departure_before_min)
        entry["weather"] = day_weather(bundle, day)
        entries.append(entry)
    if not entries:
        raise RuntimeError("Model nevrátil data pro zítřek.")
    spots = tomorrow_spots(settings, bundle, first, now)
    today_utc = now.astimezone(UTC).date()

    def hist(key: str) -> list[dict]:
        rows = (spot_history or {}).get(key, [])
        if key == "arieiro_sunset":
            return with_snapshot_links(rows, today_utc, evening=True)
        return with_snapshot_links(rows, today_utc) if key == "arieiro" else rows  # snímky jen z Arieira

    escape = sunny_escape_section(settings.sunny_escape, escape_bundle, now) if escape_bundle else None
    fog = fanal_section(settings.fanal, fanal_bundle, now) if fanal_bundle else None
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "model": bundle.model,
        "location": {"name": "Pico do Arieiro", "elevation_m": ARIEIRO_ELEVATION_M},
        "morning_webcam_url": morning_webcam_url(now.astimezone(UTC).date()),
        "morning_webcam_date": now.astimezone(UTC).date().isoformat(),
        "morning_webcam_time": _slot_time(MORNING_SNAPSHOT_SLOT),
        "morning_webcam_urls": morning_webcam_urls(now.astimezone(UTC).date()),
        "morning_webcam_times": [_slot_time(x) for x in MORNING_SNAPSHOT_SLOTS],
        "sunset_webcam_url": sunset_webcam_url(now.astimezone(UTC).date()),
        "sunset_webcam_time": _slot_time(sunset_snapshot_slot(now.astimezone(UTC).date())),
        "sunset_webcam_urls": sunset_webcam_urls(now.astimezone(UTC).date()),
        "sunset_webcam_times": [_slot_time(x) for x in sunset_snapshot_slots(now.astimezone(UTC).date())],
        "tomorrow": entries[0],
        "tomorrow_spots": spots,
        # kořen = výchozí spot (zpětná kompatibilita), `spots` = všechna místa podle klíče
        "sunrise": _spot_summary(spots["morning"][0], "sunrise", hist(spots["morning"][0]["key"]))
        | {"spots": {s["key"]: _spot_summary(s, "sunrise", hist(s["key"])) for s in spots["morning"]}},
        "sunset": _spot_summary(spots["evening"][0], "sunset", hist(spots["evening"][0]["key"]))
        | {"spots": {s["key"]: _spot_summary(s, "sunset", hist(s["key"])) for s in spots["evening"]}},
        "days": entries,
        "spot_days": spot_days(settings, bundle, first, days, now),
        "history": with_snapshot_links(history or [], now.astimezone(UTC).date()),
        **({"sunny_escape": escape} if escape else {}),
        **({"fanal": fog} if fog else {}),
    }


HISTORY_SPOT_DAYS = 7  # kolik dní historie nese každý spot v `sunrise.spots` / `sunset.spots`
CLOUD_BASE_M_PER_C = 125  # základna mraků ≈ 125 m nad místem za každý °C rozdílu T − rosný bod (jako u Fanalu)

_COMPONENT_NAMES_CS = {**_sunrise._NAMES_CS, **_sunset._NAMES_CS, **_coastal._NAMES_CS}
_PROBLEM_CS = {
    "precip": "Srážky kolem události",
    "fog": "Vrchol v mraku/mlze",
    "horizon": "Nízké mraky na obzoru",
}


def _json(text: str | None, default):
    try:
        return json.loads(text) if text else default
    except ValueError:
        return default


def _history_reason(row: dict) -> dict | None:
    """Strukturovaný důvod skóre z uložených komponent a bran (frontend z něj skládá CZ/EN text)."""
    comps = _json(row.get("components_json"), [])
    gates = _json(row.get("gates_json"), {})
    if not comps:
        return None
    problem = next(
        (name for name, limit in (("precip", 1), ("fog", 0.6), ("horizon", 0.6))
         if gates.get(name, 1) < limit),
        None,
    )
    weakest = min(comps, key=lambda c: c["value"])
    return {"problem": problem, "weakest": weakest["name"] if weakest["value"] < 0.6 else None}


def _history_summary(reason: dict | None, deck: float | None) -> str | None:
    if reason is None:
        return None
    if reason["problem"]:
        text = _PROBLEM_CS[reason["problem"]]
    elif reason["weakest"]:
        text = f"Nejslabší stránka: {_COMPONENT_NAMES_CS.get(reason['weakest'], reason['weakest'])}"
    else:
        text = "Bez výraznějšího problému"
    return text if deck is None else f"{text} · hladina mraků ~{round(deck)} m"


def _num(value, digits: int = 0):
    return None if value is None else round(value, digits) if digits else round(value)


def _history_detail(row: dict, elevation_m: float | None) -> dict:
    """Naměřené (předpovězené) parametry dne z uložených features; chybějící hodnoty jsou None."""
    f = _json(row.get("features_json"), {})
    rh = {int(k): v for k, v in (f.get("rh") or {}).items()}
    z = {int(k): v for k, v in (f.get("z") or {}).items()}
    summit = f.get("summit_m") or elevation_m
    mountain = bool(summit and summit > 1000 and f.get("rh"))  # profil atmosféry dává smysl jen u hor
    peak_rh = summit_rh(SimpleNamespace(rh=rh, z=z, summit_m=summit)) if mountain else None
    deck = row.get("deck_top_m")
    above = None if deck is None or not summit else summit - deck
    dd = f.get("dewpoint_depression_c")
    # odhad základny dává smysl, jen když nemáme horní hranici vrstvy pod místem (u inverze by byl nad vrcholem)
    base = None if dd is None or not summit or deck is not None else (
        round((summit + CLOUD_BASE_M_PER_C * max(dd, 0)) / 10) * 10
    )
    mids = [v for v in (f.get("mid_cloud"), f.get("high_cloud")) if v is not None]
    vis_km = f.get("visibility_km")
    if vis_km is None and f.get("visibility_m") is not None:
        vis_km = f["visibility_m"] / 1000
    return {
        "time": _local_time(row.get("sunrise_utc")),
        "cloud_base": base,  # odhad z T − rosný bod
        "cloud_top": _num(deck),
        "above_deck_m": _num(above),
        "inversion_case": _inversion_case(deck, above) if mountain else None,
        "cloud_cover_low": _num(f.get("low_cloud_summit", f.get("local_low_cloud"))),
        "cloud_cover_high": _num(f.get("high_cloud")),
        "canvas_cloud": _num(sum(mids) / len(mids)) if mids else _num(f.get("canvas_cloud")),
        "horizon_low_cloud": _num(f.get("east_low_cloud", f.get("horizon_low_cloud"))),
        "humidity_peak": _num(peak_rh),
        "wind_speed": _num(f.get("wind_kmh")),
        "wind_gusts": _num(f.get("gust_kmh")),
        "temp": _num(f.get("temp_c"), 1),
        "visibility_km": _num(vis_km),
    }


def _local_time(iso: str | None) -> str | None:
    if not iso:
        return None
    return f"{datetime.fromisoformat(iso).replace(tzinfo=UTC).astimezone(TZ):%H:%M}"


def history_entries(rows: list[dict], elevation_m: float | None = None) -> list[dict]:
    """Řádky historie (predikce + případné hodnocení + detailní parametry) ve slim podobě pro web.

    `score` je hlavní pole; `score_pct` zůstává kvůli starším klientům a popupu ranního snímku."""
    out = []
    for r in rows:
        reason = _history_reason(r)
        score = None if r["score"] is None else round(r["score"])
        out.append({
            "date": r["target_date"],
            "score": score,
            "score_pct": score,
            "verdict": r["verdict"],
            "summary": _history_summary(reason, r.get("deck_top_m")),
            "reason": reason,
            "inversion_pct": None if r["inversion_pct"] is None else round(r["inversion_pct"]),
            "outcome": r["outcome"],
            "actual_verdict": OUTCOME_TO_VERDICT.get(r["outcome"]) if r["outcome"] else None,
            "source": "backfill" if r["model"] == "historical_forecast" else (
                "forecast" if r["model"] else None
            ),
            **_history_detail(r, elevation_m),
        })
    return out


def collect_spot_history(
    settings: Settings,
    store: Store,
    today: date,
    previous: Path | None = None,
    days: int = HISTORY_SPOT_DAYS,
) -> dict[str, list[dict]]:
    """Posledních `days` dní historie pro každý spot. Chybějící dny se doplní z archivovaných
    předpovědí (backfill); při výpadku API zůstane historie z předchozího exportu."""
    keys = (*MORNING_SPOTS, *EVENING_SPOTS)
    by_location = {loc.key: loc.elevation_m for loc in load_locations()}
    elevations = {k: by_location.get(settings.spots[k].location or k) for k in keys}

    def rows_of(key: str) -> list[dict]:
        return store.history_rows(today, limit=days, location=key)

    missing = [k for k in keys if len(rows_of(k)) < days]
    if missing:
        try:
            forecast.fetch_history(settings, store, missing, days=days)
        except RuntimeError:
            pass  # bez archivu se použije, co je v DB / v předchozím exportu
    old = read_spot_history(previous) if previous else {}
    return {
        k: history_entries(rows_of(k), elevations.get(k)) or old.get(k, []) for k in keys
    }


def read_spot_history(path: Path) -> dict[str, list[dict]]:
    """Per-spot historie z dříve exportovaného JSONu (prázdná, pokud soubor chybí nebo je rozbitý)."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return {
            k: v.get("history", [])
            for part in ("sunrise", "sunset")
            for k, v in data[part].get("spots", {}).items()
        }
    except (OSError, ValueError, KeyError, AttributeError):
        return {}


def read_history(path: Path) -> list[dict]:
    """Historie z dříve exportovaného JSONu (prázdná, pokud soubor chybí nebo je rozbitý)."""
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("history", [])
    except (OSError, ValueError, AttributeError):
        return []


def write_payload(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
