import os

from PIL import Image

from featherframe import thumbs


def test_thumb_is_small_and_drawn_once(tmp_path):
    src = tmp_path / "blue-jay.png"
    Image.new("RGBA", (1024, 1536), (10, 20, 30, 255)).save(src)
    t = thumbs.thumb_for(src)
    assert t == tmp_path / "thumbs" / "blue-jay.jpg"
    with Image.open(t) as im:
        assert im.size == (171, 256) and im.mode == "RGB"
    first = t.stat().st_mtime
    assert thumbs.thumb_for(src).stat().st_mtime == first  # cached

    # A Cloud start writes every file fresh: a source newer than its
    # thumbnail is not a new image. Whatever replaces one drops its thumbnail.
    os.utime(src, (first + 10, first + 10))
    assert thumbs.thumb_for(src).stat().st_mtime == first
    Image.new("RGB", (1024, 1536), "white").save(src)
    thumbs.drop_thumb(src)
    assert not t.exists()
    with Image.open(thumbs.thumb_for(src)) as im:
        assert im.getpixel((5, 5)) == (255, 255, 255)


def test_a_broken_image_has_no_thumb(tmp_path):
    src = tmp_path / "x.png"
    src.write_bytes(b"not a png")
    assert thumbs.thumb_for(src) is None
