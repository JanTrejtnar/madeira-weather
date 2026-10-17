"""Export předpovědi východu slunce do statického JSONu pro web (GitHub Pages)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from madeira.analyzers import fanal
from madeira.analyzers.coastal_sunrise import score_coastal_sunrise
from madeira.analyzers.daily_weather import day_weather
from madeira.analyzers.features import extract_sunrise_features
from madeira.analyzers.sunny_escape import rank_locations, recommendation_cs
from madeira.analyzers.sunrise import extract_spot_features, score_sunrise
from madeira.analyzers.sunset import extract_sunset_features, score_sunset
from madeira.domain.config import EscapeConfig, FanalConfig, Settings
from madeira.domain.models import ForecastBundle, SunriseAnalysis, SunsetAnalysis
from madeira.fetchers.open_meteo import OpenMeteoFetcher
from madeira.services import forecast
from madeira.storage.db import Store

TZ = ZoneInfo("Atlantic/Madeira")
ARIEIRO_ELEVATION_M = 1818


NETMADEIRA_SNAPSHOT = (
    "https://www.netmadeira.com/assets/images/services/webcams/timelapses/netmadeira/"
    "pico-do-arieiro/24h/{:%Y_%m_%d}_{}.jpg"
)
MORNING_SNAPSHOT_SLOT = "08_00"  # ranní slot statických snímků timelapsu (UTC)


def morning_webcam_url(day) -> str:
    """URL ranního snímku timelapsu NetMadeira z Pico do Arieiro pro daný den (nic se nestahuje)."""
    return NETMADEIRA_SNAPSHOT.format(day, MORNING_SNAPSHOT_SLOT)


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


def _spot_summary(spot: dict, kind: str) -> dict:
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
    }


def _round(value: float | None) -> int | None:
    return None if value is None else round(value)


def _spot_meta(settings: Settings, bundle: ForecastBundle, key: str) -> dict:
    loc = bundle.forecasts[key].location
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
EVENING_SPOTS = ("ponta_do_pargo", "cristo_rei")


def spot_entry(settings: Settings, bundle: ForecastBundle, key: str, day, now: datetime) -> dict:
    """Předpověď jednoho spotu na jeden den (ranní nebo večerní tvar podle spotu)."""
    departure = settings.spots[key].departure_from_funchal_min
    if key in EVENING_SPOTS:
        features = extract_sunset_features(bundle, day, now, settings)
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
        available = {d.date for d in bundle.forecasts[key].daily}
        rows = []
        for offset in range(days):
            day = first + timedelta(days=offset)
            if day not in available:
                break
            entry = spot_entry(settings, bundle, key, day, now)
            entry["weather"] = day_weather(bundle, day, ("funchal", key))
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
    escape = sunny_escape_section(settings.sunny_escape, escape_bundle, now) if escape_bundle else None
    fog = fanal_section(settings.fanal, fanal_bundle, now) if fanal_bundle else None
    return {
        "generated_at": now.isoformat(timespec="seconds"),
        "model": bundle.model,
        "location": {"name": "Pico do Arieiro", "elevation_m": ARIEIRO_ELEVATION_M},
        "morning_webcam_url": morning_webcam_url(now.astimezone(UTC).date()),
        "morning_webcam_date": now.astimezone(UTC).date().isoformat(),
        "tomorrow": entries[0],
        "tomorrow_spots": spots,
        # kořen = výchozí spot (zpětná kompatibilita), `spots` = všechna místa podle klíče
        "sunrise": _spot_summary(spots["morning"][0], "sunrise")
        | {"spots": {s["key"]: _spot_summary(s, "sunrise") for s in spots["morning"]}},
        "sunset": _spot_summary(spots["evening"][0], "sunset")
        | {"spots": {s["key"]: _spot_summary(s, "sunset") for s in spots["evening"]}},
        "days": entries,
        "spot_days": spot_days(settings, bundle, first, days, now),
        "history": history or [],
        **({"sunny_escape": escape} if escape else {}),
        **({"fanal": fog} if fog else {}),
    }


def history_entries(rows: list[dict]) -> list[dict]:
    """Řádky pohledu `calibration` (predikce vs. hodnocení) ve slim podobě pro web."""
    return [
        {
            "date": r["target_date"],
            "score_pct": None if r["score"] is None else round(r["score"]),
            "verdict": r["verdict"],
            "inversion_pct": None if r["inversion_pct"] is None else round(r["inversion_pct"]),
            "outcome": r["outcome"],
            "source": "backfill" if r["model"] == "historical_forecast" else (
                "forecast" if r["model"] else None
            ),
        }
        for r in rows
    ]


def read_history(path: Path) -> list[dict]:
    """Historie z dříve exportovaného JSONu (prázdná, pokud soubor chybí nebo je rozbitý)."""
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("history", [])
    except (OSError, ValueError, AttributeError):
        return []


def write_payload(payload: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
