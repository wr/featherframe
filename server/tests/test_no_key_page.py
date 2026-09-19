"""W-770: the page presumes nobody ever adds an image API key. Without one the
image-generation section says the frame is complete and keeps the rows that
only matter once plates can be bought out of the way — still in the form, so
a save loses nothing."""
from __future__ import annotations

import re

import pytest
from starlette.testclient import TestClient

from featherframe.service import FeatherframeService


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    return FeatherframeService()


def _page(svc) -> str:
    from featherframe.app import app
    app.state.service = svc
    return TestClient(app, raise_server_exceptions=False).get("/").text


def _needs_key_rows(html: str) -> list[str]:
    return re.findall(r'<(?:div|details) class="[^"]*ig-needs-key"([^>]*)>', html)


def test_without_a_key_the_section_reads_as_optional_and_complete(svc):
    html = _page(svc)
    assert "Featherframe is complete without this." in html
    rows = _needs_key_rows(html)
    assert len(rows) == 3 and all("hidden" in r for r in rows)
    # Hidden, not removed: the stored toggles still ride along on a save.
    assert 'name="imagegen_enabled"' in html and 'name="collage_generated"' in html


def test_a_key_or_a_self_hosted_endpoint_brings_the_rows_back(svc):
    svc.config.imagegen_api_key = "sk-test-0123456789abcdef"
    assert all("hidden" not in r for r in _needs_key_rows(_page(svc)))
    svc.config.imagegen_api_key = ""
    svc.config.imagegen_provider = "a1111"
    assert all("hidden" not in r for r in _needs_key_rows(_page(svc)))
