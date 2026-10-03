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


def test_the_vendored_install_dialog_chunk_is_where_the_page_imports_it():
    """The hosted page opens esp-web-tools' dialog on a port it already holds,
    by importing the dialog's hashed chunk by name: a new vendored build must
    update the name in index.html."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    html = (root / "templates" / "index.html").read_text()
    m = re.search(r"import\('/static/flash/esp-web-tools/([^']+)'\)", html)
    assert m, "the page no longer imports the install dialog"
    assert (root / "static" / "flash" / "esp-web-tools" / m.group(1)).exists()


def test_the_page_says_ages_itself_and_applies_the_status_on_load(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.app import app
    from featherframe.service import FeatherframeService
    monkeypatch.setattr(app.state, "service", FeatherframeService(), raising=False)
    html = TestClient(app).get("/").text
    assert "function ffAgo(ts, now)" in html
    assert 'data-frames="' in html
    assert "pollStatus();\n" in html
    assert "PREVIEW_MS" not in html


@pytest.mark.parametrize("hosted", [False, True])
def test_live_polls_and_warming(tmp_path, monkeypatch, hosted):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.app import app
    from featherframe.service import FeatherframeService
    monkeypatch.setattr(app.state, "service", FeatherframeService(), raising=False)
    monkeypatch.setattr(app.state, "hosted", object() if hosted else None, raising=False)
    html = TestClient(app).get("/").text
    assert "jsonFetch('/api/tasks?live=1')" in html
    assert "jsonFetch('/api/generated?live=1')" in html
    assert "keepalive: true" in html
    assert ("fetch('/api/warm'" in html) is hosted


def test_the_model_list_is_asked_for_only_when_the_field_is_used(tmp_path, monkeypatch):
    """On Featherframe Cloud every read the page makes as it loads must come
    from the front door's cache (W-946): the image models' live list is the
    server asking OpenAI, so it is asked for when the model field is used."""
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.app import app
    from featherframe.service import FeatherframeService
    monkeypatch.setattr(app.state, "service", FeatherframeService(), raising=False)
    html = TestClient(app).get("/").text
    i = html.index("-- image-model suggestions")
    block = html[i:html.index("})();", i)]
    assert "addEventListener('focus', loadOnce)" in block
    assert "\n    load();\n" not in block


@pytest.mark.parametrize("hosted", [False, True])
def test_only_featherframe_cloud_says_how_old_the_page_is(tmp_path, monkeypatch, hosted):
    """W-950: a copy from the front door's cache says its age; the box's page is always live."""
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    from featherframe.app import app
    from featherframe.service import FeatherframeService
    monkeypatch.setattr(app.state, "service", FeatherframeService(), raising=False)
    monkeypatch.setattr(app.state, "hosted", object() if hosted else None, raising=False)
    html = TestClient(app).get("/").text
    assert ('id="stale-note"' in html) is hosted
    assert (">Update now</button>" in html) is hosted
    assert ("fetch('/api/page/update'" in html) is hosted
    # W-954: on both, a status older than the one shown is not applied.
    assert "function ffPageAge(opts)" in html and "if (!res.current) return;" in html
    # W-956: a reload of an old page updates it; the page's own reload says so.
    assert "ffPageAge(pageLoad())" in html
    assert ("if (pageAge.wantsUpdate()) { update(); return; }" in html) is hosted
