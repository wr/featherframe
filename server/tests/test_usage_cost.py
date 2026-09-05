"""Token usage and estimated cost per generated plate: each vendor's usage
object is captured on the model as ``last_usage``, the plate sidecar records
the image call's (and the brief's) usage plus a USD estimate from the rate
table, and the config page shows "≈ $0.xx" per plate. HTTP is mocked."""
from __future__ import annotations

import base64
import io
import json

import pytest
from PIL import Image, ImageDraw
from starlette.testclient import TestClient

from featherframe.render import genart
from featherframe.render.genart import (AnthropicTextModel, GeminiImageModel,
                                        GeminiTextModel, GeneratedArtProvider,
                                        ImageModel, LocalTextModel,
                                        OpenAIImageModel, OpenAITextModel,
                                        estimate_cost_usd)

SLUG = "passer-domesticus"


def _plate_png() -> bytes:
    img = Image.new("L", (512, 768), 245)
    ImageDraw.Draw(img).ellipse([150, 250, 360, 520], fill=40)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class _Resp:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload
        self.text = ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}")


class UsageModel(ImageModel):
    """A fake image model that reports usage the way the OpenAI adapter does."""

    name = "gpt-image-2"
    quality = "high"

    def __init__(self, usage=None) -> None:
        self.usage = usage
        self.calls = 0

    def generate(self, prompt, size, refs) -> bytes:
        self.calls += 1
        self.last_usage = self.usage
        return _plate_png()


class UsageTextModel:
    name = "gpt-5.6-luna"

    def __init__(self, usage=None) -> None:
        self.usage = usage
        self.last_usage = None

    def complete_json(self, prompt):
        self.last_usage = self.usage
        return {"is_bird": True, "description": "A small brown sparrow.",
                "plants": [{"name": "wheat", "look": "bearded ears on hollow stems"}]}


IMG_USAGE = {"input_tokens": 1200, "output_tokens": 6240,
             "input_text_tokens": 400, "input_image_tokens": 800}


# -- vendor usage parsing ----------------------------------------------------
def test_openai_image_generate_records_usage(monkeypatch):
    def fake_post(url, headers=None, json=None, data=None, files=None, timeout=None):
        return _Resp(200, {
            "data": [{"b64_json": base64.b64encode(b"PNG").decode()}],
            "usage": {"input_tokens": 1200, "output_tokens": 6240,
                      "input_tokens_details": {"text_tokens": 400, "image_tokens": 800}}})

    monkeypatch.setattr(genart.requests, "post", fake_post)
    m = OpenAIImageModel("k")
    assert m.last_usage is None
    assert m.generate("p", "1024x1536", []) == b"PNG"
    assert m.last_usage == IMG_USAGE


def test_openai_image_usage_missing_leaves_none(monkeypatch):
    monkeypatch.setattr(genart.requests, "post", lambda *a, **k: _Resp(
        200, {"data": [{"b64_json": base64.b64encode(b"PNG").decode()}]}))
    m = OpenAIImageModel("k")
    m.last_usage = {"stale": 1}
    m.generate("p", "1024x1536", [])
    assert m.last_usage is None  # never carries a previous call's numbers


def test_gemini_image_generate_records_usage(monkeypatch):
    monkeypatch.setattr(genart.requests, "post", lambda *a, **k: _Resp(200, {
        "candidates": [{"content": {"parts": [
            {"inline_data": {"data": base64.b64encode(b"PNG").decode()}}]}}],
        "usageMetadata": {"promptTokenCount": 300, "candidatesTokenCount": 1290}}))
    m = GeminiImageModel("k")
    m.generate("p", "1024x1536", [])
    assert m.last_usage == {"input_tokens": 300, "output_tokens": 1290}


def test_openai_text_records_usage(monkeypatch):
    monkeypatch.setattr(genart.requests, "post", lambda *a, **k: _Resp(200, {
        "choices": [{"message": {"content": '{"is_bird": true}'}}],
        "usage": {"prompt_tokens": 150, "completion_tokens": 90, "total_tokens": 240}}))
    m = OpenAITextModel("k")
    m.complete_json("brief")
    assert m.last_usage == {"input_tokens": 150, "output_tokens": 90}


def test_local_text_records_usage(monkeypatch):
    monkeypatch.setattr(genart.requests, "post", lambda *a, **k: _Resp(200, {
        "choices": [{"message": {"content": '{"is_bird": true}'}}],
        "usage": {"prompt_tokens": 15, "completion_tokens": 9}}))
    m = LocalTextModel("http://gpu:11434", model="llama3")
    m.complete_json("brief")
    assert m.last_usage == {"input_tokens": 15, "output_tokens": 9}


def test_anthropic_text_records_usage(monkeypatch):
    monkeypatch.setattr(genart.requests, "post", lambda *a, **k: _Resp(200, {
        "content": [{"type": "text", "text": '{"is_bird": true}'}],
        "usage": {"input_tokens": 210, "output_tokens": 77}}))
    m = AnthropicTextModel("k")
    m.complete_json("brief")
    assert m.last_usage == {"input_tokens": 210, "output_tokens": 77}


def test_gemini_text_records_usage(monkeypatch):
    monkeypatch.setattr(genart.requests, "post", lambda *a, **k: _Resp(200, {
        "candidates": [{"content": {"parts": [{"text": '{"is_bird": true}'}]}}],
        "usageMetadata": {"promptTokenCount": 120, "candidatesTokenCount": 60,
                          "totalTokenCount": 180}}))
    m = GeminiTextModel("k")
    m.complete_json("brief")
    assert m.last_usage == {"input_tokens": 120, "output_tokens": 60}


# -- the rate table ----------------------------------------------------------
def test_estimate_cost_gpt_image_2():
    # 400 text in @ $5/M + 800 image in @ $8/M + 6240 out @ $30/M
    cost = estimate_cost_usd("gpt-image-2", IMG_USAGE)
    assert cost == pytest.approx((400 * 5 + 800 * 8 + 6240 * 30) / 1e6)


def test_estimate_cost_gpt_image_1_5_output_rate():
    cost = estimate_cost_usd("gpt-image-1.5", IMG_USAGE)
    assert cost == pytest.approx((400 * 5 + 800 * 8 + 6240 * 32) / 1e6)


def test_estimate_cost_without_detail_bills_input_as_text():
    cost = estimate_cost_usd("gpt-image-2", {"input_tokens": 1000, "output_tokens": 0})
    assert cost == pytest.approx(1000 * 5 / 1e6)


def test_estimate_cost_unknown_model_or_usage_is_none():
    assert estimate_cost_usd("gemini-2.5-flash-image", IMG_USAGE) is None
    assert estimate_cost_usd("gpt-image-2", None) is None
    assert estimate_cost_usd("gpt-image-2", {}) is None


# -- the sidecar -------------------------------------------------------------
@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    return tmp_path / "data"


def test_sidecar_records_usage_and_cost(data_dir):
    text = UsageTextModel({"input_tokens": 150, "output_tokens": 90})
    provider = GeneratedArtProvider(UsageModel(IMG_USAGE), refs=[], text_model=text)
    assert provider.artwork("House Sparrow", "Passer domesticus") is not None
    meta = json.loads(provider._sidecar(SLUG).read_text())
    assert meta["model"] == "gpt-image-2" and meta["quality"] == "high"
    assert meta["usage"] == {"image": IMG_USAGE,
                             "text": {"input_tokens": 150, "output_tokens": 90}}
    assert meta["cost_usd"] == pytest.approx((400 * 5 + 800 * 8 + 6240 * 30) / 1e6)
    # The brief's usage also lands beside the brief itself.
    briefs = json.loads((data_dir / "descriptions.json").read_text())
    assert briefs[SLUG]["usage"] == {"input_tokens": 150, "output_tokens": 90}


def test_sidecar_text_usage_is_none_on_a_cached_brief(data_dir):
    text = UsageTextModel({"input_tokens": 150, "output_tokens": 90})
    provider = GeneratedArtProvider(UsageModel(IMG_USAGE), refs=[], text_model=text)
    provider.artwork("House Sparrow", "Passer domesticus")
    # A regenerate reuses the cached brief: no second text call, no text usage.
    assert provider.regenerate("House Sparrow", "Passer domesticus")
    meta = json.loads(provider._sidecar(SLUG).read_text())
    assert meta["usage"]["image"] == IMG_USAGE
    assert meta["usage"]["text"] is None


def test_sidecar_without_usage_has_no_cost(data_dir):
    provider = GeneratedArtProvider(UsageModel(None), refs=[])
    provider.artwork("House Sparrow", "Passer domesticus")
    meta = json.loads(provider._sidecar(SLUG).read_text())
    assert meta["usage"] == {"image": None, "text": None}
    assert meta["cost_usd"] is None


# -- the config page ---------------------------------------------------------
@pytest.fixture
def client(data_dir):
    from featherframe.app import app
    from featherframe.service import FeatherframeService
    svc = FeatherframeService()
    svc.source.db_path = str(data_dir / "missing.db")
    app.state.service = svc
    return TestClient(app)


def test_config_page_shows_estimated_cost(client, data_dir):
    GeneratedArtProvider(UsageModel(IMG_USAGE), refs=[]).artwork(
        "House Sparrow", "Passer domesticus")
    r = client.get("/")
    assert r.status_code == 200
    assert 'class="cost"' in r.text
    assert "≈ $0.20" in r.text  # 0.0084 + 0.0064 + 0.1872 = 0.2020


def test_config_page_omits_cost_when_unknown(client, data_dir):
    GeneratedArtProvider(UsageModel(None), refs=[]).artwork(
        "House Sparrow", "Passer domesticus")
    r = client.get("/")
    assert r.status_code == 200
    assert 'class="cost"' not in r.text


def test_config_page_survives_a_sidecar_without_cost(client, data_dir):
    # Plates generated before usage was recorded have no cost_usd key at all
    # (not a null): the page must render, not 500 on an undefined attribute.
    provider = GeneratedArtProvider(UsageModel(None), refs=[])
    provider.artwork("House Sparrow", "Passer domesticus")
    meta = json.loads(provider._sidecar(SLUG).read_text())
    del meta["cost_usd"], meta["usage"]
    provider._sidecar(SLUG).write_text(json.dumps(meta))
    r = client.get("/")
    assert r.status_code == 200
    assert 'class="cost"' not in r.text
