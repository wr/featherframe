import os

from PIL import Image

from featherframe import thumbs


def test_thumb_is_small_and_redrawn_when_the_image_is_newer(tmp_path):
    src = tmp_path / "blue-jay.png"
    Image.new("RGBA", (1024, 1536), (10, 20, 30, 255)).save(src)
    t = thumbs.thumb_for(src)
    assert t == tmp_path / "thumbs" / "blue-jay.jpg"
    with Image.open(t) as im:
        assert im.size == (171, 256) and im.mode == "RGB"
    first = t.stat().st_mtime
    assert thumbs.thumb_for(src).stat().st_mtime == first  # cached

    Image.new("RGB", (1024, 1536), "white").save(src)
    os.utime(src, (first + 10, first + 10))
    with Image.open(thumbs.thumb_for(src)) as im:
        assert im.getpixel((5, 5)) == (255, 255, 255)

    thumbs.drop_thumb(src)
    assert not t.exists()


def test_a_broken_image_has_no_thumb(tmp_path):
    src = tmp_path / "x.png"
    src.write_bytes(b"not a png")
    assert thumbs.thumb_for(src) is None
