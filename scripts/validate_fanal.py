"""Validace modelu Fanalu proti snímkům kamery Rabaçal (NetMadeira).

NetMadeira drží snímky jen ~7 dní, proto se ručně hodnocená realita (0 jasno / 1 cáry / 2 hustá mlha) ukládá do
`scripts/fanal_labels.json`. Skript stáhne chybějící snímky (08–18 h), spáruje je se štítky a s hodinovými hodnotami
Open-Meteo a porovná nový model s původním (přízemní RH a rosný bod, platil do 27. 10. 2026).

    python scripts/validate_fanal.py                 # souhrn úspěšnosti
    python scripts/validate_fanal.py --table         # + tabulka po hodinách
    python scripts/validate_fanal.py --out report.md # souhrn i tabulka do Markdownu
    python scripts/validate_fanal.py --montage       # kontaktní listy dnů pro hodnocení nových štítků

Pozor: modelové hodnoty pro uplynulé dny jsou přepočet z Open-Meteo, ne uložené předpovědi z minulosti.
Změny konstant ladit až na delší sérii (desítky dní), ne na jednom týdnu.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from madeira.analyzers import fanal
from madeira.analyzers.scoring_utils import pw
from madeira.domain.config import FanalConfig, load_settings
from madeira.fetchers.open_meteo import OpenMeteoFetcher

TZ = ZoneInfo("Atlantic/Madeira")
LABELS = Path(__file__).with_name("fanal_labels.json")
CACHE = ROOT / ".cache" / "fanal_snapshots"
URL = "https://www.netmadeira.com/assets/images/services/webcams/timelapses/netmadeira/rabacal-madeira/{day}/{hh}_00.jpg"
REALITY = {0: "Clear", 1: "Partial", 2: "Fog"}
VERDICT_LEVEL = {"CLEAR": 0, "PARTIAL": 1, "MYSTIC": 2}


def load_labels() -> tuple[list[int], dict[str, list[int]]]:
    raw = json.loads(LABELS.read_text(encoding="utf-8"))
    return raw["hours"], raw["labels"]


def download(days: list[str], hours: list[int]) -> int:
    """Stáhne chybějící snímky do .cache/fanal_snapshots; vrací počet nově stažených."""
    CACHE.mkdir(parents=True, exist_ok=True)
    got = 0
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        for day in days:
            for h in hours:
                path = CACHE / f"{day}-{h:02d}.jpg"
                if path.exists():
                    continue
                r = client.get(URL.format(day=day, hh=f"{h:02d}"))
                if r.status_code == 200:
                    path.write_bytes(r.content)
                    got += 1
    return got


def montage(day: str, hours: list[int]) -> Path | None:
    """Kontaktní list dne (4 sloupce) pro ruční hodnocení."""
    from PIL import Image, ImageDraw

    files = [(h, CACHE / f"{day}-{h:02d}.jpg") for h in hours if (CACHE / f"{day}-{h:02d}.jpg").exists()]
    if not files:
        return None
    w, h, cols = 480, 270, 4
    sheet = Image.new("RGB", (cols * w, ((len(files) + cols - 1) // cols) * h))
    for i, (hour, path) in enumerate(files):
        im = Image.open(path).convert("RGB").resize((w, h))
        ImageDraw.Draw(im).text((4, 2), f"{hour:02d}:00", fill="yellow")
        sheet.paste(im, ((i % cols) * w, (i // cols) * h))
    out = CACHE / f"montage-{day}.jpg"
    sheet.save(out, quality=82)
    return out


def legacy_score(p, cfg: FanalConfig) -> float:
    """Původní model (do 27. 10. 2026): přízemní RH a T − Td, vítr 8–30 km/h, žádná brána hladin ani pravidlo rozbřesku."""
    f = fanal.extract_fog_features(p, cfg)
    by_spread = 0.0
    if f.spread_c is not None:
        if f.spread_c <= 2.0:
            by_spread = 50
        elif f.spread_c <= 3.0:
            by_spread = 30
        elif f.spread_c < 4.0:
            by_spread = 30 * (4.0 - f.spread_c) / 1.0
    by_rh = 0.0
    if f.surface_rh_pct is not None:
        by_rh = 50 if f.surface_rh_pct >= 85 else 30 if f.surface_rh_pct >= 78 else 0
    dew = max(by_spread, by_rh)
    wind = 0.0
    if f.wind_dir_deg is not None:
        if fanal.in_sector(f.wind_dir_deg, cfg.wind.lee_from, cfg.wind.lee_to):
            wind = -cfg.wind.lee_penalty
        elif dew > 0 and f.wind_kmh is not None and 8 <= f.wind_kmh <= 30 and fanal.in_sector(
                f.wind_dir_deg, cfg.wind.windward_from, cfg.wind.windward_to):
            wind = cfg.wind.bonus
    vis = 0.0 if f.visibility_m is None else cfg.visibility_points * pw(f.visibility_m, cfg.curves.visibility)
    gate = pw(f.precip_mm_h, cfg.curves.precip) * (1.0 if f.gust_kmh is None else pw(f.gust_kmh, cfg.curves.gusts))
    return max(0.0, min(100.0, dew + wind + vis)) * gate


def build_rows(cfg: FanalConfig, hours: list[int], labels: dict[str, list[int]]) -> list[dict]:
    first = min(date.fromisoformat(d) for d in labels)
    past = min(92, (datetime.now(TZ).date() - first).days + 1)
    bundle = OpenMeteoFetcher(cache_dir=ROOT / ".cache").fetch([cfg.as_location()], days=1, past_days=past)
    rows = []
    for p in bundle.forecasts["fanal"].hourly:
        t = p.time_utc.astimezone(TZ)
        day = t.date().isoformat()
        if day not in labels or t.hour not in hours or hours.index(t.hour) >= len(labels[day]):
            continue
        f = fanal.extract_fog_features(p, cfg)
        new = fanal.score_fog(f, cfg)
        old = legacy_score(p, cfg)
        rows.append({
            "day": day, "hour": t.hour, "real": labels[day][hours.index(t.hour)],
            "new": new, "old": old, "new_v": VERDICT_LEVEL[fanal.verdict(new, cfg)],
            "old_v": VERDICT_LEVEL[fanal.verdict(old, cfg)],
            "rh2": p.relative_humidity_pct, "rh900": f.rh900_pct, "rh850": f.rh850_pct, "low": p.cloud_cover_low_pct,
            "wind": None if p.wind_speed_kmh is None else f"{p.wind_speed_kmh:.0f} km/h {p.wind_direction_deg:.0f}°",
        })
    return rows


def auc(rows: list[dict], key: str) -> float:
    pos = [r[key] for r in rows if r["real"] == 2]
    neg = [r[key] for r in rows if r["real"] < 2]
    return sum((a > b) + 0.5 * (a == b) for a in pos for b in neg) / (len(pos) * len(neg))


def summarize(rows: list[dict], cfg: FanalConfig, hours: list[int]) -> list[str]:
    fog = [r for r in rows if r["real"] == 2]
    clear = [r for r in rows if r["real"] == 0]
    head = (f"Hodnoceno {len(rows)} hodin ve {len({r['day'] for r in rows})} dnech "
            f"(mlha {len(fog)}, cáry {sum(r['real'] == 1 for r in rows)}, jasno {len(clear)})")
    out = [head, "", "| Metrika | Původní model | Nový model |", "|---|---|---|"]
    out += [
        f"| Hodiny s přesně trefeným verdiktem | {sum(r['old_v'] == r['real'] for r in rows)}/{len(rows)} | {sum(r['new_v'] == r['real'] for r in rows)}/{len(rows)} |",
        f"| Hustá mlha: zachycena jako MYSTIC | {sum(r['old_v'] == 2 for r in fog)}/{len(fog)} | {sum(r['new_v'] == 2 for r in fog)}/{len(fog)} |",
        f"| Hustá mlha: aspoň PARTIAL | {sum(r['old_v'] >= 1 for r in fog)}/{len(fog)} | {sum(r['new_v'] >= 1 for r in fog)}/{len(fog)} |",
        f"| Jasno: falešný poplach (PARTIAL a víc) | {sum(r['old_v'] >= 1 for r in clear)}/{len(clear)} | {sum(r['new_v'] >= 1 for r in clear)}/{len(clear)} |",
        f"| Jasno: falešný MYSTIC | {sum(r['old_v'] == 2 for r in clear)}/{len(clear)} | {sum(r['new_v'] == 2 for r in clear)}/{len(clear)} |",
        f"| AUC skóre (mlha vs. ostatní; 0,5 = náhoda) | {auc(rows, 'old'):.2f} | {auc(rows, 'new'):.2f} |",
    ]
    # den = vrchol ze slotů modelu; porovnává se s nejhorším hodnocením dne, jen pro dny s kompletními hodinami
    days = {}
    for r in rows:
        d = days.setdefault(r["day"], {"real": 0, "old": 0, "new": 0, "n": 0})
        d["real"] = max(d["real"], r["real"])
        d["n"] += 1
        if r["hour"] in cfg.slot_hours:
            d["old"] = max(d["old"], r["old_v"])
            d["new"] = max(d["new"], r["new_v"])
    full = {k: v for k, v in days.items() if v["n"] == len(hours)}
    if full:
        out.append(f"| Dny (vrchol slotů 9/13/17 h vs. nejhorší stav dne; {len(full)} kompletních dní) | "
                   f"{sum(v['old'] == v['real'] for v in full.values())}/{len(full)} | "
                   f"{sum(v['new'] == v['real'] for v in full.values())}/{len(full)} |")
    out += ["", "Dny: " + ", ".join(f"{k[5:]} {REALITY[v['real']]}→{REALITY[v['new']]}" for k, v in full.items())]
    return out


def table(rows: list[dict]) -> list[str]:
    f = lambda v: "–" if v is None else f"{v:.0f}"
    out = ["| Datum | Čas | Realita | Předpověď (nový) | Předpověď (původní) | RH_2m | RH_900hPa | RH_850hPa | Cloud_Cover_Low | Vítr |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    names = {0: "CLEAR", 1: "PARTIAL", 2: "MYSTIC"}
    for r in rows:
        out.append(f"| {r['day']} | {r['hour']:02d}:00 | {REALITY[r['real']]} | {names[r['new_v']]} {r['new']:.0f} | "
                   f"{names[r['old_v']]} {r['old']:.0f} | {f(r['rh2'])} % | {f(r['rh900'])} % | {f(r['rh850'])} % | "
                   f"{f(r['low'])} % | {r['wind'] or '–'} |")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--table", action="store_true", help="vypsat i tabulku po hodinách")
    ap.add_argument("--out", type=Path, help="zapsat souhrn a tabulku do Markdownu")
    ap.add_argument("--montage", action="store_true", help="vyrobit kontaktní listy dnů (po stažení)")
    ap.add_argument("--no-download", action="store_true", help="nestahovat snímky")
    args = ap.parse_args()

    hours, labels = load_labels()
    if not args.no_download:
        days = sorted({*labels, *(d.isoformat() for d in [datetime.now(TZ).date()])})
        print(f"Staženo nových snímků: {download(days, hours)} (cache {CACHE})")
    if args.montage:
        for day in sorted(labels):
            m = montage(day, hours)
            if m:
                print(f"Kontaktní list: {m}")

    cfg = load_settings().fanal
    rows = build_rows(cfg, hours, labels)
    lines = summarize(rows, cfg, hours)
    print("\n".join(lines))
    if args.table:
        print("\n" + "\n".join(table(rows)))
    if args.out:
        args.out.write_text("\n".join(lines + ["", *table(rows)]) + "\n", encoding="utf-8")
        print(f"\nZapsáno: {args.out}")


if __name__ == "__main__":
    main()
