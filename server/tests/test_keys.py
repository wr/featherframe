"""The owner's AI keys on Featherframe Cloud (W-937): the front door holds
them, the DB keeps a stand-in, and every model's request goes to the front
door's gate, which adds the key. Self-hosted, nothing changes."""
from __future__ import annotations

import base64
from pathlib import Path

import pytest
from starlette.requests import Request
from starlette.responses import Response
from starlette.testclient import TestClient

from featherframe import hosted, keys, spend
from featherframe.config import Config, load_config, save_config
from featherframe.render import genart, pipeline
from tests._door import Door

KEY = "sk-proj-abcdefghijklmnopqrstuvwxyz0123456789"
TEXT_KEY = "sk-ant-api03-zyxwvutsrqponmlkjihgfedcba9876"
_PNG = b"\x89PNG\r\n\x1a\nDATA"


class _Resp:
    def __init__(self, status=200, payload=None, content=b"", headers=None):
        self.status_code = status
        self._payload = payload
        self.content = content
        self.text = ""
        self.headers = headers or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise AssertionError(f"HTTP {self.status_code}")


# -- the stand-in -------------------------------------------------------------
def test_a_stand_in_shows_the_mask_and_is_the_same_key_but_never_the_key():
    held = keys.stand_in(KEY)
    assert keys.is_held(held) and KEY not in held
    assert keys.mask(held) == keys.mask(KEY) == "sk-pr…6789"
    assert keys.key_id(held) == keys.key_id(KEY)
    # Two keys that share their first and last characters are still two keys.
    other = KEY[:5] + "different-middle" + KEY[-4:]
    assert keys.mask(other) == keys.mask(KEY) and keys.key_id(other) != keys.key_id(KEY)
    assert keys.mask("") == keys.key_id("") == ""


# -- where a request goes -----------------------------------------------------
def test_a_key_of_its_own_goes_to_the_vendor_and_a_held_one_to_the_gate():
    direct = keys.Vendor("openai", KEY, "image")
    assert direct.url("/v1/images/edits") == "https://api.openai.com/v1/images/edits"
    assert direct.headers() == {"Authorization": f"Bearer {KEY}"}
    held = keys.Vendor("openai", keys.stand_in(KEY), "image")
    assert held.url("/v1/images/edits") == "http://openai.vendor.internal/v1/images/edits"
    assert held.headers() == {"X-FF-Key": "image"}         # no purchase open
    with spend.Gate.unlimited().purchase("plate", "test", model="m") as p:
        assert held.headers() == {"X-FF-Key": "image", "X-FF-Purchase": p.record.id}
    rep = keys.Vendor("replicate", keys.stand_in(KEY), "image")
    assert rep.rebase("https://api.replicate.com/v1/predictions/abc") == \
        "http://replicate.vendor.internal/v1/predictions/abc"
    assert rep.rebase("https://replicate.delivery/x.png") == "https://replicate.delivery/x.png"
    assert keys.Vendor("replicate", KEY, "image").rebase(
        "https://api.replicate.com/v1/predictions/abc") == "https://api.replicate.com/v1/predictions/abc"


@pytest.mark.usefixtures("metered")
@pytest.mark.parametrize("make,vendor,reply", [
    (lambda k: genart.OpenAIImageModel(k), "openai",
     {"data": [{"b64_json": base64.b64encode(_PNG).decode()}]}),
    (lambda k: genart.GeminiImageModel(k), "gemini",
     {"candidates": [{"content": {"parts": [{"inline_data": {"data": base64.b64encode(_PNG).decode()}}]}}]}),
])
def test_every_image_model_sends_a_held_key_to_the_gate_without_it(monkeypatch, make, vendor, reply):
    sent = {}

    def post(url, headers=None, **kw):
        sent.update(url=url, headers=headers)
        return _Resp(200, reply)

    monkeypatch.setattr(genart.requests, "post", post)
    assert make(keys.stand_in(KEY)).generate("p", "1024x1024", []) == _PNG
    assert sent["url"].startswith(f"http://{vendor}.vendor.internal/")
    assert sent["headers"]["X-FF-Key"] == "image" and sent["headers"]["X-FF-Purchase"]
    assert not {"Authorization", "x-goog-api-key", "x-api-key"} & set(sent["headers"])


@pytest.mark.usefixtures("metered")
def test_replicate_polls_and_downloads_through_the_gate_where_the_key_is_needed(monkeypatch):
    gets = []

    def post(url, headers=None, json=None, timeout=None):
        assert url == "http://replicate.vendor.internal/v1/models/black-forest-labs/flux-kontext-pro/predictions"
        return _Resp(201, {"status": "starting",
                           "urls": {"get": "https://api.replicate.com/v1/predictions/p1"}})

    def get(url, headers=None, timeout=None):
        gets.append((url, dict(headers or {})))
        if url.startswith("https://replicate.delivery/"):
            return _Resp(200, content=_PNG)
        return _Resp(200, {"status": "succeeded", "output": ["https://replicate.delivery/o.png"]})

    monkeypatch.setattr(genart.requests, "post", post)
    monkeypatch.setattr(genart.requests, "get", get)
    monkeypatch.setattr(genart.time, "sleep", lambda s: None)
    assert genart.ReplicateImageModel(keys.stand_in(KEY)).generate("p", "1024x1024", []) == _PNG
    assert gets[0][0] == "http://replicate.vendor.internal/v1/predictions/p1"
    assert gets[0][1]["X-FF-Key"] == "image" and "Authorization" not in gets[0][1]
    assert gets[-1][0] == "https://replicate.delivery/o.png"


@pytest.mark.usefixtures("metered")
def test_the_text_models_name_the_key_they_were_built_from(monkeypatch):
    sent = []
    reply = {"choices": [{"message": {"content": "{}"}}], "content": [{"type": "text", "text": "{}"}],
             "output": [{"type": "message", "content": [{"text": "{}"}]}], "candidates": [{"content": {"parts": [{"text": "{}"}]}}]}
    monkeypatch.setattr(genart.requests, "post",
                        lambda url, headers=None, **kw: sent.append((url, headers)) or _Resp(200, reply))
    same = genart.make_text_model(Config(imagegen_provider="openai", imagegen_api_key=keys.stand_in(KEY)))
    same.complete_json("p")
    same.search_json("p")
    own = genart.make_text_model(Config(imagegen_provider="openai", imagegen_text_provider="anthropic",
                                        imagegen_text_key=keys.stand_in(TEXT_KEY)))
    own.complete_json("p")
    gem = genart.make_text_model(Config(imagegen_provider="gemini", imagegen_api_key=keys.stand_in(KEY)))
    gem.complete_json("p")
    assert [(u, h["X-FF-Key"]) for u, h in sent] == [
        ("http://openai.vendor.internal/v1/chat/completions", "image"),
        ("http://openai.vendor.internal/v1/responses", "image"),
        ("http://anthropic.vendor.internal/v1/messages", "text"),
        ("http://gemini.vendor.internal/v1beta/models/gemini-2.5-flash:generateContent", "image")]
    assert all("x-api-key" not in h and "Authorization" not in h for _, h in sent)


def test_a_held_key_never_goes_to_a_local_endpoint(monkeypatch):
    sent = {}
    monkeypatch.setattr(genart.requests, "post", lambda url, headers=None, **kw: sent.update(h=headers)
                        or _Resp(200, {"choices": [{"message": {"content": "{}"}}]}))
    with spend.Gate.unlimited().purchase("plate", "test", model="m"):
        genart.LocalTextModel("http://llm", api_key=keys.stand_in(KEY)).complete_json("p")
    assert "Authorization" not in sent["h"]


def test_the_model_list_asks_through_the_gate(monkeypatch):
    asked = {}
    monkeypatch.setattr(genart.requests, "get", lambda url, headers=None, timeout=None: asked.update(
        url=url, h=headers) or _Resp(200, {"data": [{"id": "gpt-image-2"}]}))
    out = genart._query_image_models("openai", keys.stand_in(KEY), "")
    assert out["live"] and asked["url"] == "http://openai.vendor.internal/v1/models"
    assert asked["h"] == {"X-FF-Key": "image"}


def test_a_key_moves_only_when_the_gate_answers_as_the_vendor_does():
    def http(direct, via, via_headers=None):
        class H:
            def get(self, url, headers=None, timeout=None):
                if url.startswith("http://"):
                    assert headers["X-FF-Key"] == "image" and "Authorization" not in headers
                    return _Resp(via, headers=via_headers or {})
                assert headers["Authorization"] == f"Bearer {KEY}"
                return _Resp(direct)
        return H()

    assert keys.gate_agrees("openai", "image", KEY, http(200, 200))
    assert keys.gate_agrees("openai", "image", KEY, http(401, 401))        # a bad key is no reason to stay
    assert not keys.gate_agrees("openai", "image", KEY, http(200, 401))
    assert not keys.gate_agrees("openai", "image", KEY, http(404, 404, {"X-FF-Gate": "no key"}))


# -- the server on Cloud --------------------------------------------------------
def front_door():
    door = Door()
    door.state.keys, door.state.calls = {}, []

    @door.api_route("/h/keys/{slot}", methods=["PUT", "DELETE"])
    async def one(request: Request, slot: str):
        assert request.headers.get("authorization") == "Bearer k"
        door.state.calls.append((request.method, slot))
        if request.method == "PUT":
            door.state.keys[slot] = (await request.json())["key"]
        else:
            door.state.keys.pop(slot, None)
        return Response(status_code=204)

    return door


@pytest.fixture
def cloud(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    door = front_door()
    link = hosted.HostedLink("http://door/h", "k", tmp_path / "data", session=TestClient(door))
    hosted.activate(link)
    yield door, link, tmp_path / "data"
    hosted.activate(None)


def _service(cfg: Config = None):
    from featherframe.service import FeatherframeService
    pipeline.DITHER_OVERRIDE = "none"
    svc = FeatherframeService()
    if cfg is not None:
        save_config(svc.db, cfg)
        svc.config = load_config(svc.db)
        svc.provider = svc._build_provider(svc.config)
    return svc


def _pushed_db(link) -> bytes:
    """The database as a sync would send it to the front door."""
    return Path(link._local()[hosted.DB_NAME]).read_bytes()


def test_a_key_saved_on_cloud_goes_to_the_front_door_and_never_into_the_db(cloud):
    door, link, data = cloud
    svc = _service()
    svc.update_config(Config(imagegen_api_key=KEY, imagegen_text_provider="anthropic",
                             imagegen_text_key=TEXT_KEY))
    assert door.state.keys == {"image": KEY, "text": TEXT_KEY}
    assert svc.config.imagegen_api_key == keys.stand_in(KEY)
    assert svc._masked_config()["imagegen_api_key"] == "sk-pr…6789"
    assert isinstance(svc.genart._model, genart.OpenAIImageModel) and svc.genart._model.vendor.held
    pushed = _pushed_db(link)
    assert KEY.encode() not in pushed and TEXT_KEY.encode() not in pushed
    # Saving something else keeps the key held; clearing it drops it there.
    svc.update_config(load_config(svc.db).__class__(**{**svc.config.to_dict(), "region": "europe"}))
    assert door.state.calls == [("PUT", "image"), ("PUT", "text")]
    svc.update_config(Config(**{**svc.config.to_dict(), "imagegen_api_key": ""}))
    assert door.state.keys == {"text": TEXT_KEY} and svc.config.imagegen_api_key == ""


def test_a_front_door_that_does_not_take_the_key_saves_nothing(cloud, monkeypatch):
    door, link, data = cloud
    svc = _service()
    monkeypatch.setattr(link, "hold_key", lambda slot, key: (_ for _ in ()).throw(OSError("down")))
    with pytest.raises(RuntimeError, match="could not be stored"):
        svc.update_config(Config(imagegen_api_key=KEY))
    assert load_config(svc.db).imagegen_api_key == ""


def test_an_older_households_key_moves_once_the_gate_answers_for_it(cloud, monkeypatch):
    door, link, data = cloud
    svc = _service(Config(imagegen_api_key=KEY))
    svc.db.set("imagegen_cooldowns", {"blue jay": 1})
    svc._collage_redraw = False
    assert KEY.encode() in _pushed_db(link)
    asked = []
    monkeypatch.setattr(keys, "gate_agrees", lambda v, s, k: asked.append((v, s, k)) or True)
    assert svc.hold_keys() is True
    assert asked == [("openai", "image", KEY)] and door.state.keys == {"image": KEY}
    assert load_config(svc.db).imagegen_api_key == keys.stand_in(KEY)
    assert svc.genart._model.vendor.held
    assert KEY.encode() not in _pushed_db(link)
    # The same key: nothing it failed on is forgotten, nothing is bought again.
    assert svc.db.get("imagegen_cooldowns") == {"blue jay": 1} and not svc._collage_redraw
    assert svc.hold_keys() is True and door.state.calls == [("PUT", "image")]


def test_a_key_the_gate_cannot_answer_for_stays_and_keeps_working(cloud, monkeypatch):
    door, link, data = cloud
    svc = _service(Config(imagegen_api_key=KEY))
    monkeypatch.setattr(keys, "gate_agrees", lambda v, s, k: False)
    assert svc.hold_keys() is False
    assert door.state.keys == {} and door.state.calls == [("PUT", "image"), ("DELETE", "image")]
    assert load_config(svc.db).imagegen_api_key == KEY and not svc.genart._model.vendor.held


def test_a_take_moves_the_keys_after_the_queue(cloud, monkeypatch):
    door, link, data = cloud
    svc = _service()
    moved = []
    monkeypatch.setattr(link, "_apply_checkins", lambda s: None)
    monkeypatch.setattr(link, "take_queue", lambda: [])
    monkeypatch.setattr(svc, "hold_keys", lambda: moved.append(link.queue_ready.is_set()) or True)
    link.queue_ready.clear()
    link.take(svc)
    link.take(svc)
    assert moved == [True]                  # once, and never ahead of a report


def test_self_hosted_keeps_its_key(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    hosted.activate(None)
    svc = _service()
    svc.update_config(Config(imagegen_api_key=KEY))
    assert load_config(svc.db).imagegen_api_key == KEY and not svc.genart._model.vendor.held
    assert svc.hold_keys() is True


def test_cloud_offers_only_the_vendors_its_keys_can_reach(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    hosted.activate(None)
    from featherframe.app import app
    app.state.service = _service()
    app.state.hosted = None
    client = TestClient(app, raise_server_exceptions=False)
    try:
        page = client.get("/").text
        assert 'value="a1111"' in page and 'value="local"' in page
        app.state.hosted = type("Link", (), {"settle": lambda *a: None})()
        page = client.get("/").text
        assert 'value="a1111"' not in page and 'value="local"' not in page
        assert 'value="anthropic"' in page and 'value="replicate"' in page
    finally:
        app.state.hosted = None
