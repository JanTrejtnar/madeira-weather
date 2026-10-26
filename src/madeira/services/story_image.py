"""Generátor obrázků pro sdílení: 9:16 Story (1080×1920) a Open Graph náhled (1200×630).

Vstupem je payload z `webexport.build_payload` (klíče `sunrise`, `sunset`, `tomorrow`).
"""

from __future__ import annotations

import io
from datetime import date
from pathlib import Path

import qrcode
from PIL import Image, ImageDraw, ImageFont

SITE_URL = "https://jantrejtnar.github.io/madeira-weather/"
STORY_SIZE = (1080, 1920)
OG_SIZE = (1200, 630)

BG_TOP, BG_BOTTOM = (2, 6, 23), (15, 23, 42)  # #020617 → #0f172a
CARD = (30, 41, 59)
TEXT = (241, 245, 249)
MUTED = (148, 163, 184)
ORANGE = (251, 146, 60)
FUCHSIA = (217, 70, 239)
VERDICT_COLORS = {"GO": (34, 197, 94), "MAYBE": (249, 115, 22), "NO_GO": (239, 68, 68)}
VERDICT_LABELS = {"GO": "GO", "MAYBE": "MAYBE", "NO_GO": "NO-GO"}

_FONT_DIRS = ("C:/Windows/Fonts", "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu",
              "/usr/share/fonts/TTF", "/Library/Fonts", "/System/Library/Fonts/Supplemental")
_FONT_FILES = {
    False: ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "Arial.ttf"),
    True: ("segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf", "Arial Bold.ttf"),
}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    for d in _FONT_DIRS:
        for name in _FONT_FILES[bold]:
            path = Path(d) / name
            if path.exists():
                return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size)  # nouzově (bez diakritiky může vypadat hůř)


def _gradient(size: tuple[int, int]) -> Image.Image:
    w, h = size
    img = Image.new("RGB", size, BG_TOP)
    px = ImageDraw.Draw(img)
    for y in range(h):
        k = y / (h - 1)
        px.line([(0, y), (w, y)], fill=tuple(round(a + (b - a) * k) for a, b in zip(BG_TOP, BG_BOTTOM)))
    return img


def _text(d: ImageDraw.ImageDraw, xy, s, f, fill=TEXT, anchor="la") -> None:
    d.text(xy, s, font=f, fill=fill, anchor=anchor)


def _qr(url: str, box: int) -> Image.Image:
    qr = qrcode.QRCode(border=1, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(url)
    qr.make(fit=True)
    m = qr.get_matrix()
    n = len(m)
    cell = box // n
    img = Image.new("RGB", (cell * n, cell * n), "white")
    d = ImageDraw.Draw(img)
    for y, row in enumerate(m):
        for x, on in enumerate(row):
            if on:
                d.rectangle([x * cell, y * cell, (x + 1) * cell - 1, (y + 1) * cell - 1], fill=(2, 6, 23))
    return img


def _fmt_day(iso: str) -> str:
    d = date.fromisoformat(iso)
    return f"{d.day}. {d.month}. {d.year}"


def _badge(d: ImageDraw.ImageDraw, x: int, y: int, spot: dict, h: int, fsize: int) -> int:
    """Pilulka verdiktu + skóre; vrací její pravý okraj."""
    color = VERDICT_COLORS.get(spot["verdict"], MUTED)
    label = f"{VERDICT_LABELS.get(spot['verdict'], spot['verdict'])}  {spot['score']} %"
    f = font(fsize, True)
    w = int(d.textlength(label, font=f)) + 2 * (h // 2)
    d.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=color)
    _text(d, (x + w // 2, y + h // 2), label, f, fill=(2, 6, 23), anchor="mm")
    return x + w


def _stat(d, x, y, label, value, w) -> None:
    d.rounded_rectangle([x, y, x + w, y + 150], radius=28, fill=(15, 23, 42))
    _text(d, (x + 24, y + 22), label, font(26), MUTED)
    _text(d, (x + 24, y + 62), value, font(44, True))


def _cloud_sea(spot: dict) -> str:
    cs = spot.get("cloud_sea")
    if not cs:
        return "—"
    above = cs.get("above_deck_m")
    if above is None:
        return "—"
    return f"+{above} m" if above >= 0 else f"{above} m"


def render_story(payload: dict) -> Image.Image:
    sr, ss = payload["sunrise"], payload["sunset"]
    img = _gradient(STORY_SIZE)
    d = ImageDraw.Draw(img)
    W = STORY_SIZE[0]
    pad = 70

    # záhlaví
    _text(d, (pad, 90), "MADEIRA SKY FORECAST", font(54, True), ORANGE)
    _text(d, (pad, 165), _fmt_day(sr["date"]), font(40), MUTED)

    # východ – Pico do Arieiro
    top, bottom = 260, 1130
    d.rounded_rectangle([pad - 20, top, W - pad + 20, bottom], radius=48, fill=CARD)
    d.rounded_rectangle([pad - 20, top, pad - 8, bottom], radius=6, fill=ORANGE)
    _text(d, (pad + 30, top + 40), "VÝCHOD SLUNCE", font(30, True), ORANGE)
    _text(d, (pad + 30, top + 90), sr["location"]["name"], font(58, True))
    _text(d, (pad + 30, top + 190), sr["time"], font(150, True))
    _badge(d, pad + 30, top + 430, sr, 130, 66)
    sy, sw, gap = top + 620, (W - 2 * pad - 60 - 2 * 24) // 3, 24
    stats = [
        ("Moře mraků", _cloud_sea(sr)),
        ("Vítr", f"{sr['wind']['kmh']} km/h"),
        ("Barvy (mraky)", f"{sr['clouds_canvas']['pct']} %"),
    ]
    for i, (lab, val) in enumerate(stats):
        _stat(d, pad + 30 + i * (sw + gap), sy, lab, val, sw)
    _text(d, (pad + 30, sy + 190), f"Nárazy až {sr['wind']['gust_kmh']} km/h", font(30), MUTED)

    # západ – Ponta do Pargo
    top2, bottom2 = 1170, 1560
    d.rounded_rectangle([pad - 20, top2, W - pad + 20, bottom2], radius=48, fill=CARD)
    d.rounded_rectangle([pad - 20, top2, pad - 8, bottom2], radius=6, fill=FUCHSIA)
    _text(d, (pad + 30, top2 + 36), "ZÁPAD SLUNCE", font(30, True), (240, 171, 252))
    _text(d, (pad + 30, top2 + 84), ss["location"]["name"], font(46, True))
    _text(d, (pad + 30, top2 + 160), ss["time"], font(110, True))
    _badge(d, W - pad - 30 - 420, top2 + 190, ss, 110, 54)

    # call to action
    qr_box = 240
    qr = _qr(SITE_URL, qr_box)
    qy = 1620
    img.paste(qr, (pad, qy))
    tx = pad + qr.width + 40
    _text(d, (tx, qy + 20), "Celá předpověď", font(44, True))
    _text(d, (tx, qy + 80), "a živé webkamery:", font(34), MUTED)
    site = SITE_URL.removeprefix("https://").rstrip("/")
    _text(d, (tx, qy + 150), site, font(30, True), ORANGE)
    return img


def render_og() -> Image.Image:
    img = _gradient(OG_SIZE)
    d = ImageDraw.Draw(img)
    W, H = OG_SIZE
    # zapadající slunce nad horizontem
    cx, cy, r = W - 280, H - 150, 130
    for i in range(5, 0, -1):  # jemná záře
        rr = r + i * 36
        k = 0.05 * (6 - i)
        d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr],
                  fill=tuple(round(a + (o - a) * k) for a, o in zip(BG_BOTTOM, ORANGE)))
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=ORANGE)
    d.polygon([(0, H), (0, H - 120), (260, H - 230), (430, H - 150), (640, H - 290),
               (860, H - 170), (W, H - 240), (W, H)], fill=(15, 23, 42))
    d.rectangle([0, H - 100, W, H], fill=(2, 6, 23))
    _text(d, (70, 120), "Madeira Sky &", font(78, True))
    _text(d, (70, 215), "Inversion Predictor", font(78, True), ORANGE)
    _text(d, (70, 335), "Ranní inverze a západy slunce na Madeiře", font(38), MUTED)
    _text(d, (70, H - 60), SITE_URL.removeprefix("https://").rstrip("/"), font(30), MUTED,
          anchor="lm")
    return img


def story_bytes(payload: dict) -> bytes:
    buf = io.BytesIO()
    render_story(payload).save(buf, "PNG", optimize=True)
    return buf.getvalue()


def write_story(payload: dict, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(story_bytes(payload))
    return out


def write_og(out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    render_og().save(out, "PNG", optimize=True)
    return out
