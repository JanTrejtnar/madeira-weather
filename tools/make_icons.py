"""Vygeneruje ikony PWA (SVG + PNG + favicon) do docs/. Spuštění: python tools/make_icons.py (vyžaduje Pillow)."""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw

DOCS = Path(__file__).resolve().parents[1] / "docs"
S = 512  # návrhová velikost; obsah je v bezpečné zóně (maskable) uprostřed
SUN = (256, 250, 84)  # x, y, poloměr
# (barva, y základna, amplituda, fáze) od nejzadnější vlny
WAVES = [("#6366f1", 318, 14, 0.0), ("#818cf8", 352, 16, 1.6), ("#c7d2fe", 388, 18, 3.1)]
BG_TOP, BG_BOTTOM = (15, 23, 42), (30, 27, 75)
SUN_COLOR = "#fb923c"


def wave_points(base: float, amp: float, phase: float, step: int = 8) -> list[tuple[float, float]]:
    pts = [(x, base + amp * math.sin(x / S * 2 * math.pi * 2 + phase)) for x in range(0, S + 1, step)]
    return pts + [(S, S), (0, S)]


def write_svg(path: Path) -> None:
    waves = "\n".join(
        f'  <polygon fill="{c}" points="{" ".join(f"{x:.0f},{y:.0f}" for x, y in wave_points(b, a, p))}"/>'
        for c, b, a, p in WAVES
    )
    path.write_text(
        f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {S} {S}">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="rgb{BG_TOP}"/><stop offset="1" stop-color="rgb{BG_BOTTOM}"/>
    </linearGradient>
    <radialGradient id="glow"><stop offset="0" stop-color="{SUN_COLOR}" stop-opacity=".45"/><stop offset="1" stop-color="{SUN_COLOR}" stop-opacity="0"/></radialGradient>
  </defs>
  <rect width="{S}" height="{S}" fill="url(#bg)"/>
  <circle cx="{SUN[0]}" cy="{SUN[1]}" r="{SUN[2] * 2}" fill="url(#glow)"/>
  <circle cx="{SUN[0]}" cy="{SUN[1]}" r="{SUN[2]}" fill="{SUN_COLOR}"/>
{waves}
</svg>
''',
        encoding="utf-8",
    )


def render(scale: int = 4) -> Image.Image:
    """Vykreslí ikonu s nadvzorkováním (anti-aliasing) a vrátí ji ve velikosti S×S."""
    n = S * scale
    img = Image.new("RGB", (n, n))
    d = ImageDraw.Draw(img)
    for y in range(n):
        k = y / (n - 1)
        d.line([(0, y), (n, y)], fill=tuple(round(a + (b - a) * k) for a, b in zip(BG_TOP, BG_BOTTOM)))
    glow = Image.new("RGBA", (n, n), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    cx, cy, r = (v * scale for v in SUN)
    for i in range(20, 0, -1):  # měkká záře
        rr = r + (r * i / 20)
        gd.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=(251, 146, 60, round(150 / 20 * (1 - i / 20) + 4)))
    img.paste(glow, (0, 0), glow)
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=SUN_COLOR)
    for color, base, amp, phase in WAVES:
        d.polygon([(x * scale, y * scale) for x, y in wave_points(base, amp, phase, 4)], fill=color)
    return img.resize((S, S), Image.LANCZOS)


def main() -> None:
    DOCS.mkdir(exist_ok=True)
    write_svg(DOCS / "icon.svg")
    write_svg(DOCS / "favicon.svg")
    master = render()
    for name, size in (("icon-192.png", 192), ("icon-512.png", 512), ("apple-touch-icon.png", 180)):
        master.resize((size, size), Image.LANCZOS).save(DOCS / name, optimize=True)
    master.save(DOCS / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    print("Ikony zapsány do", DOCS)


if __name__ == "__main__":
    main()
