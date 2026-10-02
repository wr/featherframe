"""The spend guards as the server and the webapp see them (W-938)."""
from __future__ import annotations

from featherframe.config import Config


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
