"""The spend guards as the server and the webapp see them (W-938)."""
from __future__ import annotations

from datetime import datetime

import pytest

from featherframe import spend
from featherframe.config import Config, save_config
from featherframe.db import Database


def test_the_missing_species_switch_takes_the_old_value():
    off = Config.from_dict({"imagegen_enabled": False})
    assert off.illustrations_generated is False and off.imagegen_enabled is False
    on = Config.from_dict({"imagegen_enabled": True})
    assert on.illustrations_generated is True and on.imagegen_enabled is True
    kept = Config.from_dict({"imagegen_enabled": True, "illustrations_generated": False})
    assert kept.illustrations_generated is False


def test_the_limit_is_whole_dollars_from_1_to_1000():
    assert Config().ai_monthly_limit_usd == 10
    assert Config(ai_monthly_limit_usd=0).sanitize().ai_monthly_limit_usd == 1
    assert Config(ai_monthly_limit_usd=5000).sanitize().ai_monthly_limit_usd == 1000
    assert Config(ai_monthly_limit_usd="12.7").sanitize().ai_monthly_limit_usd == 12
    assert Config(ai_monthly_limit_usd="x").sanitize().ai_monthly_limit_usd == 10


@pytest.fixture
def svc(tmp_path, monkeypatch):
    monkeypatch.setenv("FEATHERFRAME_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FEATHERFRAME_PLATES_DIR", str(tmp_path / "plates"))
    monkeypatch.setenv("FEATHERFRAME_DB", str(tmp_path / "ff.db"))
    from featherframe.service import FeatherframeService
    db = Database(tmp_path / "ff.db")
    save_config(db, Config(imagegen_api_key="sk-proj-verysecretkey1234",
                           imagegen_quality="medium"))
    s = FeatherframeService(db)
    s._clock = lambda: datetime(2026, 10, 14, 12, 0)
    return s


def test_the_service_buys_through_its_own_durable_gate(svc):
    assert svc.genart.gate is svc.spend_gate
    assert isinstance(svc.spend_gate.store, spend.LocalStore)


def test_the_summary_says_provider_and_spend(svc):
    v = svc.ai_view(svc._clock())
    assert v["state"] == "good" and v["summary"] == "OpenAI \u00b7 $0.00 of $10.00"


def test_off(svc):
    svc.config.imagegen_enabled = False
    assert svc.ai_view(svc._clock())["summary"] == "Off"
    assert svc.ai_refusal() == "off"


def test_limit_reached(svc):
    svc.config.ai_monthly_limit_usd = 1
    with svc.spend_gate.purchase("collage", "2026-10-14", model="m", auto=False) as p:
        p.settle(None, 0.99)
    v = svc.ai_view(svc._clock())
    assert v["summary"] == "Limit reached" and v["notice"] == "limit"
    assert v["text"] == ("This month's AI spend reached your $1.00 limit. New AI "
                         "illustrations and collages resume on 1 Nov, or raise the limit.")
    assert svc.ai_refusal() == "limit"
    assert svc._imagegen_glass_note() == "AI limit reached"


def test_paused_and_resumed(svc):
    svc.spend_gate.store.resume(0)
    svc.db.set("ai_pause", {"at": 1.0, "count": 6})
    v = svc.ai_view(svc._clock())
    assert v["summary"] == "Paused" and v["notice"] == "paused"
    assert v["text"] == ("AI generation is paused: 6 purchases in the last hour, more "
                         "than usual. Nothing more is bought until you resume.")
    assert svc._imagegen_glass_note() == "AI paused"
    svc.resume_ai()
    assert svc.ai_view(svc._clock())["notice"] is None


def test_the_missing_species_switch_reaches_the_provider(svc):
    svc.config.illustrations_generated = False
    save_config(svc.db, svc.config)
    svc.reload_config()
    assert svc.genart.buy_new is False


def test_status_carries_the_view(svc):
    assert svc.status()["ai"]["summary"] == "OpenAI \u00b7 $0.00 of $10.00"


def test_the_owner_flag_ends_with_the_tick_that_took_the_redraw(svc, monkeypatch):
    from datetime import date

    from featherframe.config import load_config
    from featherframe.pictures import COLLAGE
    # The owner saves a new branch while no collage has been drawn yet.
    saved = load_config(svc.db)
    saved.collage_branch = "bare"
    save_config(svc.db, saved)
    svc.reload_config()
    assert svc._collage_redraw and svc._collage_by_owner
    assert svc.pictures[COLLAGE].etag is None
    svc._tick_pictures()
    assert svc._collage_redraw is False
    assert svc._collage_by_owner is False

    class _Stop(Exception):
        pass

    seen = []

    def day_composite(*args, **kwargs):
        seen.append(kwargs)
        raise _Stop

    monkeypatch.setattr(svc.genart, "day_composite", day_composite)
    monkeypatch.setattr(svc.source, "top_species_today", lambda *a, **k: [
        {"common": "Barn Owl", "scientific": "Tyto alba", "count": 3},
        {"common": "Blue Jay", "scientific": "Cyanocitta cristata", "count": 4}])
    with pytest.raises(_Stop):
        svc._build_collage(svc._clock(), date(2026, 10, 14), nightly=True)
    assert seen and seen[0]["auto"] is True
