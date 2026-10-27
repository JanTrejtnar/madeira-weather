from madeira.services import story_image


def test_og_preview_size():
    assert story_image.render_og().size == (1200, 630)
