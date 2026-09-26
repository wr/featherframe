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


# -- the frame: a grid drawn in place of the AI collage says why -------------
from datetime import datetime  # noqa: E402

from PIL import Image  # noqa: E402

from featherframe.render import pipeline, theme  # noqa: E402
from tests.test_corroborate import _GateSource, _rows  # noqa: E402

NOW = datetime(2026, 9, 2, 8, 0, 0)


@pytest.fixture
def grid_notes(svc, monkeypatch):
    """What the grid is asked to print as its footnote, per render."""
    svc._clock = lambda: NOW
    pipeline.DITHER_OVERRIDE = "none"
    svc.source = _GateSource([], today=_rows(4))
    svc.config.collage_generated = True
    seen = []

    def fake_render_collage(cells, provider, **kw):
        seen.append((kw.get("note"), kw.get("note_kind")))
        return Image.new("L", (theme.WIDTH, theme.HEIGHT), 255)
    from featherframe.service import collage_mod
    monkeypatch.setattr(collage_mod, "render_collage", fake_render_collage)
    return seen


def _fail_with(svc, message):
    svc._note_imagegen(GenerationError(message))
    svc.genart.day_composite = lambda cells, when, force=False, **_: None


def test_grid_says_out_of_credits(svc, grid_notes):
    _fail_with(svc, 'HTTP 429: {"error": {"code": "insufficient_quota"}}')
    assert svc._build_collage(NOW, NOW.date()) is True
    assert grid_notes == [("Out of OpenAI credits: add more for the AI collage", "imagegen")]


def test_grid_says_the_key_was_rejected(svc, grid_notes):
    _fail_with(svc, "HTTP 401: invalid_api_key")
    svc._build_collage(NOW, NOW.date())
    assert grid_notes == [("AI key rejected: replace it on the webapp", "imagegen")]


def test_grid_says_nothing_for_a_passing_failure(svc, grid_notes):
    # A timeout is tried again at the next redraw: no alarm on the glass.
    _fail_with(svc, "HTTP 500: upstream")
    svc._build_collage(NOW, NOW.date())
    assert grid_notes == [(None, None)]


def test_grid_says_nothing_with_the_ai_collage_off(svc, grid_notes):
    _fail_with(svc, 'HTTP 429: {"error": {"code": "insufficient_quota"}}')
    svc.config.collage_generated = False
    svc._build_collage(NOW, NOW.date())
    assert grid_notes == [(None, None)]


def test_an_alarm_about_detections_comes_first(svc, grid_notes, monkeypatch):
    _fail_with(svc, 'HTTP 429: {"error": {"code": "insufficient_quota"}}')
    monkeypatch.setattr(svc, "_note_text", lambda: "No detections since 7:00 am")
    monkeypatch.setattr(svc, "_note_kind", lambda: "quiet")
    svc._build_collage(NOW, NOW.date())
    assert grid_notes == [("No detections since 7:00 am", "quiet")]


# -- the frame: the empty branch drawn in place of an AI illustration --------
from featherframe.render import compose as compose_mod  # noqa: E402
from featherframe.render.provider import ArtProvider, Artwork  # noqa: E402
from featherframe.sources.base import Detection  # noqa: E402


class _Art(ArtProvider):
    name = "stub"

    def __init__(self, art):
        self.art = art

    def artwork(self, common_name, scientific_name):
        return self.art


def _pills(monkeypatch):
    seen = []
    monkeypatch.setattr(compose_mod.typography, "note_line",
                        lambda field, text, *a, kind=None, **k: seen.append((text, kind)))
    return seen


def test_the_empty_branch_carries_the_failure(monkeypatch):
    pills = _pills(monkeypatch)
    spec = compose_mod.SingleSpec("Veery", "Catharus fuscescens",
                                  fallback_note="AI key rejected: replace it on the webapp")
    compose_mod.render_single(spec, _Art(None))
    assert pills == [("AI key rejected: replace it on the webapp", "imagegen")]


def test_an_illustration_never_does(monkeypatch):
    pills = _pills(monkeypatch)
    art = Artwork(image=Image.new("L", (600, 800), 240))
    spec = compose_mod.SingleSpec("Veery", "Catharus fuscescens",
                                  fallback_note="AI key rejected: replace it on the webapp")
    compose_mod.render_single(spec, _Art(art))
    assert pills == []


def test_an_alarm_about_detections_comes_first_on_the_branch(monkeypatch):
    pills = _pills(monkeypatch)
    spec = compose_mod.SingleSpec("Veery", "Catharus fuscescens",
                                  note="No detections since 7:00 am", note_kind="quiet",
                                  fallback_note="AI key rejected: replace it on the webapp")
    compose_mod.render_single(spec, _Art(None))
    assert pills == [("No detections since 7:00 am", "quiet")]


def _specs(svc, monkeypatch):
    seen = []
    monkeypatch.setattr(compose_mod, "render_single",
                        lambda spec, provider, color=False: seen.append(spec)
                        or Image.new("L", (theme.WIDTH, theme.HEIGHT), 255))
    return seen


def _veery():
    return Detection(rowid=1, date="2026-09-02", time="08:00:00", common_name="Veery",
                     scientific_name="Catharus fuscescens", confidence=0.9)


def test_service_hands_the_branch_its_note(svc, monkeypatch):
    svc._clock = lambda: NOW
    specs = _specs(svc, monkeypatch)
    svc._note_imagegen(GenerationError('HTTP 429: {"error": {"code": "insufficient_quota"}}'))
    svc._render_single(_veery(), NOW, reason="new")
    assert specs[-1].fallback_note == "Out of OpenAI credits: add more for AI illustrations"


def test_service_says_nothing_without_a_failure_or_a_model(svc, monkeypatch):
    svc._clock = lambda: NOW
    specs = _specs(svc, monkeypatch)
    svc._render_single(_veery(), NOW, reason="new")
    assert specs[-1].fallback_note is None
    svc._note_imagegen(GenerationError("HTTP 401: invalid_api_key"))
    svc.genart._model = None
    svc._render_single(_veery(), NOW, reason="new")
    assert specs[-1].fallback_note is None
