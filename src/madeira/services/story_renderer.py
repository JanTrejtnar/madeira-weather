"""Instagram Story 9:16 (1080×1920) s předpovědí na nejbližší ráno.

Vstupem je payload z `webexport.build_payload`. Kreslí se Pillow (stejný stack jako OG náhled),
takže běží i v GitHub Actions bez prohlížeče. Instagram překrývá horních a dolních 250 px UI prvky,
proto je veškerý obsah uvnitř safe zóny."""

from __future__ import annotations

import io
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from madeira.services.story_image import (
    BG_BOTTOM,
    BG_TOP,
    FUCHSIA,
    MUTED,
    ORANGE,
    SITE_URL,
    TEXT,
    VERDICT_COLORS,
    VERDICT_LABELS,
    font,
)

TZ = ZoneInfo("Atlantic/Madeira")
SIZE = (1080, 1920)
SAFE_TOP, SAFE_BOTTOM = 250, 250
PAD = 70
INK = (2, 6, 23)
FANAL_COLORS = {"MYSTIC": (167, 139, 250), "PARTIAL": (56, 189, 248), "CLEAR": MUTED}
MONTHS_CS = ("ledna", "února", "března", "dubna", "května", "června", "července", "srpna", "září",
             "října", "listopadu", "prosince")
WEEKDAYS_CS = ("Pondělí", "Úterý", "Středa", "Čtvrtek", "Pátek", "Sobota", "Neděle")
MINI_SPOTS =("bica_da_cana", "ponta_do_rosto")  # Ponta do Pargo je večerní spot, bere se z `evening`
GLASS_FILL = (30, 41, 59, 150)
GLASS_EDGE = (148, 163, 184, 60)


def _background() -> Image.Image:
    """Uhlový svislý gradient s jemnou oranžovou září u horizontu a fialovou nahoře."""
    w, h = SIZE
    img = Image.new("RGB", SIZE, BG_TOP)
    px = ImageDraw.Draw(img)
    for y in range(h):
        k = y / (h - 1)
        px.line([(0, y), (w, y)], fill=tuple(round(a + (b - a) * k) for a, b in zip(BG_TOP, BG_BOTTOM)))
    glow = Image.new("RGB", SIZE, (0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.ellipse([-300, -400, 700, 400], fill=(60, 30, 110))
    g.ellipse([380, 1250, 1500, 2300], fill=(120, 55, 20))
    glow = glow.filter(ImageFilter.GaussianBlur(220)).point(lambda v: int(v * 0.45))
    return ImageChops.add(img, glow)


def _glass(img: Image.Image, box: tuple[int, int, int, int], radius: int = 44, accent=None) -> None:
    """Poloprůhledná „skleněná“ karta s tenkým světlým okrajem."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle(box, radius=radius, fill=GLASS_FILL, outline=GLASS_EDGE, width=2)
    if accent:
        x0, y0, _, y1 = box
        d.rounded_rectangle([x0, y0 + 36, x0 + 8, y1 - 36], radius=4, fill=accent)
    img.paste(Image.alpha_composite(img.convert("RGBA"), layer).convert("RGB"))


def _t(d: ImageDraw.ImageDraw, xy, s, f, fill=TEXT, anchor="la") -> None:
    d.text(xy, s, font=f, fill=fill, anchor=anchor)


def _day_label(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{WEEKDAYS_CS[d.weekday()]}, {d.day}. {MONTHS_CS[d.month - 1]}"


def _tz_name(iso: str) -> str:
    """WET (zima) / WEST (letní čas) podle data."""
    return datetime.fromisoformat(iso).replace(hour=12, tzinfo=TZ).tzname() or "WET"


def _signed(m: int | None) -> str:
    return "—" if m is None else f"{m:+d} m".replace("-", "−")


def _verdict_label(verdict: str) -> str:
    return VERDICT_LABELS.get(verdict, verdict)


def _find(spots: list[dict], key: str) -> dict | None:
    return next((s for s in spots if s["key"] == key), None)


def _conditions(spot: dict) -> str:
    """Jednořádkový souhrn: inverze / oblačnost."""
    above = spot.get("summit_above_deck_m")
    if above is not None:
        return f"Moře mraků: vrchol {above} m nad mraky" if above >= 0 else f"Vrchol v mracích ({_signed(above)})"
    canvas = spot.get("canvas_cloud_pct")
    return "Bez inverze" if canvas is None else f"Bez inverze · oblačnost na barvy {canvas} %"


def _hero(img: Image.Image, spot: dict, top: int, bottom: int) -> None:
    _glass(img, (PAD - 20, top, SIZE[0] - PAD + 20, bottom), accent=ORANGE)
    d = ImageDraw.Draw(img)
    x = PAD + 30
    _t(d, (x, top + 38), "PICO DO ARIEIRO · 1818 m", font(28, True), ORANGE)
    color = VERDICT_COLORS.get(spot["verdict"], MUTED)
    _t(d, (x, top + 90), _verdict_label(spot["verdict"]), font(168, True), color)
    _t(d, (SIZE[0] - PAD - 30, top + 118), f"{spot['score_pct']} %", font(118, True), anchor="ra")
    _t(d, (SIZE[0] - PAD - 30, top + 250), "skóre", font(30), MUTED, "ra")
    # tři čísla: výška mraků, vítr, nárazy
    deck = spot.get("cloud_deck_top_m")
    stats = [
        ("Vrchol mraků", "—" if deck is None else f"{deck} m"),
        ("Vítr", f"{spot['wind_kmh']} km/h"),
        ("Nárazy", f"{spot['gust_kmh']} km/h"),
    ]
    gap = 22
    w = (SIZE[0] - 2 * PAD - 60 - 2 * gap) // 3
    y = top + 330
    for i, (label, value) in enumerate(stats):
        x0 = x + i * (w + gap)
        d.rounded_rectangle([x0, y, x0 + w, y + 132], radius=26, fill=(15, 23, 42))
        _t(d, (x0 + 22, y + 20), label, font(24), MUTED)
        _t(d, (x0 + 22, y + 60), value, font(40, True))
    _t(d, (x, y + 170), _conditions(spot), font(32), TEXT)


def _fanal(img: Image.Image, fanal: dict | None, top: int, bottom: int) -> None:
    _glass(img, (PAD - 20, top, SIZE[0] - PAD + 20, bottom), accent=FANAL_COLORS["MYSTIC"])
    d = ImageDraw.Draw(img)
    x = PAD + 30
    _t(d, (x, top + 30), "FANAL · LAURISILVA", font(26, True), (196, 181, 253))
    if not fanal:
        _t(d, (x, top + 90), "Bez dat", font(48, True), MUTED)
        return
    verdict = fanal["verdict"]
    _t(d, (x, top + 78), verdict, font(76, True), FANAL_COLORS.get(verdict, MUTED))
    rh = fanal.get("forest_humidity")
    _t(d, (SIZE[0] - PAD - 30, top + 84), "—" if rh is None else f"{rh} %", font(64, True), anchor="ra")
    _t(d, (SIZE[0] - PAD - 30, top + 160), "vlhkost v lese", font(26), MUTED, "ra")


def _mini(img: Image.Image, rows: list[tuple[str, dict | None, str]], top: int, bottom: int) -> None:
    """Řádek na spot: tečka verdiktu, název, čas a skóre. Ikona = barevný kruh podle verdiktu."""
    _glass(img, (PAD - 20, top, SIZE[0] - PAD + 20, bottom), radius=36)
    d = ImageDraw.Draw(img)
    step = (bottom - top - 20) // len(rows)
    for i, (name, spot, kind) in enumerate(rows):
        cy = top + 10 + step * i + step // 2
        x = PAD + 30
        if spot is None:
            d.ellipse([x, cy - 14, x + 28, cy + 14], outline=MUTED, width=3)
            _t(d, (x + 56, cy), name, font(34, True), MUTED, "lm")
            _t(d, (SIZE[0] - PAD - 30, cy), "—", font(34), MUTED, "rm")
            continue
        color = VERDICT_COLORS.get(spot["verdict"], MUTED)
        d.ellipse([x, cy - 14, x + 28, cy + 14], fill=color)
        _t(d, (x + 56, cy - 14), name, font(34, True), TEXT, "lm")
        _t(d, (x + 56, cy + 24), f"{'západ' if kind == 'sunset' else 'východ'} {spot[kind]}", font(24), MUTED, "lm")
        _t(d, (SIZE[0] - PAD - 30, cy), f"{spot['score_pct']} %", font(40, True), color, "rm")
        if i < len(rows) - 1:
            d.line([(x, cy + step // 2), (SIZE[0] - PAD - 30, cy + step // 2)], fill=(51, 65, 85), width=1)


def render_story(payload: dict) -> Image.Image:
    morning = payload["tomorrow_spots"]["morning"]
    evening = payload["tomorrow_spots"]["evening"]
    hero = _find(morning, "arieiro") or morning[0]
    img = _background()
    d = ImageDraw.Draw(img)

    # hlavička (začíná na hranici safe zóny)
    y = SAFE_TOP
    _t(d, (PAD, y), "MADEIRA GOLDENHOUR", font(30, True), ORANGE)
    _t(d, (PAD, y + 48), _day_label(hero["date"]), font(66, True))
    sy = y + 160  # symbol východu: půlkruh slunce nad horizontem (DejaVu nemá emoji)
    d.pieslice([PAD, sy - 14, PAD + 44, sy + 30], 180, 360, fill=ORANGE)
    d.line([(PAD - 6, sy + 8), (PAD + 50, sy + 8)], fill=ORANGE, width=3)
    _t(d, (PAD + 66, sy + 8), f"Východ slunce {hero['sunrise']} {_tz_name(hero['date'])}", font(36), MUTED, "lm")

    _hero(img, hero, y + 210, y + 210 + 560)
    d = ImageDraw.Draw(img)
    fanal_top = y + 210 + 560 + 30
    _fanal(img, (payload.get("fanal") or {}).get("tomorrow") or payload.get("fanal"), fanal_top, fanal_top + 220)

    rows = [(s["name"].removeprefix("Miradouro da ").removeprefix("Miradouro do "), s, "sunrise")
            for k in MINI_SPOTS if (s := _find(morning, k))]
    pargo = _find(evening, "ponta_do_pargo")
    rows.append(("Ponta do Pargo", pargo, "sunset"))
    mini_top = fanal_top + 220 + 30
    mini_bottom = SIZE[1] - SAFE_BOTTOM - 100
    _mini(img, rows, mini_top, mini_bottom)

    d = ImageDraw.Draw(img)
    _t(d, (SIZE[0] // 2, SIZE[1] - SAFE_BOTTOM - 45), SITE_URL.removeprefix("https://").rstrip("/"),
       font(28, True), FUCHSIA, "mm")
    return img


def story_bytes(payload: dict) -> bytes:
    buf = io.BytesIO()
    render_story(payload).save(buf, "PNG", optimize=True)
    return buf.getvalue()


def write_story(payload: dict, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(story_bytes(payload))
    return out
