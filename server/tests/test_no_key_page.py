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
    assert 'class="opt">\u00b7 optional' in html
    assert "docs/ai-plates.md" in html and ">Learn more</a>" in html
    rows = _needs_key_rows(html)
    assert len(rows) == 1 and all("hidden" in r for r in rows)
    # Both AI toggles are LOCKED, not disabled \u2014 a disabled checkbox posts
    # nothing, so a save would quietly store the setting off.
    for name in ('name="imagegen_enabled"', 'name="collage_generated"'):
        assert name in html
        field = html.split(name)[1].split(">")[0]
        assert 'aria-disabled="true"' in field and "disabled>" not in field
    assert html.count(">Needs an API key<") == 2
    assert html.count('class="frow toggle locked"') == 2


def test_a_key_or_a_self_hosted_endpoint_brings_the_rows_back(svc):
    def unlocked(html):
        return (all("hidden" not in r for r in _needs_key_rows(html))
                and 'class="frow toggle locked"' not in html
                and ">Needs an API key<" not in html)
    svc.config.imagegen_api_key = "sk-test-0123456789abcdef"
    assert unlocked(_page(svc))
    svc.config.imagegen_api_key = ""
    svc.config.imagegen_provider = "a1111"
    assert unlocked(_page(svc))
