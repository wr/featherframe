"""W-765: generated plates are paid for per image, so the gallery can hand
them over as a zip and take them back — without ever replacing a newer plate."""
from __future__ import annotations

import io
import json
import zipfile

import pytest
from starlette.testclient import TestClient

from featherframe.render.genart import GeneratedArtProvider
from featherframe.service import FeatherframeService

PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 512


def _plate(d, slug, created, body=PNG, **extra):
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{slug}.png").write_bytes(body)
    (d / f"{slug}.json").write_text(json.dumps(
        {"slug": slug, "common": slug.title(), "scientific": slug, "created_at": created, **extra}))


def _zip(members: dict[str, bytes]) -> io.BytesIO:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, body in members.items():
            z.writestr(name, body)
    buf.seek(0)
    return buf


def _sidecar(slug, created):
    return json.dumps({"slug": slug, "common": slug, "scientific": slug,
                       "created_at": created}).encode()


@pytest.fixture
def prov(tmp_path):
    return GeneratedArtProvider(None, cache_dir=tmp_path / "data" / "generated")


def test_export_then_import_round_trips_the_cache(prov, tmp_path):
    _plate(prov._dir(), "catharus-fuscescens", "2026-09-01T10:00:00+00:00", cost_usd=0.19)
    _plate(prov._dir(), "sturnus-vulgaris", "2026-09-02T10:00:00+00:00")
    (prov._dir().parent / "descriptions.json").write_text(json.dumps({"sturnus-vulgaris": {"description": "d"}}))
    out = tmp_path / "backup.zip"
    assert prov.export_to(out) == 2
    fresh = GeneratedArtProvider(None, cache_dir=tmp_path / "new" / "generated")
    with open(out, "rb") as fh:
        assert fresh.import_from(fh) == {"restored": 2, "kept": 0, "skipped": 0}
    assert [m["slug"] for m in fresh.cached_species()] == ["sturnus-vulgaris", "catharus-fuscescens"]
    assert fresh.cached_species()[1]["cost_usd"] == 0.19
    assert (fresh._dir() / "sturnus-vulgaris.png").read_bytes() == PNG
    assert json.loads((fresh._dir().parent / "descriptions.json").read_text()) == {
        "sturnus-vulgaris": {"description": "d"}}


def test_import_never_replaces_a_newer_plate(prov):
    _plate(prov._dir(), "veery", "2026-09-10T10:00:00+00:00", body=PNG + b"mine")
    _plate(prov._dir(), "starling", "2026-08-01T10:00:00+00:00", body=PNG + b"old")
    backup = _zip({"veery.png": PNG + b"backup", "veery.json": _sidecar("veery", "2026-09-01T10:00:00+00:00"),
                   "starling.png": PNG + b"backup", "starling.json": _sidecar("starling", "2026-09-01T10:00:00+00:00")})
    assert prov.import_from(backup) == {"restored": 1, "kept": 1, "skipped": 0}
    assert (prov._dir() / "veery.png").read_bytes() == PNG + b"mine"
    assert (prov._dir() / "starling.png").read_bytes() == PNG + b"backup"


def test_import_leaves_a_plate_that_is_regenerating(prov):
    _plate(prov._dir(), "veery", "2026-08-01T10:00:00+00:00", body=PNG + b"mine")
    backup = _zip({"veery.png": PNG, "veery.json": _sidecar("veery", "2026-09-01T10:00:00+00:00")})
    assert prov.import_from(backup, busy={"veery"}) == {"restored": 0, "kept": 1, "skipped": 0}


def test_import_skips_anything_that_is_not_a_plate_pair(prov):
    backup = _zip({
        "../evil.png": PNG, "../evil.json": _sidecar("evil", "2026-09-01T10:00:00+00:00"),
        "sub/dir.png": PNG,
        "lonely.png": PNG,                                        # no sidecar
        "notpng.png": b"<html>", "notpng.json": _sidecar("notpng", "2026-09-01T10:00:00+00:00"),
        "badjson.png": PNG, "badjson.json": b"[1, 2]",
        "renamed.png": PNG, "renamed.json": _sidecar("other", "2026-09-01T10:00:00+00:00"),
        "good.png": PNG, "good.json": _sidecar("good", "2026-09-01T10:00:00+00:00"),
    })
    out = prov.import_from(backup)
    assert out["restored"] == 1 and out["skipped"] >= 4
    assert sorted(p.name for p in prov._dir().iterdir()) == ["good.json", "good.png"]
    assert not (prov._dir().parent / "evil.png").exists()


def test_import_rejects_a_file_that_is_not_a_zip(prov):
    with pytest.raises(ValueError):
        prov.import_from(io.BytesIO(b"definitely not a zip"))


def test_import_refuses_an_oversized_member(prov, monkeypatch):
    monkeypatch.setattr("featherframe.render.genart.BACKUP_MAX_MEMBER_BYTES", 256)
    backup = _zip({"big.png": PNG, "big.json": _sidecar("big", "2026-09-01T10:00:00+00:00")})
    assert prov.import_from(backup) == {"restored": 0, "kept": 0, "skipped": 1}


# -- through the web ------------------------------------------------------------
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.app import app
    app.state.service = FeatherframeService()
    return TestClient(app, raise_server_exceptions=False)


def test_the_gallery_downloads_and_restores_a_backup(client, tmp_path):
    gen = tmp_path / "data" / "generated"
    _plate(gen, "veery", "2026-09-01T10:00:00+00:00")
    r = client.get("/api/generated/export")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert "featherframe-generated-plates" in r.headers["content-disposition"]
    assert sorted(zipfile.ZipFile(io.BytesIO(r.content)).namelist()) == ["veery.json", "veery.png"]
    assert not list((tmp_path / "data").glob("*.zip*"))          # the temp zip is cleaned up

    (gen / "veery.png").unlink(); (gen / "veery.json").unlink()
    r = client.post("/api/generated/import", files={"backup": ("b.zip", r.content, "application/zip")})
    assert r.json() == {"ok": True, "error": None, "restored": 1, "kept": 0, "skipped": 0}
    assert [m["slug"] for m in client.get("/api/generated").json()["cached"]] == ["veery"]


def test_import_reports_a_bad_upload_and_refuses_cross_origin(client):
    r = client.post("/api/generated/import", files={"backup": ("b.zip", b"nope", "application/zip")})
    assert r.json()["ok"] is False and "zip" in r.json()["error"].lower()
    r = client.post("/api/generated/import", headers={"origin": "https://evil.example"},
                    files={"backup": ("b.zip", b"nope", "application/zip")})
    assert r.status_code == 403


def test_the_restore_control_is_on_the_page_with_an_empty_gallery(client):
    html = client.get("/").text
    assert 'action="/api/generated/import"' in html
    assert "/api/generated/export" not in html                    # nothing to download yet
