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
        self.prompts: list[str] = []
        self.sizes: list[str] = []

    def generate(self, prompt: str, size: str, refs) -> bytes:
        self.calls += 1
        self.prompts.append(prompt)
        self.sizes.append(size)
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


def test_cache_write_failure_sets_cooldown_and_never_raises(data_dir):
    """A paid generation whose cache write fails (full disk) must engage the
    cooldown — otherwise every detection re-bills — and regenerate must return
    False instead of raising."""
    model = FakeModel()
    provider = GeneratedArtProvider(model)
    gen_dir = data_dir / "generated"
    gen_dir.mkdir(parents=True, exist_ok=True)
    gen_dir.chmod(0o500)  # unwritable: every write attempt raises
    try:
        assert provider.artwork("House Sparrow", "Passer domesticus") is None
        assert model.calls == 1
        # Cooldown engaged: the next detection must not buy again.
        assert provider.artwork("House Sparrow", "Passer domesticus") is None
        assert model.calls == 1
        assert provider.regenerate("House Sparrow", "Passer domesticus") is False
    finally:
        gen_dir.chmod(0o700)


def test_cached_species_skips_foreign_and_malformed_sidecars(data_dir):
    provider = GeneratedArtProvider(FakeModel())
    provider.artwork("House Sparrow", "Passer domesticus")
    gen_dir = data_dir / "generated"
    (gen_dir / "stray-export.json").write_text("[]")          # valid JSON, not a dict
    (gen_dir / "sturnus-vulgaris.json").write_text(json.dumps(
        {"slug": "sturnus-vulgaris", "common": "European Starling",
         "scientific": "Sturnus vulgaris", "created_at": None}))
    (gen_dir / "sturnus-vulgaris.png").write_bytes(_plate_png())
    listed = provider.cached_species()  # must not raise
    assert {e["slug"] for e in listed} == {"passer-domesticus", "sturnus-vulgaris"}


def test_pick_reference_plates_survives_malformed_index(data_dir, monkeypatch, tmp_path):
    from featherframe import paths as ffpaths
    from featherframe.render.genart import pick_reference_plates
    bad = tmp_path / "index.json"
    for payload in ("[]", '{"species": null}'):
        bad.write_text(payload)
        monkeypatch.setattr(ffpaths, "plate_index_path", lambda: bad)
        assert pick_reference_plates("House Sparrow") == []


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
def test_prompt_never_commands_imperfections():
    # The always-on imperfection clause came back as tattered leaves on every
    # sheet; damage may only arrive via the rare weathered direction bucket.
    p = build_prompt("House Sparrow", "Passer domesticus")
    assert "imperfection" not in p.lower()
    assert "damage" not in p.lower()


def test_direction_sampling_weighted_toward_restraint():
    import random as _random
    from featherframe.render.genart import _sample_direction
    picks = [_sample_direction(_random.Random(i))[1] for i in range(300)]
    weathered = sum(1 for p in picks if "weathering" in p["condition"])
    fresh = sum(1 for p in picks if "fresh and whole" in p["condition"])
    assert weathered < 40      # the ~5% bucket stays the exception
    assert fresh > 150         # restraint dominates


def test_direction_lands_in_prompt_only_when_drawn():
    import random as _random
    from featherframe.render.genart import _sample_direction
    direction, _ = _sample_direction(_random.Random(1))
    with_dir = build_prompt("House Sparrow", "Passer domesticus", direction)
    without = build_prompt("House Sparrow", "Passer domesticus")
    assert direction in with_dir
    assert "Art direction for this sheet" in with_dir
    assert "Art direction for this sheet" not in without


def test_prompt_pins_subject_identity():
    p = build_prompt("Greater Anglewing", "Microcentrum rhombifolium")
    assert "never translated into a" in p


def test_prompt_mentions_species_and_bans_text():
    p = build_prompt("Southeastern myotis", "Myotis austroriparius")
    assert "Southeastern myotis" in p
    assert "Myotis austroriparius" in p
    assert "no text" in p.lower() or "no lettering" in p.lower()


# -- the naturalist's brief -------------------------------------------------
class FakeTextModel:
    name = "fake-text"

    def __init__(self, is_bird=False, description="A large leaf-green katydid.",
                 plants=None, fail=False):
        self.is_bird = is_bird
        self.description = description
        self.plants = plants if plants is not None else [
            {"name": "American elm", "look": "serrate ovate leaves on arching twigs"},
            {"name": "wild plum", "look": "white five-petaled blossom clusters"},
        ]
        self.fail = fail
        self.calls = 0

    def complete_json(self, prompt):
        self.calls += 1
        if self.fail:
            raise RuntimeError("no brief today")
        return {"is_bird": self.is_bird, "description": self.description,
                "plants": self.plants}


def test_brief_lands_in_prompt_and_sidecar(tmp_path):
    model = FakeModel()
    text = FakeTextModel()
    provider = GeneratedArtProvider(model, cache_dir=tmp_path / "generated",
                                    refs=[], text_model=text)
    assert provider.artwork("Greater Anglewing", "Microcentrum rhombifolium")
    assert "leaf-green katydid" in model.prompts[-1]
    import json as _json
    meta = _json.loads((tmp_path / "generated" /
                        "microcentrum-rhombifolium.json").read_text())
    assert "katydid" in meta["description"]
    # One plant from the pool was drawn, named in the prompt, and recorded.
    assert meta["plant"]["name"] in ("American elm", "wild plum")
    assert meta["plant"]["name"] in model.prompts[-1]


def test_empty_plant_pool_is_respected_not_rebought(tmp_path):
    text = FakeTextModel(plants=[])
    provider = GeneratedArtProvider(FakeModel(), cache_dir=tmp_path / "generated",
                                    refs=[], text_model=text)
    provider._describe("Chimney Swift", "Chaetura pelagica")
    provider._describe("Chimney Swift", "Chaetura pelagica")
    assert text.calls == 1


def test_brief_is_bought_once_and_cached(tmp_path):
    text = FakeTextModel()
    provider = GeneratedArtProvider(FakeModel(), cache_dir=tmp_path / "generated",
                                    refs=[], text_model=text)
    provider._describe("Greater Anglewing", "Microcentrum rhombifolium")
    provider._describe("Greater Anglewing", "Microcentrum rhombifolium")
    assert text.calls == 1
    # The cache file lives beside the plate cache, never inside it.
    assert (tmp_path / "descriptions.json").exists()
    assert not (tmp_path / "generated" / "descriptions.json").exists()


def test_brief_failure_never_blocks_a_plate(tmp_path):
    provider = GeneratedArtProvider(FakeModel(), cache_dir=tmp_path / "generated",
                                    refs=[], text_model=FakeTextModel(fail=True))
    assert provider.artwork("House Sparrow", "Passer domesticus")


def test_nonbird_tableau_never_offers_nests():
    import random as _random
    from featherframe.render.genart import _sample_direction
    for i in range(300):
        _, picks = _sample_direction(_random.Random(i), is_bird=False)
        assert "own nest" not in picks["tableau"]
        assert "juvenile" not in picks["tableau"]


def test_lichen_is_not_commanded():
    p = build_prompt("Brown Creeper", "Certhia americana")
    assert "lichen" not in p.lower()


# -- regeneration variety ---------------------------------------------------
def test_forced_regenerate_avoids_the_previous_draw(tmp_path):
    model = FakeModel()
    provider = GeneratedArtProvider(model, cache_dir=tmp_path / "generated",
                                    refs=[], text_model=FakeTextModel())
    assert provider.artwork("Greater Anglewing", "Microcentrum rhombifolium")
    import json as _json
    sidecar = tmp_path / "generated" / "microcentrum-rhombifolium.json"
    first = _json.loads(sidecar.read_text())
    # Several forced repaints: none may repeat the previous plant or the
    # previous tableau/foliage/figures/armature sentences.
    prev = first
    for _ in range(6):
        assert provider.regenerate("Greater Anglewing", "Microcentrum rhombifolium")
        cur = _json.loads(sidecar.read_text())
        assert cur["plant"]["name"] != prev["plant"]["name"]
        for axis in ("tableau", "foliage", "figures", "armature"):
            assert cur["art_direction"][axis] != prev["art_direction"][axis]
        prev = cur


def test_juvenile_tableau_never_draws_a_single_figure():
    import random as _random
    from featherframe.render.genart import _sample_direction
    for i in range(500):
        _, picks = _sample_direction(_random.Random(i))
        if "juvenile" in picks["tableau"]:
            assert "alone" not in picks["figures"]


def test_ref_shuffle_varies_and_default_stays_deterministic(tmp_path, monkeypatch):
    import random as _random
    from featherframe.render.genart import pick_reference_plates
    from featherframe import paths as _paths
    idx_dir = tmp_path / "plates"
    img_dir = idx_dir / "img"
    img_dir.mkdir(parents=True)
    species = []
    for i in range(8):
        (img_dir / f"p{i}.jpg").write_bytes(b"x")
        species.append({"scientific": f"Sci {i}", "image": f"p{i}.jpg"})
    import json as _json
    (idx_dir / "index.json").write_text(_json.dumps(
        {"images_dir": str(img_dir), "species": species}))
    monkeypatch.setattr(_paths, "plate_index_path", lambda: idx_dir / "index.json")
    a = pick_reference_plates()
    b = pick_reference_plates()
    assert a == b                                    # no rng: stable anchor
    seen = {tuple(pick_reference_plates(rng=_random.Random(i)) or [])
            for i in range(12)}
    assert len(seen) > 1                             # rng: the fill varies


# -- review fixes (PR #17 opus panel) ---------------------------------------
def test_braces_in_model_text_are_literal():
    p = build_prompt("Big Brown Bat", "Eptesicus fuscus",
                     description="Forearm {38-50 mm}; a stocky vespertilionid.",
                     plant={"name": "Ilex", "look": "berries {scarlet} in autumn"})
    assert "{38-50 mm}" in p and "{scarlet}" in p


def test_failed_regenerate_restores_the_old_sidecar(tmp_path, monkeypatch):
    provider = GeneratedArtProvider(FakeModel(), cache_dir=tmp_path / "generated",
                                    refs=[], text_model=FakeTextModel())
    assert provider.artwork("House Sparrow", "Passer domesticus")
    sidecar = tmp_path / "generated" / "passer-domesticus.json"
    before = sidecar.read_bytes()
    calls = {"n": 0}
    real_write = GeneratedArtProvider._write_atomic

    def failing_write(path, data):
        calls["n"] += 1
        if str(path).endswith(".png") and calls["n"] > 0:
            raise OSError("disk full")
        return real_write(path, data)

    monkeypatch.setattr(GeneratedArtProvider, "_write_atomic",
                        staticmethod(failing_write))
    assert not provider.regenerate("House Sparrow", "Passer domesticus")
    # The surviving plate keeps a matching sidecar; nothing is orphaned.
    assert sidecar.read_bytes() == before
    assert (tmp_path / "generated" / "passer-domesticus.png").exists()


def test_brief_model_change_rebuilds_provider(tmp_path, monkeypatch):
    from dataclasses import replace
    from featherframe.service import FeatherframeService
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    svc = FeatherframeService()
    cfg = replace(svc.config, imagegen_enabled=True, imagegen_api_key="k",
                  imagegen_text_model="gpt-5.6-luna")
    svc.update_config(cfg)
    assert svc.genart._text_model.model == "gpt-5.6-luna"
    svc.update_config(replace(cfg, imagegen_text_model="gpt-5.6-sol"))
    assert svc.genart._text_model.model == "gpt-5.6-sol"
