"""What a Featherframe Cloud start pays for (W-987): a wake imports only what
it uses, and the image compiles ahead what a start would compile."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from featherframe import paths
from featherframe.render import pipeline

SERVER = Path(__file__).resolve().parents[1]
DOCKERFILE = SERVER.parent / "hosted" / "Dockerfile"


def test_a_start_does_not_import_jinja(tmp_path):
    """A wake serves no page: the templates load with the first one."""
    env = {**os.environ, "FEATHERFRAME_DATA_DIR": str(tmp_path / "data"), "FEATHERFRAME_MDNS": "0"}
    out = subprocess.run([sys.executable, "-c",
                          "import sys, featherframe.app; print('jinja2' in sys.modules)"],
                         cwd=SERVER, env=env, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe import app as app_mod
    from featherframe.service import FeatherframeService
    pipeline.DITHER_OVERRIDE = "none"
    svc = FeatherframeService()
    svc.source.db_path = str(tmp_path / "missing.db")
    app_mod.app.state.service = svc
    monkeypatch.setattr(app_mod, "_templates", None)
    return TestClient(app_mod.app)


def test_the_image_compiled_page_is_the_page(client, tmp_path, monkeypatch):
    """Built with FEATHERFRAME_TEMPLATE_CACHE, a start loads every template
    the image compiled instead of compiling it, and serves the same page."""
    from featherframe import app as app_mod
    plain = client.get("/").text

    cache = tmp_path / "jinja-cache"
    monkeypatch.setenv("FEATHERFRAME_TEMPLATE_CACHE", str(cache))
    monkeypatch.setattr(app_mod, "_templates", None)
    names = app_mod.warm_templates()
    assert names == sum(1 for p in paths.templates_dir().rglob("*") if p.is_file())
    assert len(list(cache.iterdir())) == names

    # A new start: nothing compiled in this process yet, and nothing may be.
    monkeypatch.setattr(app_mod, "_templates", None)
    env = app_mod.page_templates().env

    def compiled(*_a, **_k):
        raise AssertionError("a template was compiled at start")

    monkeypatch.setattr(env, "compile", compiled)
    assert client.get("/").text == plain


def test_hosted_image_compiles_what_a_start_would():
    dockerfile = DOCKERFILE.read_text()
    assert "OPENBLAS_NUM_THREADS=1" in dockerfile
    assert "FEATHERFRAME_TEMPLATE_CACHE=" in dockerfile
    warm = dockerfile.index("app.warm_templates()")
    assert dockerfile.index("COPY server/templates") < warm
    # Bytecode for the server and Python's own library, after the last copy
    # of either.
    compiled = dockerfile.index("compileall")
    assert dockerfile.rindex("COPY server/featherframe") < compiled
    assert "/usr/local/lib/python3" in dockerfile[compiled:].splitlines()[0]
