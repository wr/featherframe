"""A generation that fails is said on the page, not only in the log: an
account out of credits, a rejected key, or any other failure turns the AI
image generation row red (or amber) and says what to do. A success, or a new
key, clears it."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from featherframe.config import Config, save_config
from featherframe.db import Database
from featherframe.render.genart import (
    GenerationError,
    GeneratedArtProvider,
    failure_reason,
)
from tests.test_genart import FakeModel


@pytest.mark.parametrize("message, reason", [
    # OpenAI: quota and a hard billing limit.
    ('HTTP 429: {"error": {"code": "insufficient_quota", "message": "You exceeded your current quota"}}', "credits"),
    ('HTTP 400: {"error": {"code": "billing_hard_limit_reached"}}', "credits"),
    # Replicate: 402 Payment Required.
    ('HTTP 402: {"detail": "You have insufficient credit to run this model."}', "credits"),
    ('HTTP 401: {"error": {"code": "invalid_api_key", "message": "Incorrect API key provided"}}', "key"),
    ('HTTP 400: {"error": {"message": "API key not valid. Please pass a valid API key.", "status": "INVALID_ARGUMENT"}}', "key"),
    ("HTTP 500: upstream", "other"),
    ("boom", "other"),
])
def test_failure_reason(message, reason):
    assert failure_reason(GenerationError(message)) == reason


class _Broke(FakeModel):
    def generate(self, prompt, size, refs):
        self.calls += 1
        raise GenerationError('HTTP 429: {"error": {"code": "insufficient_quota"}}')


def test_provider_reports_each_outcome(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    seen = []
    provider = GeneratedArtProvider(_Broke())
    provider.on_outcome = seen.append
    assert provider.artwork("Veery", "Catharus fuscescens") is None
    assert len(seen) == 1 and failure_reason(seen[0]) == "credits"
    provider._model = FakeModel()
    provider._failed_at.clear()
    assert provider.artwork("Veery", "Catharus fuscescens") is not None
    assert seen[-1] is None


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    monkeypatch.setenv("FEATHERFRAME_DB", str(tmp_path / "ff.db"))
    from featherframe.service import FeatherframeService
    db = Database(tmp_path / "ff.db")
    save_config(db, Config(imagegen_api_key="sk-proj-verysecretkey1234"))
    return FeatherframeService(db)


def _page(svc) -> str:
    from featherframe.app import app
    app.state.service = svc
    return TestClient(app, raise_server_exceptions=False).get("/").text


def _section(html: str) -> str:
    return html.split('id="set-imagegen"')[1].split("</form>")[0]


def test_out_of_credits_is_on_the_page(svc):
    svc.genart._model = _Broke()
    assert svc.genart.artwork("Veery", "Catharus fuscescens") is None
    err = svc.status()["imagegen_error"]
    assert err["reason"] == "credits" and err["state"] == "bad"
    assert err["summary"] == "Out of credits"
    html = _page(svc)
    sec = _section(html)
    assert 'id="set-imagegen" data-state="bad" open' in html
    assert '<span class="d bad" id="ig-dot"></span>' in sec
    assert "Out of credits" in sec.split("</summary>")[0]
    assert "OpenAI is out of credits" in sec
    assert "Add credits to your OpenAI account" in sec


def test_a_success_clears_it(svc):
    svc.genart._model = _Broke()
    svc.genart.artwork("Veery", "Catharus fuscescens")
    svc.genart._model = FakeModel()
    svc.genart._failed_at.clear()
    svc.genart.artwork("Veery", "Catharus fuscescens")
    assert svc.status()["imagegen_error"] is None
    html = _page(svc)
    assert 'id="set-imagegen" data-state="good"' in html
    assert '<span class="d good" id="ig-dot"></span>' in html


def test_a_new_key_clears_it(svc):
    svc.genart._model = _Broke()
    svc.genart.artwork("Veery", "Catharus fuscescens")
    save_config(svc.db, Config(imagegen_api_key="sk-proj-anotherkey56789"))
    svc.reload_config()
    assert svc.status()["imagegen_error"] is None


def test_no_key_means_no_error(svc):
    svc.genart._model = _Broke()
    svc.genart.artwork("Veery", "Catharus fuscescens")
    save_config(svc.db, Config(imagegen_api_key=""))
    svc.reload_config()
    assert svc.status()["imagegen_error"] is None
