import io

from PIL import Image

from madeira.services import story_image

SPOT = {
    "location": {"name": "Pico do Arieiro"},
    "date": "2026-10-17", "time": "08:13", "departure": "06:53",
    "verdict": "GO", "score": 82,
    "cloud_sea": {"above_deck_m": 700}, "wind": {"kmh": 9, "gust_kmh": 20},
    "clouds_canvas": {"pct": 30},
}


def _payload():
    sunset = {**SPOT, "location": {"name": "Farol da Ponta do Pargo"}, "time": "19:33",
              "verdict": "NO_GO", "score": 12, "cloud_sea": None}
    return {"sunrise": SPOT, "sunset": sunset}


def test_story_is_9_16_png():
    img = Image.open(io.BytesIO(story_image.story_bytes(_payload())))
    assert img.format == "PNG" and img.size == (1080, 1920)


def test_og_preview_size():
    assert story_image.render_og().size == (1200, 630)


def test_write_story(tmp_path):
    out = story_image.write_story(_payload(), tmp_path / "s.png")
    assert out.stat().st_size > 1000
