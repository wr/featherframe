"""Phase 4 image backends: factory selection across providers, and each new
model's request/response shape (HTTP mocked — no keys, no network)."""
from __future__ import annotations

import base64

from featherframe.config import Config
from featherframe.render import genart
from featherframe.render.genart import (A1111ImageModel, GeminiImageModel,
                                        GeminiTextModel, OpenAIImageModel,
                                        OpenAITextModel, ReplicateImageModel)

_PNG = b"\x89PNG\r\n\x1a\nDATA"


class _Resp:
    def __init__(self, status=200, payload=None, content=b""):
        self.status_code = status
        self._payload = payload
        self.content = content
        self.text = ""

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}")


# -- factory selection ------------------------------------------------------
def test_make_image_model_selects_provider():
    assert isinstance(genart.make_image_model(Config(imagegen_provider="openai", imagegen_api_key="k")), OpenAIImageModel)
    assert isinstance(genart.make_image_model(Config(imagegen_provider="gemini", imagegen_api_key="k")), GeminiImageModel)
    assert isinstance(genart.make_image_model(Config(imagegen_provider="replicate", imagegen_api_key="k")), ReplicateImageModel)
    assert isinstance(genart.make_image_model(Config(imagegen_provider="a1111", imagegen_base_url="http://gpu:7860")), A1111ImageModel)


def test_self_hosted_needs_url_not_key():
    assert genart.make_image_model(Config(imagegen_provider="a1111", imagegen_base_url="")) is None
    # and requires no API key
    assert isinstance(genart.make_image_model(Config(imagegen_provider="a1111",
                      imagegen_base_url="http://gpu:7860", imagegen_api_key="")), A1111ImageModel)


def test_hosted_providers_need_key():
    assert genart.make_image_model(Config(imagegen_provider="gemini", imagegen_api_key="")) is None
    assert genart.make_image_model(Config(imagegen_provider="replicate", imagegen_api_key="")) is None


def test_leftover_model_id_does_not_leak_across_providers():
    # A stored OpenAI model id must not be sent to Gemini/Replicate.
    g = genart.make_image_model(Config(imagegen_provider="gemini", imagegen_api_key="k", imagegen_model="gpt-image-2"))
    assert g.model == "gemini-2.5-flash-image"
    r = genart.make_image_model(Config(imagegen_provider="replicate", imagegen_api_key="k", imagegen_model="gpt-image-2"))
    assert "/" in r.model


def test_make_text_model_per_provider():
    assert isinstance(genart.make_text_model(Config(imagegen_provider="openai", imagegen_api_key="k")), OpenAITextModel)
    assert isinstance(genart.make_text_model(Config(imagegen_provider="gemini", imagegen_api_key="k")), GeminiTextModel)
    assert genart.make_text_model(Config(imagegen_provider="replicate", imagegen_api_key="k")) is None
    assert genart.make_text_model(Config(imagegen_provider="a1111", imagegen_base_url="http://x")) is None


def test_gemini_text_model_falls_back_to_gemini_id():
    assert GeminiTextModel("k", model="gpt-5.6-luna").model == "gemini-2.5-flash"
    assert GeminiTextModel("k", model="gemini-3-pro").model == "gemini-3-pro"


# -- generate() shapes ------------------------------------------------------
def test_gemini_generate(monkeypatch):
    calls = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.update(url=url, headers=headers, json=json)
        b64 = base64.b64encode(_PNG).decode()
        return _Resp(200, {"candidates": [{"content": {"parts": [{"inline_data": {"data": b64}}]}}]})

    monkeypatch.setattr(genart.requests, "post", fake_post)
    out = GeminiImageModel("key", "gemini-2.5-flash-image").generate("draw a cardinal", "1024x1024", [])
    assert out == _PNG
    assert calls["headers"]["x-goog-api-key"] == "key"
    assert ":generateContent" in calls["url"]
    assert calls["json"]["contents"][0]["parts"][0]["text"] == "draw a cardinal"


def test_a1111_generate_txt2img(monkeypatch):
    calls = {}

    def fake_post(url, json=None, timeout=None):
        calls.update(url=url, json=json)
        return _Resp(200, {"images": [base64.b64encode(_PNG).decode()]})

    monkeypatch.setattr(genart.requests, "post", fake_post)
    out = A1111ImageModel("http://gpu:7860").generate("p", "512x768", [])
    assert out == _PNG
    assert calls["url"].endswith("/sdapi/v1/txt2img")
    assert calls["json"]["width"] == 512 and calls["json"]["height"] == 768


def test_replicate_generate_polls_and_downloads(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        return _Resp(201, {"status": "succeeded", "output": ["http://img/out.png"],
                           "urls": {"get": "http://x/get"}})

    def fake_get(url, headers=None, timeout=None):
        if url == "http://img/out.png":
            return _Resp(200, None, content=_PNG)
        return _Resp(200, {"status": "succeeded", "output": ["http://img/out.png"]})

    monkeypatch.setattr(genart.requests, "post", fake_post)
    monkeypatch.setattr(genart.requests, "get", fake_get)
    out = ReplicateImageModel("tok").generate("p", "1024x1024", [])
    assert out == _PNG


def test_gemini_generate_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(genart.requests, "post", lambda *a, **k: _Resp(429))
    try:
        GeminiImageModel("key").generate("p", "1024x1024", [])
        assert False, "expected GenerationError"
    except genart.GenerationError:
        pass
