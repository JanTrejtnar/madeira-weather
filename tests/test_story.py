"""Instagram Story: přesné rozměry 1080×1920, platné PNG a obsah uvnitř safe zón."""

import io

from PIL import Image, ImageChops

from madeira.services import story_renderer

ARIEIRO = {
    "key": "arieiro", "name": "Pico do Arieiro", "date": "2026-10-28", "sunrise": "07:51",
    "verdict": "GO", "score_pct": 83, "cloud_deck_top_m": 1100, "summit_above_deck_m": 700,
    "wind_kmh": 9, "gust_kmh": 20, "canvas_cloud_pct": 30,
}
BICA = {**ARIEIRO, "key": "bica_da_cana", "name": "Bica da Cana", "verdict": "MAYBE", "score_pct": 55}
ROSTO = {**ARIEIRO, "key": "ponta_do_rosto", "name": "Ponta do Rosto", "verdict": "NO_GO", "score_pct": 12}
PARGO = {"key": "ponta_do_pargo", "name": "Farol da Ponta do Pargo", "date": "2026-10-28",
         "sunset": "18:58", "verdict": "GO", "score_pct": 71}


def _payload(fanal=True):
    p = {
        "today": "2026-10-27",
        "tomorrow_spots": {"morning": [ARIEIRO, BICA, ROSTO], "evening": [PARGO]},
    }
    if fanal:
        p["fanal"] = {"tomorrow": {"verdict": "MYSTIC", "forest_humidity": 94}}
    return p


def test_story_is_1080x1920_png():
    img = Image.open(io.BytesIO(story_renderer.story_bytes(_payload())))
    assert img.format == "PNG" and img.size == (1080, 1920)


def test_story_keeps_ui_safe_zones_clear():
    img = story_renderer.render_story(_payload()).convert("RGB")
    bg = story_renderer._background().convert("RGB")
    diff = ImageChops.difference(img, bg)
    w, h = img.size
    assert diff.crop((0, 0, w, 245)).getbbox() is None
    assert diff.crop((0, h - 245, w, h)).getbbox() is None


def test_story_renders_without_fanal_and_missing_spots():
    p = _payload(fanal=False)
    p["tomorrow_spots"] = {"morning": [ARIEIRO], "evening": []}
    assert story_renderer.render_story(p).size == (1080, 1920)


def test_write_story(tmp_path):
    out = story_renderer.write_story(_payload(), tmp_path / "docs" / "story-latest.png")
    assert out.stat().st_size > 1000


def test_day_label_has_weekday_and_timezone_follows_dst():
    assert story_renderer._day_label("2026-10-28") == "Středa, 28. října"
    assert story_renderer._tz_name("2026-01-15") == "WET"
    assert story_renderer._tz_name("2026-07-15") == "WEST"


def test_story_uses_bundled_ttf_font():
    from madeira.services.story_image import ASSET_FONTS, font

    assert (ASSET_FONTS / "DejaVuSans.ttf").exists() and (ASSET_FONTS / "DejaVuSans-Bold.ttf").exists()
    assert font(30, True).path == str(ASSET_FONTS / "DejaVuSans-Bold.ttf")
