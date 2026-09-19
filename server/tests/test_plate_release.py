"""plate_release: the Havell set as checksummed release tarballs (W-764).

Packing and fetching are tested against each other through a fake HTTP
session — nothing here touches the network.
"""
from __future__ import annotations

import hashlib
import io
import json
import tarfile

import pytest
import requests

from featherframe import plate_release as pr

BASE = "https://example.test/plates-v1"


def _catalog(*plates: int) -> dict[int, dict]:
    return {p: {"plate": p, "name": f"Bird {p}", "fileName": f"plate-{p}-bird-{p}.jpg"}
            for p in plates}


def _images(tmp_path, catalog):
    d = tmp_path / "img"
    d.mkdir()
    for p, m in catalog.items():
        (d / m["fileName"]).write_bytes(bytes([p % 251]) * 4096)
    return d


class _Resp:
    def __init__(self, status: int, body: bytes = b"") -> None:
        self.status_code = status
        self._body = body

    def iter_content(self, chunk_size=1 << 16):
        for i in range(0, len(self._body), chunk_size):
            yield self._body[i:i + chunk_size]

    def json(self):
        return json.loads(self._body)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class ReleaseSession:
    """Serves a packed release directory at BASE; anything else 404s."""

    def __init__(self, dist, broken: dict[str, bytes] | None = None) -> None:
        self.dist = dist
        self.broken = broken or {}
        self.calls: list[str] = []

    def get(self, url, **kw):
        self.calls.append(url)
        name = url.rsplit("/", 1)[-1]
        if not url.startswith(BASE + "/"):
            raise requests.ConnectionError("mirror unreachable")
        if name in self.broken:
            return _Resp(200, self.broken[name])
        f = self.dist / name
        return _Resp(200, f.read_bytes()) if f.exists() else _Resp(404)


@pytest.fixture
def packed(tmp_path):
    cat = _catalog(1, 2, 99, 100, 250, 435)
    img = _images(tmp_path, cat)
    dist = tmp_path / "dist"
    manifest = pr.pack(cat, img, dist)
    return cat, img, dist, manifest


def test_part_names_follow_the_plate_buckets():
    assert pr.part_name(1) == "havell-001-099.tar"
    assert pr.part_name(99) == "havell-001-099.tar"
    assert pr.part_name(100) == "havell-100-199.tar"
    assert pr.part_name(435) == "havell-400-435.tar"


def test_pack_writes_parts_manifest_and_checksums(packed):
    cat, img, dist, manifest = packed
    names = sorted(p.name for p in dist.iterdir())
    assert names == ["SHA256SUMS", "data.json", "havell-001-099.tar", "havell-100-199.tar",
                     "havell-200-299.tar", "havell-400-435.tar", "plates-manifest.json"]
    assert manifest == json.loads((dist / "plates-manifest.json").read_text())
    part = next(p for p in manifest["parts"] if p["name"] == "havell-001-099.tar")
    assert part["plates"] == [1, 2, 99]
    body = (dist / part["name"]).read_bytes()
    assert part["sha256"] == hashlib.sha256(body).hexdigest()
    assert part["bytes"] == len(body)
    assert f"{part['sha256']}  {part['name']}" in (dist / "SHA256SUMS").read_text()
    # The catalog rides along so a fresh install never needs the mirror.
    assert {int(e["plate"]) for e in json.loads((dist / "data.json").read_text())} == set(cat)


def test_pack_is_reproducible(tmp_path, packed):
    cat, img, dist, manifest = packed
    again = pr.pack(cat, img, tmp_path / "dist2")
    assert [p["sha256"] for p in again["parts"]] == [p["sha256"] for p in manifest["parts"]]


def test_pack_refuses_an_incomplete_cache(tmp_path):
    cat = _catalog(1, 2)
    img = _images(tmp_path, cat)
    (img / cat[2]["fileName"]).unlink()
    with pytest.raises(FileNotFoundError):
        pr.pack(cat, img, tmp_path / "dist")


def test_fetch_restores_missing_plates_with_the_mirror_down(tmp_path, packed):
    cat, _, dist, _ = packed
    dest = tmp_path / "fresh"
    sess = ReleaseSession(dist)
    got = pr.fetch_parts(sess, BASE, {1, 2, 99, 435}, cat, dest)
    assert got == {1, 2, 99, 435}
    assert sorted(f.name for f in dest.iterdir()) == sorted(
        cat[p]["fileName"] for p in (1, 2, 99, 435))
    # Only the parts that hold a wanted plate are downloaded.
    assert not any("havell-100-199" in u or "havell-200-299" in u for u in sess.calls)


def test_fetch_keeps_plates_already_on_disk(tmp_path, packed):
    cat, _, dist, _ = packed
    dest = tmp_path / "fresh"
    dest.mkdir()
    mine = dest / cat[1]["fileName"]
    mine.write_bytes(b"local" * 1000)
    pr.fetch_parts(ReleaseSession(dist), BASE, {2}, cat, dest)
    assert mine.read_bytes() == b"local" * 1000
    assert (dest / cat[2]["fileName"]).exists()


def test_fetch_rejects_a_part_whose_checksum_is_wrong(tmp_path, packed):
    cat, _, dist, _ = packed
    dest = tmp_path / "fresh"
    sess = ReleaseSession(dist, broken={"havell-001-099.tar": b"not the tarball" * 100})
    assert pr.fetch_parts(sess, BASE, {1, 435}, cat, dest) == {435}
    assert [f.name for f in dest.iterdir()] == [cat[435]["fileName"]]  # no .part litter


def test_fetch_never_extracts_outside_the_images_dir(tmp_path, packed):
    """A tampered part that still matched its manifest must not write anywhere
    but plate files: odd member names are skipped."""
    cat, _, dist, manifest = packed
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name in ("../evil.jpg", "/abs/plate-1-x.jpg", "notes.txt", "plate-435-bird-435.jpg"):
            info = tarfile.TarInfo(name)
            info.size = 2048
            tar.addfile(info, io.BytesIO(b"e" * 2048))
    body = buf.getvalue()
    for p in manifest["parts"]:
        if p["name"] == "havell-400-435.tar":
            p["sha256"], p["bytes"] = hashlib.sha256(body).hexdigest(), len(body)
    (dist / "plates-manifest.json").write_text(json.dumps(manifest))
    (dist / "havell-400-435.tar").write_bytes(body)
    dest = tmp_path / "fresh"
    assert pr.fetch_parts(ReleaseSession(dist), BASE, {435}, cat, dest) == {435}
    assert [f.name for f in dest.iterdir()] == ["plate-435-bird-435.jpg"]
    assert not (tmp_path / "evil.jpg").exists()


def test_fetch_soft_fails_when_the_release_is_unreachable(tmp_path, packed):
    cat, _, dist, _ = packed
    sess = ReleaseSession(dist)
    assert pr.fetch_parts(sess, "https://elsewhere.test/x", {1}, cat, tmp_path / "fresh") == set()


def test_fetch_catalog_returns_the_released_data_json(packed):
    cat, _, dist, _ = packed
    raw = pr.fetch_catalog(ReleaseSession(dist), BASE)
    assert {int(e["plate"]) for e in raw} == set(cat)
    assert pr.fetch_catalog(ReleaseSession(dist), "https://elsewhere.test/x") is None
