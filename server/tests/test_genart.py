"""Generated-art provider: cache-forever behavior, provider chaining, config,
and key masking. The image model is faked — no network in tests."""
from __future__ import annotations

import io
import json

import pytest
from PIL import Image, ImageDraw

from featherframe.config import Config
from featherframe.render.genart import (
    GeneratedArtProvider,
    ImageModel,
    OpenAIImageModel,
    build_prompt,
    make_image_model,
    slugify,
)
from featherframe.render.provider import ArtProvider, Artwork, ChainedProvider


def _plate_png() -> bytes:
    """A plausible fake plate: light paper, one dark subject mass."""
    img = Image.new("L", (512, 768), 245)
    d = ImageDraw.Draw(img)
    d.ellipse([150, 250, 360, 520], fill=40)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


class FakeModel(ImageModel):
    name = "fake"

    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    def generate(self, prompt: str, size: str, refs) -> bytes:
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")
        return _plate_png()


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


# -- slug -------------------------------------------------------------------
def test_slugify_scientific_names():
    assert slugify("Passer domesticus") == "passer-domesticus"
    assert slugify("Myotis austroriparius") == "myotis-austroriparius"
    assert slugify("  Sturnus   vulgaris ") == "sturnus-vulgaris"
    assert slugify("Cooper's Hawk!") == "coopers-hawk"


# -- cache behavior ---------------------------------------------------------
def test_generates_once_then_serves_cache(data_dir):
    model = FakeModel()
    provider = GeneratedArtProvider(model)

    art = provider.artwork("House Sparrow", "Passer domesticus")
    assert isinstance(art, Artwork)
    assert art.generated is True
    assert art.image.mode == "L"
    assert model.calls == 1

    png = data_dir / "generated" / "passer-domesticus.png"
    sidecar = data_dir / "generated" / "passer-domesticus.json"
    assert png.exists() and sidecar.exists()
    meta = json.loads(sidecar.read_text())
    assert meta["scientific"] == "Passer domesticus"
    assert meta["common"] == "House Sparrow"
    assert meta["model"] == "fake"

    # Second ask: cache, no second generation.
    art2 = provider.artwork("House Sparrow", "Passer domesticus")
    assert isinstance(art2, Artwork)
    assert model.calls == 1


def test_cache_only_without_model(data_dir):
    # No model (no key): misses return None, hits are still served.
    cache_only = GeneratedArtProvider(None)
    assert cache_only.artwork("House Sparrow", "Passer domesticus") is None

    GeneratedArtProvider(FakeModel()).artwork("House Sparrow", "Passer domesticus")
    art = cache_only.artwork("House Sparrow", "Passer domesticus")
    assert isinstance(art, Artwork)
    assert art.generated is True


def test_failure_cools_down_and_falls_back(data_dir):
    model = FakeModel(fail=True)
    provider = GeneratedArtProvider(model)

    assert provider.artwork("House Sparrow", "Passer domesticus") is None
    assert model.calls == 1
    # Within the cooldown window the model must not be hammered again.
    assert provider.artwork("House Sparrow", "Passer domesticus") is None
    assert model.calls == 1

    # With the cooldown disabled it retries.
    eager = GeneratedArtProvider(model, cooldown_s=0)
    assert eager.artwork("House Sparrow", "Passer domesticus") is None
    assert model.calls == 2


def test_generation_failure_never_raises(data_dir):
    provider = GeneratedArtProvider(FakeModel(fail=True))
    assert provider.artwork("Any Bird", "Avis quaevis") is None


def test_regenerate_replaces_cache(data_dir):
    model = FakeModel()
    provider = GeneratedArtProvider(model)
    provider.artwork("House Sparrow", "Passer domesticus")
    assert model.calls == 1

    assert provider.regenerate("House Sparrow", "Passer domesticus") is True
    assert model.calls == 2

    # Failed regeneration keeps the old cached plate.
    provider._model = FakeModel(fail=True)
    assert provider.regenerate("House Sparrow", "Passer domesticus") is False
    assert isinstance(provider.artwork("House Sparrow", "Passer domesticus"), Artwork)


def test_delete_removes_cache(data_dir):
    provider = GeneratedArtProvider(FakeModel())
    provider.artwork("House Sparrow", "Passer domesticus")
    assert provider.delete("passer-domesticus") is True
    assert provider.artwork("House Sparrow", "Passer domesticus") is not None  # regenerates
    assert provider.delete("turdus-nemo") is False


def test_corrupt_cache_self_heals(data_dir):
    model = FakeModel()
    provider = GeneratedArtProvider(model)
    provider.artwork("House Sparrow", "Passer domesticus")
    # Simulate a torn write (power cut mid-write on the wall frame).
    png = data_dir / "generated" / "passer-domesticus.png"
    png.write_bytes(png.read_bytes()[:100])

    cache_only = GeneratedArtProvider(None)
    assert cache_only.artwork("House Sparrow", "Passer domesticus") is None
    assert not png.exists()  # corrupt file removed so the species can retry

    # With a model available the species regenerates instead of wedging.
    assert provider.artwork("House Sparrow", "Passer domesticus") is not None
    assert model.calls == 2


def test_no_double_purchase_after_lock_wait(data_dir):
    model = FakeModel()
    provider = GeneratedArtProvider(model)
    provider.artwork("House Sparrow", "Passer domesticus")
    # A caller that queued on the generation lock while another thread bought
    # the plate must see the fresh cache and not buy again.
    assert provider._generate_to_cache("passer-domesticus", "House Sparrow",
                                       "Passer domesticus") is True
    assert model.calls == 1
    # The explicit regenerate path still forces a purchase.
    assert provider.regenerate("House Sparrow", "Passer domesticus") is True
    assert model.calls == 2


def test_cached_species_listing(data_dir):
    provider = GeneratedArtProvider(FakeModel())
    provider.artwork("House Sparrow", "Passer domesticus")
    provider.artwork("European Starling", "Sturnus vulgaris")
    listed = provider.cached_species()
    assert {e["scientific"] for e in listed} == {"Passer domesticus", "Sturnus vulgaris"}
    assert all(e["slug"] and e["created_at"] for e in listed)


# -- chaining ---------------------------------------------------------------
class _StubProvider(ArtProvider):
    name = "stub"

    def __init__(self, art):
        self._art = art
        self.asked = 0

    def artwork(self, common_name, scientific_name):
        self.asked += 1
        return self._art


def test_chain_prefers_first_provider(data_dir):
    real = Artwork(image=Image.new("L", (10, 10)), audubon_plate=1)
    first = _StubProvider(real)
    second = _StubProvider(Artwork(image=Image.new("L", (10, 10)), audubon_plate=None))
    chain = ChainedProvider([first, second])
    assert chain.artwork("X", "Y") is real
    assert second.asked == 0


def test_chain_falls_through_to_generated(data_dir):
    chain = ChainedProvider([_StubProvider(None), GeneratedArtProvider(FakeModel())])
    art = chain.artwork("House Sparrow", "Passer domesticus")
    assert isinstance(art, Artwork) and art.generated


def test_chain_returns_none_when_empty(data_dir):
    assert ChainedProvider([_StubProvider(None), _StubProvider(None)]).artwork("X", "Y") is None


# -- config -----------------------------------------------------------------
def test_config_imagegen_defaults_and_sanitize():
    c = Config()
    assert c.imagegen_enabled is True
    assert c.imagegen_provider == "openai"
    assert c.imagegen_model == "gpt-image-2"
    assert c.imagegen_quality == "high"
    assert c.imagegen_api_key == ""

    c = Config(imagegen_provider="nonsense", imagegen_quality="ultra",
               imagegen_model="  ", imagegen_api_key="  sk-x  ")
    assert c.imagegen_provider == "openai"
    assert c.imagegen_quality == "high"
    assert c.imagegen_model == "gpt-image-2"
    assert c.imagegen_api_key == "sk-x"


def test_config_roundtrip_keeps_imagegen():
    c = Config(imagegen_api_key="sk-test", imagegen_model="gpt-image-1.5")
    again = Config.from_dict(c.to_dict())
    assert again.imagegen_api_key == "sk-test"
    assert again.imagegen_model == "gpt-image-1.5"


# -- model factory ----------------------------------------------------------
def test_make_image_model():
    c = Config(imagegen_api_key="sk-test", imagegen_model="gpt-image-1.5",
               imagegen_quality="medium")
    model = make_image_model(c)
    assert isinstance(model, OpenAIImageModel)
    assert model.model == "gpt-image-1.5"
    assert model.quality == "medium"

    assert make_image_model(Config(imagegen_api_key="")) is None
    assert make_image_model(Config(imagegen_api_key="k", imagegen_enabled=False)) is None


# -- service-level guarantees ----------------------------------------------
def test_service_masks_key_and_counts_cache(data_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DB", str(tmp_path / "ff.db"))
    from featherframe.config import save_config
    from featherframe.db import Database
    from featherframe.service import FeatherframeService

    GeneratedArtProvider(FakeModel()).artwork("House Sparrow", "Passer domesticus")

    db = Database(tmp_path / "ff.db")
    save_config(db, Config(imagegen_api_key="sk-proj-verysecretkey1234"))
    svc = FeatherframeService(db)
    status = svc.status()

    assert "verysecretkey" not in json.dumps(status)
    assert status["config"]["imagegen_api_key"].endswith("1234")
    assert status["generated_cached"] == 1
    assert svc.genart is not None


def test_service_serves_paid_plates_even_when_disabled(data_dir, tmp_path, monkeypatch):
    """Turning generation off must never hide plates the user already bought:
    the chain stays, only the model goes away (cache-only)."""
    monkeypatch.setenv("FEATHERFRAME_DB", str(tmp_path / "ff.db"))
    from featherframe.config import save_config
    from featherframe.db import Database
    from featherframe.service import FeatherframeService

    GeneratedArtProvider(FakeModel()).artwork("House Sparrow", "Passer domesticus")

    db = Database(tmp_path / "ff.db")
    save_config(db, Config(imagegen_enabled=False))
    svc = FeatherframeService(db)
    assert isinstance(svc.provider, ChainedProvider)
    assert svc.genart._model is None
    art = svc.provider.artwork("House Sparrow", "Passer domesticus")
    assert art is not None and art.generated


# -- prompt -----------------------------------------------------------------
def test_prompt_mentions_species_and_bans_text():
    p = build_prompt("Southeastern myotis", "Myotis austroriparius")
    assert "Southeastern myotis" in p
    assert "Myotis austroriparius" in p
    assert "no text" in p.lower() or "no lettering" in p.lower()
