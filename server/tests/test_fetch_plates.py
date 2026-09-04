"""fetch_plates.py: the full-catalog cache path (`--all`) and its retry logic.

The script lives in scripts/, not the package, so it is loaded by path. Every
test uses a fake HTTP session — nothing here touches the network.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "fetch_plates.py"


@pytest.fixture(scope="module")
def fp():
    spec = importlib.util.spec_from_file_location("fetch_plates", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Resp:
    def __init__(self, status: int, body: bytes = b"") -> None:
        self.status_code = status
        self._body = body

    def iter_content(self, chunk_size=1 << 16):
        yield self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSession:
    """Scripted responses per URL; a list is consumed one call at a time so a
    test can make the first attempt fail and the second succeed."""

    def __init__(self, script: dict[str, list]) -> None:
        self.script = {k: list(v) for k, v in script.items()}
        self.calls: list[str] = []

    def get(self, url, **kw):
        self.calls.append(url)
        queue = self.script.get(url)
        if not queue:
            return _Resp(404)
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _catalog(*plates: int) -> dict[int, dict]:
    return {p: {"plate": p, "name": f"Bird {p}", "fileName": f"plate-{p}-bird-{p}.jpg"}
            for p in plates}


def test_bucket_matches_mirror_layout(fp):
    assert fp._bucket(1) == "1-99"
    assert fp._bucket(99) == "1-99"
    assert fp._bucket(100) == "100-199"
    assert fp._bucket(399) == "300-399"
    assert fp._bucket(400) == "400-435"
    assert fp._bucket(435) == "400-435"


def test_download_skips_existing_file(fp, tmp_path):
    cat = _catalog(5)
    dest = tmp_path / cat[5]["fileName"]
    dest.write_bytes(b"x" * 2048)
    sess = FakeSession({})
    assert fp.download_plate(sess, 5, cat, tmp_path, force=False) == cat[5]["fileName"]
    assert sess.calls == []


def test_download_retries_transient_failure(fp, tmp_path, monkeypatch):
    """A flaky resolver on the box (CT 113 DNS drops under bursts) must not
    lose a plate: the same URL is retried before falling through."""
    import requests
    monkeypatch.setattr(fp.time, "sleep", lambda s: None)
    cat = _catalog(7)
    url = f"{fp.RAW_BASE}/plates/1-99/{cat[7]['fileName']}"
    body = b"j" * 4096
    sess = FakeSession({url: [requests.ConnectionError("dns"), _Resp(200, body)]})
    assert fp.download_plate(sess, 7, cat, tmp_path, force=False) == cat[7]["fileName"]
    assert sess.calls.count(url) == 2
    assert (tmp_path / cat[7]["fileName"]).read_bytes() == body


def test_download_falls_back_to_audubon_media(fp, tmp_path, monkeypatch):
    monkeypatch.setattr(fp.time, "sleep", lambda s: None)
    cat = _catalog(300)
    fn = cat[300]["fileName"]
    raw = f"{fp.RAW_BASE}/plates/300-399/{fn}"
    media = f"{fp.AUDUBON_MEDIA}/{fn}"
    sess = FakeSession({raw: [_Resp(404)], media: [_Resp(200, b"m" * 4096)]})
    assert fp.download_plate(sess, 300, cat, tmp_path, force=False) == fn


def test_download_gives_up_after_retries(fp, tmp_path, monkeypatch):
    import requests
    monkeypatch.setattr(fp.time, "sleep", lambda s: None)
    cat = _catalog(9)
    sess = FakeSession({})  # every URL 404s
    assert fp.download_plate(sess, 9, cat, tmp_path, force=False) is None
    # A 404 is not transient: no retries, just the two sources tried once each.
    assert len(sess.calls) == 2
    err = requests.ConnectionError("down")
    sess = FakeSession({f"{fp.RAW_BASE}/plates/1-99/{cat[9]['fileName']}": [err, err, err, err]})
    assert fp.download_plate(sess, 9, cat, tmp_path, force=False) is None
    assert len(sess.calls) == (fp.RETRIES + 1) + 1  # raw: 1 try + RETRIES; then media once


def test_cache_all_downloads_only_missing_plates(fp, tmp_path, monkeypatch):
    monkeypatch.setattr(fp.time, "sleep", lambda s: None)
    cat = _catalog(1, 2, 3)
    (tmp_path / cat[2]["fileName"]).write_bytes(b"x" * 2048)  # already cached
    script = {f"{fp.RAW_BASE}/plates/1-99/{cat[p]['fileName']}": [_Resp(200, b"p" * 4096)]
              for p in (1, 3)}
    sess = FakeSession(script)
    stats = fp.cache_all(sess, cat, tmp_path, force=False)
    assert stats == {"downloaded": 2, "present": 1, "failed": 0}
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(m["fileName"] for m in cat.values())


def test_index_catalog_records_cached_images(fp, tmp_path):
    """index.json's catalog names the on-disk file for every cached plate so
    the server (and a future non-curated lookup) can see what is available."""
    cat = _catalog(1, 2)
    (tmp_path / cat[1]["fileName"]).write_bytes(b"x" * 2048)
    rows = fp.catalog_rows(cat, tmp_path)
    assert rows == [
        {"plate": 1, "name": "Bird 1", "image": cat[1]["fileName"]},
        {"plate": 2, "name": "Bird 2", "image": None},
    ]


def test_species_legend_reduces_a_composite_to_the_species_key(fp):
    plate_legends = {353: {"composite": True, "lines": [
        "Chesnut-backed Titmouse, 1. Male, 2. Female.",
        "Black-capt Titmouse, 3. Male, 4. Female.",
        "Willow Oak ~ Quercus Phellos. L."]},
        159: {"composite": False, "lines": ["Male, 1. Female, 2.", "Wild Almond."]}}
    chick = {"common": "Black-capped Chickadee", "audubon_title": "Black-capt Titmouse (composite)",
             "composite": True}
    assert fp.species_legend(chick, 353, plate_legends) == [
        "3. Male, 4. Female.", "Willow Oak ~ Quercus Phellos. L."]
    card = {"common": "Northern Cardinal", "audubon_title": "Cardinal Grosbeak"}
    assert fp.species_legend(card, 159, plate_legends) == ["Male, 1. Female, 2.", "Wild Almond."]
    assert fp.species_legend(card, 999, plate_legends) == []
    assert fp.species_legend(card, None, plate_legends) == []
