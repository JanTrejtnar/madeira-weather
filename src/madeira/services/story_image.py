"""Sdílené konstanty a písma pro obrázky + Open Graph náhled (1200×630). Story je v `story_renderer`."""

from __future__ import annotations

from pathlib import Path

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

ASSET_FONTS = Path(__file__).resolve().parents[3] / "assets" / "fonts"  # DejaVu Sans (volná licence) v repozitáři
_FONT_DIRS = (str(ASSET_FONTS), "C:/Windows/Fonts", "/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu",
              "/usr/share/fonts/TTF", "/Library/Fonts", "/System/Library/Fonts/Supplemental")
_FONT_FILES = {
    False: ("DejaVuSans.ttf", "segoeui.ttf", "arial.ttf", "Arial.ttf"),
    True: ("DejaVuSans-Bold.ttf", "segoeuib.ttf", "arialbd.ttf", "Arial Bold.ttf"),
}


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Nejdřív TTF z `assets/fonts` (stejný vzhled na Windows i v Actions na Linuxu), pak systémové."""
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


def write_og(out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    render_og().save(out, "PNG", optimize=True)
    return out
