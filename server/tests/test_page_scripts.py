"""Every inline script on the page parses. One syntax error stops the page's
whole script, and nothing on it can be clicked: a template value escaped into
a JS string literal did exactly that (22 Sep 2026, hosted and box alike)."""
from __future__ import annotations

import re
import shutil
import subprocess

import pytest
from starlette.testclient import TestClient

_SCRIPT = re.compile(r"<script(?![^>]*(?:application/json|\bsrc=))[^>]*>(.*?)</script>", re.S)


@pytest.mark.parametrize("hosted", [False, True])
def test_every_script_on_the_page_parses(tmp_path, monkeypatch, hosted):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.app import app
    from featherframe.service import FeatherframeService
    monkeypatch.setattr(app.state, "service", FeatherframeService(), raising=False)
    # The page only asks whether it is hosted; undone after, as the app is shared.
    monkeypatch.setattr(app.state, "hosted", object() if hosted else None, raising=False)
    html = TestClient(app).get("/").text
    scripts = _SCRIPT.findall(html)
    assert scripts
    for i, js in enumerate(scripts):
        f = tmp_path / f"script{i}.js"
        f.write_text(js)
        r = subprocess.run([node, "--check", str(f)], capture_output=True, text=True)
        assert r.returncode == 0, f"script {i} does not parse:\n{r.stderr[:600]}"
